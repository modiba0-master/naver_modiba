"""조회 조건이 반영된 DataFrame만으로 KPI 집계·구간 분할."""

from __future__ import annotations

import pandas as pd


def delta_rate(current: float, previous: float) -> float:
    rate = ((current - previous) / previous * 100) if previous != 0 else 0
    return float(rate)


def kpi_aggregate(df: pd.DataFrame) -> dict[str, float]:
    """
    KPI 집계 (조회 필터 반영된 df만 사용).

    - total_amount: `net_revenue` 합계(있으면), 없으면 `amount` 합계
    - order_count: order_id 서로 다른 값 개수 (컬럼 없으면 행 수)
    - total_quantity: quantity 합계
    - customer_count: customer_id nunique
    - avg_order_value: total_amount / order_count (order_count=0이면 0)
    """
    if df.empty:
        return {
            "total_amount": 0.0,
            "order_count": 0.0,
            "total_quantity": 0.0,
            "customer_count": 0.0,
            "avg_order_value": 0.0,
        }
    rev_col = "net_revenue" if "net_revenue" in df.columns else "amount"
    total_amount = float(pd.to_numeric(df[rev_col], errors="coerce").fillna(0).sum())
    total_quantity = float(df["quantity"].sum())

    if "order_id" in df.columns:
        oid = df["order_id"].astype(str).replace("", pd.NA).dropna()
        if len(oid) == 0:
            order_count = float(len(df))
        else:
            order_count = float(oid.nunique())
    else:
        order_count = float(len(df))

    customer_count = float(df["customer_id"].nunique())
    avg_order_value = total_amount / order_count if order_count > 0 else 0.0

    return {
        "total_amount": total_amount,
        "order_count": order_count,
        "total_quantity": total_quantity,
        "customer_count": customer_count,
        "avg_order_value": avg_order_value,
    }


def split_filtered_date_halves(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(df["date"].dt.date.dropna().unique())
    if len(dates) <= 1:
        return df.copy(), df.iloc[:0].copy()
    mid = len(dates) // 2
    first_dates = set(dates[:mid])
    second_dates = set(dates[mid:])
    first_df = df[df["date"].dt.date.isin(first_dates)].copy()
    second_df = df[df["date"].dt.date.isin(second_dates)].copy()
    return first_df, second_df


def daily_avg_sales(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    rev_col = "net_revenue" if "net_revenue" in df.columns else "amount"
    n = max(1, int(df["date"].dt.date.nunique()))
    return float(pd.to_numeric(df[rev_col], errors="coerce").fillna(0).sum()) / n


def expected_sales_from_recent_7d(df: pd.DataFrame) -> float:
    """
    최근 7일 일매출 평균 기반 예상매출.

    daily_sales = df.groupby("date")["amount"].sum()
    expected_sales = daily_sales.tail(7).mean()
    """
    if df.empty:
        return 0.0
    rev_col = "net_revenue" if "net_revenue" in df.columns else "amount"
    daily_sales = df.groupby(df["date"].dt.date)[rev_col].sum()
    if daily_sales.empty:
        return 0.0
    return float(daily_sales.tail(7).mean())
