from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from streamlit_app.services.data_grid import show_data_grid
from streamlit_app.services.kpi_from_filtered import (
    delta_rate,
    expected_sales_from_recent_7d,
    kpi_aggregate,
)
from streamlit_app.services.kpi_ui import add_avg_ticket_to_daily, append_daily_total_row

DEFAULT_API_BASE_URL = "https://web-production-0001b.up.railway.app"
REQUIRED_COLUMNS = [
    "order_id",
    "date",
    "payment_date",
    "buyer_name",
    "buyer_id",
    "receiver_name",
    "address",
    "product_name",
    "option_name",
    "quantity",
    "amount",
]
KST = ZoneInfo("Asia/Seoul")

_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


def _format_sales_date_label(d: date) -> str:
    return f"{d.isoformat()} ({_WEEKDAY_KO[d.weekday()]})"


def _aggregate_kpi_daily(kpi_daily_table: pd.DataFrame) -> pd.DataFrame:
    if kpi_daily_table.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "total_amount",
                "order_count",
                "total_quantity",
            ]
        )
    df = kpi_daily_table.copy()
    if "net_revenue" in df.columns:
        df["_amount"] = pd.to_numeric(df["net_revenue"], errors="coerce").fillna(0.0)
    else:
        df["_amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    out_rows: list[dict] = []
    for d, g in df.groupby("date"):
        a = g["_amount"]
        total = float(a.sum())
        oid = int(g["order_id"].astype(str).replace("", pd.NA).dropna().nunique())
        tq = float(pd.to_numeric(g["quantity"], errors="coerce").fillna(0).sum())
        out_rows.append(
            {
                "date": d,
                "total_amount": total,
                "order_count": oid,
                "total_quantity": tq,
            }
        )
    return pd.DataFrame(out_rows)


def format_now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def extract_multiplier(option_name: str) -> int:
    if not isinstance(option_name, str) or not option_name.strip():
        return 1
    patterns = [r"x\s*(\d+)", r"(\d+)\s*개", r"(\d+)\s*팩"]
    for pattern in patterns:
        match = re.search(pattern, option_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 1


def extract_weight_unit(option_name: str) -> str:
    if not isinstance(option_name, str) or not option_name.strip():
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g)", option_name, re.IGNORECASE)
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2).lower()}"


@st.cache_data(ttl=60)
def fetch_order_data(base_url: str, revenue_basis: str = "payment") -> pd.DataFrame:
    url = base_url.rstrip("/")
    response = httpx.get(
        f"{url}/analytics/orders-raw",
        params={"revenue_basis": revenue_basis},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", [])
    return pd.DataFrame(items)


def _normalize_api_column_name(name: object) -> str:
    text = str(name).strip()
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", text)
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text


def _normalize_api_columns(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df.columns = [_normalize_api_column_name(col) for col in df.columns]

    alias_map = {
        "orderer_name": "buyer_name",
        "orderer_id": "buyer_id",
        "shipping_address": "address",
        "receiver_address": "address",
    }
    for src, dst in alias_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    if "date" not in df.columns and "business_date" in df.columns:
        df = df.rename(columns={"business_date": "date"})
    return df


def normalize_order_data(frame: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_api_columns(frame)
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["payment_date"] = pd.to_datetime(df["payment_date"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    if "net_revenue" not in df.columns:
        df["net_revenue"] = df["amount"]
    else:
        df["net_revenue"] = pd.to_numeric(df["net_revenue"], errors="coerce").fillna(0)
    df["multiplier"] = df["option_name"].apply(extract_multiplier)
    df["real_quantity"] = df["quantity"] * df["multiplier"]
    df["weight_unit"] = df["option_name"].apply(extract_weight_unit)
    df["pack_count"] = df["multiplier"]
    df["converted_quantity"] = df["quantity"] * df["pack_count"]
    df["short_address"] = df["address"].astype(str).str.slice(0, 20)
    if "customer_id" not in df.columns:
        df["customer_id"] = df["buyer_id"]
    df = df.dropna(subset=["date"]).copy()
    return df


st.set_page_config(page_title="네이버 모디바 대시보드", layout="wide")


def _require_login() -> bool:
    if st.session_state.get("authenticated", False):
        return True

    st.title("🔐 보안 접속")
    password = st.text_input("비밀번호를 입력하세요", type="password")
    if st.button("로그인", type="primary"):
        actual_password = None
        try:
            actual_password = st.secrets.get("PASSWORD")
        except Exception:
            pass

        if not actual_password:
            actual_password = os.environ.get("PASSWORD")

        if password == actual_password and actual_password is not None:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")

    st.stop()
    return False


def main_content() -> None:
    st_autorefresh(interval=60000, key="naver_modiba_dashboard_autorefresh")

    header_left, header_right = st.columns([5, 1])
    with header_left:
        st.title("네이버 커머스 주문 분석 대시보드")
    with header_right:
        st.markdown("")
        if "last_click" not in st.session_state:
            st.session_state.last_click = 0.0
        if st.button("새로고침"):
            now = time.time()
            if now - st.session_state.last_click >= 5:
                st.session_state.last_click = now
                st.rerun()
        if st.button("강제 새로고침"):
            st.cache_data.clear()
            for _k in (
                "last_good_order_df",
                "kpi_start_date",
                "kpi_end_date",
                "analysis_start_date",
                "analysis_end_date",
            ):
                st.session_state.pop(_k, None)
            st.rerun()

    with st.sidebar:
        st.header("API")
        api_base_url = st.text_input("API URL", value=DEFAULT_API_BASE_URL).rstrip("/")
        revenue_basis = st.selectbox(
            "집계 기준",
            options=["payment", "order", "shipping"],
            index=0,
            format_func=lambda x: {
                "payment": "결제",
                "order": "주문",
                "shipping": "발송",
            }[x],
            key="revenue_basis_select_root",
        )

    try:
        raw_df = fetch_order_data(api_base_url, revenue_basis)
        order_df = normalize_order_data(raw_df)
        st.session_state["last_good_order_df"] = order_df.copy()
        data_loaded_at = format_now_kst()
        st.session_state["last_data_loaded_at"] = data_loaded_at
        st.session_state["last_data_loaded_mode"] = "live"
    except Exception:
        cached_df = st.session_state.get("last_good_order_df")
        if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
            order_df = cached_df.copy()
        else:
            st.stop()

    if order_df.empty:
        st.stop()

    default_end = datetime.now(KST).date()
    default_start = default_end - timedelta(days=6)

    st.markdown("## KPI")
    kpi_col1, kpi_col2 = st.columns(2)
    with kpi_col1:
        kpi_start_date = st.date_input(
            "시작",
            value=default_start,
            key="kpi_start_date",
        )
    with kpi_col2:
        kpi_end_date = st.date_input(
            "종료",
            value=default_end,
            key="kpi_end_date",
        )

    if kpi_start_date > kpi_end_date:
        st.stop()

    kpi_mask = (
        (order_df["date"].dt.date >= kpi_start_date)
        & (order_df["date"].dt.date <= kpi_end_date)
    )
    kpi_filtered_df = order_df[kpi_mask].copy()
    if kpi_filtered_df.empty:
        st.stop()

    period_days = (kpi_end_date - kpi_start_date).days + 1
    prev_start = kpi_start_date - timedelta(days=period_days)
    prev_end = kpi_end_date - timedelta(days=period_days)
    prev_mask = (
        (order_df["date"].dt.date >= prev_start)
        & (order_df["date"].dt.date <= prev_end)
    )
    prev_df = order_df[prev_mask].copy()

    compare_prev = not prev_df.empty
    whole = kpi_aggregate(kpi_filtered_df)
    prev_m = kpi_aggregate(prev_df)
    expected_sales = expected_sales_from_recent_7d(kpi_filtered_df)
    prev_expected_sales = expected_sales_from_recent_7d(prev_df)

    def _prev_delta(curr: float, base: float) -> str | None:
        if not compare_prev:
            return None
        return f"{delta_rate(curr, base):.1f}%"

    compare_info = f"vs {prev_start}~{prev_end}"

    with st.container(border=True):
        r1a, r1b, r1c, r1d = st.columns(4)
        r1a.metric(
            f"순매출 ({compare_info})",
            f"{whole['total_amount']:,.0f}원",
            _prev_delta(whole["total_amount"], prev_m["total_amount"]),
        )
        r1b.metric(
            f"상품주문 ({compare_info})",
            f"{int(whole['order_count']):,}",
            _prev_delta(whole["order_count"], prev_m["order_count"]),
        )
        r1c.metric(
            f"판매 수량 합계 ({compare_info})",
            f"{whole['total_quantity']:,.0f}",
            _prev_delta(whole["total_quantity"], prev_m["total_quantity"]),
        )
        r1d.metric(
            f"고객 수 ({compare_info})",
            f"{int(whole['customer_count']):,}",
            _prev_delta(whole["customer_count"], prev_m["customer_count"]),
        )
        r2a, r2b, r2c = st.columns(3)
        r2a.metric(
            f"객단가 ({compare_info})",
            f"{whole['avg_order_value']:,.0f}원",
            _prev_delta(whole["avg_order_value"], prev_m["avg_order_value"]),
        )
        r2b.metric(
            f"최근7일 평균 일매출 ({compare_info})",
            f"{expected_sales:,.0f}원",
            _prev_delta(expected_sales, prev_expected_sales),
        )
        r2c.metric(
            "일수",
            f"{period_days}일",
            None,
        )

    st.markdown("")
    st.subheader("일자별")
    kpi_daily_start = default_end - timedelta(days=6)
    kpi_daily_mask = (
        (order_df["date"].dt.date >= kpi_daily_start)
        & (order_df["date"].dt.date <= default_end)
    )
    kpi_daily_table = order_df[kpi_daily_mask].copy()
    kpi_daily_table["date"] = kpi_daily_table["date"].dt.date
    daily_kpi = _aggregate_kpi_daily(kpi_daily_table)
    cal = pd.DataFrame(
        {"date": pd.date_range(kpi_daily_start, default_end, freq="D").date}
    )
    daily_kpi = cal.merge(daily_kpi, on="date", how="left")
    for col in ("total_amount", "total_quantity"):
        daily_kpi[col] = pd.to_numeric(daily_kpi[col], errors="coerce").fillna(0.0)
    daily_kpi["order_count"] = daily_kpi["order_count"].fillna(0).astype(int)
    daily_kpi = add_avg_ticket_to_daily(daily_kpi)
    daily_kpi["date_label"] = daily_kpi["date"].map(_format_sales_date_label)
    daily_kpi = daily_kpi.sort_values("date", ascending=False)

    daily_kpi = daily_kpi[
        [
            "date_label",
            "total_amount",
            "avg_ticket",
            "order_count",
            "total_quantity",
        ]
    ]
    daily_kpi = append_daily_total_row(daily_kpi)

    show_data_grid(daily_kpi)

    st.markdown("---")
    st.markdown("## 분석")
    ana_col1, ana_col2 = st.columns(2)
    with ana_col1:
        analysis_start_date = st.date_input(
            "시작",
            value=default_start,
            key="analysis_start_date",
        )
    with ana_col2:
        analysis_end_date = st.date_input(
            "종료",
            value=default_end,
            key="analysis_end_date",
        )
    buyer_name_search = st.text_input("구매자", "", key="main_buyer_search")

    if analysis_start_date > analysis_end_date:
        st.stop()

    analysis_mask = (
        (order_df["date"].dt.date >= analysis_start_date)
        & (order_df["date"].dt.date <= analysis_end_date)
    )
    analysis_filtered_df = order_df[analysis_mask].copy()
    if buyer_name_search:
        analysis_filtered_df = analysis_filtered_df[
            analysis_filtered_df["buyer_name"].astype(str).str.contains(
                buyer_name_search, case=False, na=False
            )
        ]
    if analysis_filtered_df.empty:
        st.stop()

    _rev_col = (
        "net_revenue" if "net_revenue" in analysis_filtered_df.columns else "amount"
    )

    tab_product_sales, tab_option_sales = st.tabs(["상품", "옵션"])

    with tab_product_sales:
        product_summary = (
            analysis_filtered_df.groupby("product_name", as_index=False)
            .agg(
                total_amount=(_rev_col, "sum"),
                quantity=("quantity", "sum"),
                real_quantity=("real_quantity", "sum"),
                order_count=(
                    "order_id",
                    lambda s: s.astype(str).replace("", pd.NA).dropna().nunique(),
                ),
            )
            .sort_values("total_amount", ascending=False)
        )
        show_data_grid(product_summary)

    with tab_option_sales:
        option_name_summary = (
            analysis_filtered_df.groupby("option_name", as_index=False)
            .agg(
                weight_unit=(
                    "weight_unit",
                    lambda s: next((x for x in s if str(x).strip()), ""),
                ),
                total_amount=(_rev_col, "sum"),
                quantity=("quantity", "sum"),
                real_quantity=("real_quantity", "sum"),
                converted_quantity=("converted_quantity", "sum"),
                pack_count_sum=("pack_count", "sum"),
                order_count=(
                    "order_id",
                    lambda s: s.astype(str).replace("", pd.NA).dropna().nunique(),
                ),
            )
            .sort_values("total_amount", ascending=False)
        )
        show_data_grid(option_name_summary)

    show_data_grid(analysis_filtered_df)


if _require_login():
    main_content()
