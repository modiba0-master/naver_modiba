from __future__ import annotations

import re
import os
import sys
import time
import hmac
from typing import Any
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st
from sqlalchemy import text

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover - optional component in deploy
    st_autorefresh = None

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.data_grid import show_data_grid
from services.db import SessionLocal
from services.kpi_from_filtered import (
    delta_rate,
    expected_sales_from_recent_7d,
    kpi_aggregate,
)
from services.kpi_ui import add_avg_ticket_to_daily, append_daily_total_row
from ui_theme import (
    apply_dashboard_theme,
    render_page_title,
    section_heading,
)

# HTTP로 주문 목록·DB 통계를 가져온다. 잘못된 URL이면 "어제와 같은 결과"처럼 보인다(다른/옛 백엔드 DB).
# Railway: Streamlit 서비스에 `ANALYTICS_API_BASE_URL`로 실제 FastAPI(동기화+MariaDB) URL을 맞출 것.
_PRODUCTION_API = "https://web-production-0001b.up.railway.app"
DEFAULT_API_BASE_URL = (os.environ.get("ANALYTICS_API_BASE_URL") or _PRODUCTION_API).strip().rstrip(
    "/"
)
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
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)
HTTP_RETRY_ATTEMPTS = 3
HTTP_RETRY_BACKOFF_SECONDS = 0.8
# `/analytics/orders-raw` 조회 상한(영업일). 30일 중기·전주 비교에 맞추되 응답 크기를 최소화한다.
ORDERS_RAW_FETCH_DAYS = 45
# 경영요약 상승/하락 표: 동일 높이로 PC에서 하단 정렬이 맞도록 Glide 표 높이(px).
SUMMARY_RISE_FALL_DATAFRAME_HEIGHT_PX = 320

_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


def _kst_anchor_business_date() -> date:
    """대시보드 KPI 기준일과 동일: 16시 이후면 익일 영업일 달력."""
    now_kst = datetime.now(KST)
    return now_kst.date() + timedelta(days=1) if now_kst.hour >= 16 else now_kst.date()


_OPTION_PRODUCT_JOIN_SEP = " — "


def _option_product_label(product_name: object, option_name: object) -> str:
    """집계 키: 상품명(+ 구분자 + 옵션명). 옵션 없으면 상품명만."""
    pn = str(product_name or "").strip()
    on = str(option_name or "").strip()
    if not on or on.lower() == "nan":
        return pn
    return f"{pn}{_OPTION_PRODUCT_JOIN_SEP}{on}"


def _option_grid_display_text(key_label: object) -> str:
    """그리드 표시: 옵션 있으면 옵션명만, 없으면 `상품명 (통합)`."""
    s = str(key_label or "").strip()
    if not s:
        return ""
    if _OPTION_PRODUCT_JOIN_SEP not in s:
        return f"{s} (통합)"
    left, _sep, right = s.partition(_OPTION_PRODUCT_JOIN_SEP)
    left, right = left.strip(), right.strip()
    if right and right.lower() != "nan":
        return right
    return f"{left} (통합)" if left else ""


def _format_sales_date_label(d: date) -> str:
    """매출 집계일(달력) + 요일 — KPI 일자 행에 통일 표기."""
    return f"{d.isoformat()} ({_WEEKDAY_KO[d.weekday()]})"


def _format_sales_date_compact(value: object) -> str:
    """상세 원장용 매출 집계일 표기: M/D (요일)."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""
    d = ts.date()
    return f"{d.month}/{d.day} ({_WEEKDAY_KO[d.weekday()]})"


def _aggregate_kpi_daily(kpi_daily_table: pd.DataFrame) -> pd.DataFrame:
    """매출 집계일별 주문금액·건수·수량 합계 (`date` = 저장된 `business_date`)."""
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


def format_krw(value: float | int) -> str:
    return f"{float(value):,.0f}원"


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


def _is_icepack_size_16x23(product_name: object) -> bool:
    """아이스팩 16x23 규격 상품은 수량 배수 보정 대상."""
    text = str(product_name or "")
    return ("아이스팩" in text) and bool(re.search(r"16\s*[xX]\s*23", text))


def product_group(product_name: str) -> str:
    text = product_name if isinstance(product_name, str) else ""
    if "닭가슴살" in text:
        return "닭가슴살"
    if "닭안심" in text:
        return "닭안심"
    return "기타"


def _http_get_json_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """일시 장애(타임아웃/네트워크/429/5xx)에 대해 짧은 재시도."""
    last_error: Exception | None = None
    for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
        try:
            response = httpx.get(url, params=params, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            retryable = status == 429 or (status is not None and status >= 500)
            if not retryable:
                raise
            last_error = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc

        if attempt < HTTP_RETRY_ATTEMPTS:
            time.sleep(HTTP_RETRY_BACKOFF_SECONDS * attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError("HTTP request failed without captured exception")


@st.cache_data(ttl=60)
def fetch_order_data(
    base_url: str,
    revenue_basis: str = "payment",
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    url = base_url.rstrip("/")
    params: dict[str, Any] = {"revenue_basis": revenue_basis}
    if start_date is not None and end_date is not None:
        params["start_date"] = datetime.combine(start_date, time.min, tzinfo=KST).isoformat()
        params["end_date"] = datetime.combine(end_date, time(23, 59, 59), tzinfo=KST).isoformat()
    payload = _http_get_json_with_retry(f"{url}/analytics/orders-raw", params=params)
    items = payload.get("items", [])
    return pd.DataFrame(items)


@st.cache_data(ttl=15)
def fetch_db_stats(base_url: str) -> dict[str, object]:
    """`/analytics/db-stats` — 원장 건수·최신 결제일시(실시간 DB 확인용)."""
    url = base_url.rstrip("/")
    return _http_get_json_with_retry(f"{url}/analytics/db-stats")


def _mark_api_success() -> None:
    st.session_state["api_last_success_at"] = format_now_kst()
    st.session_state["api_consecutive_failures"] = 0
    st.session_state["api_last_error"] = ""


def _mark_api_failure(exc: Exception) -> None:
    st.session_state["api_consecutive_failures"] = int(
        st.session_state.get("api_consecutive_failures", 0)
    ) + 1
    st.session_state["api_last_error"] = str(exc)[:180]


def _render_api_health_caption() -> None:
    last_ok = st.session_state.get("api_last_success_at", "—")
    fail_count = int(st.session_state.get("api_consecutive_failures", 0))
    last_error = st.session_state.get("api_last_error", "")
    st.write(f"API 최근 성공: {last_ok} · 연속 실패: {fail_count}회")
    if fail_count > 0 and last_error:
        st.write(f"최근 오류: {last_error}")


def _safe_autorefresh(interval_ms: int, key: str) -> None:
    """컴포넌트 로딩 실패 시에도 앱이 중단되지 않게 보호."""
    if st_autorefresh is None:
        return
    try:
        st_autorefresh(interval=interval_ms, key=key)
    except Exception:
        # 배포 네트워크/프록시 이슈로 component asset 로드가 실패할 수 있다.
        # 자동 새로고침만 비활성화하고 나머지 대시보드는 정상 동작시킨다.
        if not st.session_state.get("_autorefresh_component_warned", False):
            st.warning("자동 새로고침 컴포넌트 로딩에 실패해 자동 새로고침을 비활성화했습니다.")
            st.session_state["_autorefresh_component_warned"] = True


def _normalize_api_column_name(name: object) -> str:
    """API 응답 컬럼명을 내부 표준 snake_case로 정규화."""
    text = str(name).strip()
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", text)
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text


def _normalize_api_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """네이버/백엔드 응답 컬럼 alias를 내부 분석 컬럼으로 통일."""
    df = frame.copy()
    df.columns = [_normalize_api_column_name(col) for col in df.columns]

    alias_map = {
        "orderer_name": "buyer_name",
        "orderer_id": "buyer_id",
        "shipping_address": "address",
        "receiver_address": "address",
        "expectedsettlementamount": "expected_settlement_amount",
    }
    for src, dst in alias_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    if "date" not in df.columns and "business_date" in df.columns:
        df = df.rename(columns={"business_date": "date"})
    return df


@st.cache_data(ttl=60)
def get_today_summary(today: date) -> dict[str, int]:
    """
    orders 테이블 기준 오늘 날짜 주문 건수·매출 합계.
    스키마상 금액 컬럼은 `amount`이므로 SUM(amount)로 집계한다.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS order_count,
                    COALESCE(SUM(net_revenue), 0) AS total_sales
                FROM orders
                WHERE business_date = :today
                """
            ),
            {"today": today},
        ).one()
        return {
            "order_count": int(row.order_count or 0),
            "total_sales": int(row.total_sales or 0),
        }
    finally:
        db.close()


@st.cache_data(ttl=300)
def get_daily_summary(selected_date: date) -> dict[str, int]:
    """
    daily_summary 테이블에서 선택일의 주문·매출 합계.
    테이블은 (date, product_id, option_id) 단위 행이므로 SUM으로 일 합계를 낸다.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(orders), 0) AS total_orders,
                    COALESCE(SUM(revenue), 0) AS total_sales
                FROM daily_summary
                WHERE date = :date
                """
            ),
            {"date": selected_date},
        ).one()
        return {
            "total_orders": int(row.total_orders or 0),
            "total_sales": int(row.total_sales or 0),
        }
    finally:
        db.close()


def normalize_order_data(frame: pd.DataFrame) -> pd.DataFrame:
    # 로드 직후 컬럼명을 통일해 이후 집계/표시에 동일 스키마를 사용한다.
    df = _normalize_api_columns(frame)
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    # 결제일시: 원본(payment_date). 매출 집계일 `date`는 저장된 business_date(16시 규칙).
    df["payment_date"] = pd.to_datetime(df["payment_date"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    if "expected_settlement_amount" not in df.columns:
        df["expected_settlement_amount"] = 0
    df["expected_settlement_amount"] = pd.to_numeric(
        df["expected_settlement_amount"], errors="coerce"
    ).fillna(0)
    if "net_revenue" not in df.columns:
        df["net_revenue"] = df["amount"]
    else:
        df["net_revenue"] = pd.to_numeric(df["net_revenue"], errors="coerce").fillna(0)
    df["multiplier"] = df["option_name"].apply(extract_multiplier)
    # 아이스팩 16x23 규격명은 "x23"이 수량 배수가 아니라 사이즈 표기이므로 1개로 취급.
    icepack_mask = df["product_name"].apply(_is_icepack_size_16x23)
    if icepack_mask.any():
        df.loc[icepack_mask, "multiplier"] = 1
    df["real_quantity"] = df["quantity"] * df["multiplier"]
    df["weight_unit"] = df["option_name"].apply(extract_weight_unit)
    df["pack_count"] = df["multiplier"]
    df["converted_quantity"] = df["quantity"] * df["pack_count"]
    df["short_address"] = df["address"].astype(str).str.slice(0, 20)
    if "customer_id" not in df.columns:
        df["customer_id"] = df["buyer_id"]
    df["option_product_label"] = [
        _option_product_label(a, b) for a, b in zip(df["product_name"], df["option_name"])
    ]
    df = df.dropna(subset=["date"]).copy()
    return df


def _prepare_analysis_summary(
    frame: pd.DataFrame,
    *,
    group_key: str,
    revenue_column: str,
) -> pd.DataFrame:
    grouped = (
        frame.groupby(group_key, as_index=False)
        .agg(
            total_amount=(revenue_column, "sum"),
            order_quantity=("quantity", "sum"),
            sold_quantity=("real_quantity", "sum"),
            order_count=(
                "order_id",
                lambda s: s.astype(str).replace("", pd.NA).dropna().nunique(),
            ),
        )
        .sort_values("total_amount", ascending=False)
    )
    total_amount_sum = float(grouped["total_amount"].sum()) if not grouped.empty else 0.0
    if total_amount_sum > 0:
        grouped["sales_share_pct"] = grouped["total_amount"] / total_amount_sum * 100.0
    else:
        grouped["sales_share_pct"] = 0.0
    grouped["amount_per_order"] = grouped["total_amount"] / grouped["order_count"].replace(0, pd.NA)
    grouped["amount_per_order"] = grouped["amount_per_order"].fillna(0.0)
    return grouped


def _append_analysis_total_row(
    summary_df: pd.DataFrame,
    *,
    name_col: str,
) -> pd.DataFrame:
    """상품/옵션 분석표 하단 합계 행 추가."""
    if summary_df.empty:
        return summary_df
    body = summary_df.copy()
    total_amount = float(pd.to_numeric(body["total_amount"], errors="coerce").fillna(0).sum())
    total_orders = int(pd.to_numeric(body["order_count"], errors="coerce").fillna(0).sum())
    total_order_quantity = float(
        pd.to_numeric(body["order_quantity"], errors="coerce").fillna(0).sum()
    )
    total_sold = float(pd.to_numeric(body["sold_quantity"], errors="coerce").fillna(0).sum())
    avg_amount_per_order = (total_amount / total_orders) if total_orders > 0 else 0.0
    total_row = pd.DataFrame(
        [
            {
                name_col: "합계",
                "order_count": total_orders,
                "order_quantity": total_order_quantity,
                "total_amount": total_amount,
                "sales_share_pct": 100.0 if total_amount > 0 else 0.0,
                "sold_quantity": total_sold,
                "amount_per_order": avg_amount_per_order,
            }
        ]
    )
    return pd.concat([body, total_row], ignore_index=True)


def _prepare_detail_ledger_for_display(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    """상세 주문 원장 표시 규칙: 집계일 포맷/일부 컬럼 숨김/안내문 상단 분리."""
    ledger = frame.copy()
    guidance: str | None = None

    if "aggregation_window_kst" in ledger.columns:
        guide_series = ledger["aggregation_window_kst"].dropna().astype(str).str.strip()
        guide_series = guide_series[guide_series != ""]
        if not guide_series.empty:
            guidance = guide_series.iloc[0]
        ledger = ledger.drop(columns=["aggregation_window_kst"])

    if "content_order_no" in ledger.columns:
        ledger = ledger.drop(columns=["content_order_no"])

    if "date" in ledger.columns:
        ledger["date"] = ledger["date"].map(_format_sales_date_compact)

    return ledger, guidance


def _daily_summary_from_orders(frame: pd.DataFrame, target_date: date) -> dict[str, float]:
    day_df = frame[frame["date"].dt.date == target_date].copy()
    if day_df.empty:
        return {
            "total_amount": 0.0,
            "order_count": 0.0,
            "order_quantity": 0.0,
            "sold_quantity": 0.0,
            "customer_count": 0.0,
        }
    amount_col = "net_revenue" if "net_revenue" in day_df.columns else "amount"
    return {
        "total_amount": float(pd.to_numeric(day_df[amount_col], errors="coerce").fillna(0).sum()),
        "order_count": float(day_df["order_id"].astype(str).replace("", pd.NA).dropna().nunique()),
        "order_quantity": float(pd.to_numeric(day_df["quantity"], errors="coerce").fillna(0).sum()),
        "sold_quantity": float(pd.to_numeric(day_df["real_quantity"], errors="coerce").fillna(0).sum()),
        "customer_count": float(day_df["buyer_id"].astype(str).replace("", pd.NA).dropna().nunique()),
    }


def _product_revenue_delta_table(
    frame: pd.DataFrame,
    current_date: date,
    compare_date: date,
) -> pd.DataFrame:
    amount_col = "net_revenue" if "net_revenue" in frame.columns else "amount"
    key = "option_product_label"
    curr = (
        frame[frame["date"].dt.date == current_date]
        .groupby(key, as_index=False)
        .agg(current_revenue=(amount_col, "sum"))
    )
    prev = (
        frame[frame["date"].dt.date == compare_date]
        .groupby(key, as_index=False)
        .agg(prev_revenue=(amount_col, "sum"))
    )
    merged = curr.merge(prev, on=key, how="outer").fillna(0.0)
    if merged.empty:
        return merged
    merged["revenue_diff"] = merged["current_revenue"] - merged["prev_revenue"]
    merged["revenue_diff_pct"] = merged.apply(
        lambda r: ((r["revenue_diff"] / r["prev_revenue"]) * 100.0) if r["prev_revenue"] > 0 else 0.0,
        axis=1,
    )
    return merged.sort_values("revenue_diff", ascending=False)


def _sorted_business_dates_up_to(frame: pd.DataFrame, report_date: date) -> list[date]:
    s = frame["date"].dt.date
    return sorted({d for d in s.dropna().unique() if d <= report_date})


def _option_avg_daily_in_tail_window(
    frame: pd.DataFrame,
    *,
    amount_col: str,
    report_date: date,
    calendar_days: int,
    key_col: str,
) -> pd.DataFrame:
    """최근 min(calendar_days, 로드된 영업일 수)일에서 옵션상품명별 일평균 매출(누적합/달력일수)."""
    dates = _sorted_business_dates_up_to(frame, report_date)
    if not dates:
        return pd.DataFrame(columns=[key_col, "avg_daily"])
    use_n = min(int(calendar_days), len(dates))
    tail = set(dates[-use_n:])
    df = frame.assign(_d=frame["date"].dt.date)
    df = df[df["_d"].isin(tail)]
    if df.empty:
        return pd.DataFrame(columns=[key_col, "avg_daily"])
    g = df.groupby(key_col, as_index=False).agg(_total=(amount_col, "sum"))
    g["avg_daily"] = g["_total"] / float(use_n)
    return g[[key_col, "avg_daily"]]


def _simple_nextday_forecast(frame: pd.DataFrame, report_date: date) -> tuple[float, str]:
    amount_col = "net_revenue" if "net_revenue" in frame.columns else "amount"
    daily = (
        frame[frame["date"].dt.date <= report_date]
        .assign(_d=lambda df: df["date"].dt.date)
        .groupby("_d", as_index=False)
        .agg(total_amount=(amount_col, "sum"))
        .sort_values("_d")
    )
    if daily.empty:
        return 0.0, "하"
    recent = daily.tail(7)
    forecast = float(pd.to_numeric(recent["total_amount"], errors="coerce").fillna(0).mean())
    confidence = "상" if len(recent) >= 7 else ("중" if len(recent) >= 4 else "하")
    return forecast, confidence


def _build_product_insight_table(
    frame: pd.DataFrame,
    report_date: date,
    compare_date: date,
) -> pd.DataFrame:
    """옵션상품명별 증감, 원인 추정, 단기·중기·보합·상한 예측 참고(로드된 DB 구간만 사용)."""
    amount_col = "net_revenue" if "net_revenue" in frame.columns else "amount"
    key = "option_product_label"
    df = frame.copy()
    if key not in df.columns:
        df[key] = [_option_product_label(a, b) for a, b in zip(df["product_name"], df["option_name"])]
    df["_biz_date"] = df["date"].dt.date

    curr = (
        df[df["_biz_date"] == report_date]
        .groupby(key, as_index=False)
        .agg(
            today_revenue=(amount_col, "sum"),
            today_order_qty=("quantity", "sum"),
            today_sold_qty=("real_quantity", "sum"),
            today_order_count=("order_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
            today_customer_count=("buyer_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
        )
    )
    prev = (
        df[df["_biz_date"] == compare_date]
        .groupby(key, as_index=False)
        .agg(
            prev_revenue=(amount_col, "sum"),
            prev_order_qty=("quantity", "sum"),
            prev_order_count=("order_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
            prev_customer_count=("buyer_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
        )
    )
    merged = curr.merge(prev, on=key, how="outer").fillna(0.0)
    if merged.empty:
        return merged

    merged["today_avg_price"] = merged["today_revenue"] / merged["today_order_qty"].replace(0, pd.NA)
    merged["prev_avg_price"] = merged["prev_revenue"] / merged["prev_order_qty"].replace(0, pd.NA)
    merged["today_avg_price"] = merged["today_avg_price"].fillna(0.0)
    merged["prev_avg_price"] = merged["prev_avg_price"].fillna(0.0)

    merged["revenue_diff"] = merged["today_revenue"] - merged["prev_revenue"]
    merged["revenue_diff_pct"] = merged.apply(
        lambda r: ((r["revenue_diff"] / r["prev_revenue"]) * 100.0) if r["prev_revenue"] > 0 else 0.0,
        axis=1,
    )
    merged["price_diff_pct"] = merged.apply(
        lambda r: ((r["today_avg_price"] - r["prev_avg_price"]) / r["prev_avg_price"] * 100.0)
        if r["prev_avg_price"] > 0
        else 0.0,
        axis=1,
    )

    short_df = _option_avg_daily_in_tail_window(
        df, amount_col=amount_col, report_date=report_date, calendar_days=7, key_col=key
    ).rename(columns={"avg_daily": "forecast_short"})
    med_df = _option_avg_daily_in_tail_window(
        df, amount_col=amount_col, report_date=report_date, calendar_days=30, key_col=key
    ).rename(columns={"avg_daily": "forecast_medium"})
    merged = merged.merge(short_df, on=key, how="left")
    merged = merged.merge(med_df, on=key, how="left")
    merged["forecast_short"] = pd.to_numeric(merged["forecast_short"], errors="coerce").fillna(0.0)
    merged["forecast_medium"] = pd.to_numeric(merged["forecast_medium"], errors="coerce").fillna(0.0)
    merged["forecast_balanced"] = (merged["forecast_short"] + merged["forecast_medium"]) / 2.0
    merged["forecast_upper"] = merged[["forecast_short", "forecast_medium", "today_revenue"]].max(axis=1)

    def _reason(row: pd.Series) -> str:
        reasons: list[str] = []
        if abs(float(row["price_diff_pct"])) >= 5.0:
            direction = "상승" if float(row["price_diff_pct"]) > 0 else "하락"
            reasons.append(f"평균 판매가 {direction}")
        qty_diff = float(row["today_order_qty"]) - float(row["prev_order_qty"])
        if abs(qty_diff) >= 3:
            reasons.append("주문수량 변화")
        cust_diff = float(row["today_customer_count"]) - float(row["prev_customer_count"])
        if abs(cust_diff) >= 2:
            reasons.append("고객 유입/재구매 변화")
        if not reasons:
            reasons.append("기본 변동 범위")
        return " + ".join(reasons[:2])

    merged["change_reason"] = merged.apply(_reason, axis=1)
    return merged.sort_values("revenue_diff", ascending=False)


def _safe_date(value: object) -> date | None:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def _build_happycall_candidates(frame: pd.DataFrame, report_date: date) -> pd.DataFrame:
    """고객별 이탈 위험/해피콜 우선순위 후보를 계산한다."""
    rows: list[dict[str, object]] = []
    for buyer_id, grp in frame.groupby("buyer_id"):
        buyer = str(buyer_id or "").strip()
        if not buyer:
            continue
        g = grp.sort_values("date")
        order_days = sorted(
            {d for d in (_safe_date(v) for v in g["date"]) if d is not None}
        )
        if not order_days:
            continue
        last_order_date = order_days[-1]
        order_count = len(order_days)
        total_revenue = float(pd.to_numeric(g["net_revenue"], errors="coerce").fillna(0).sum())
        customer_name = str(g["buyer_name"].dropna().iloc[-1]) if g["buyer_name"].notna().any() else ""

        if order_count >= 2:
            cycles = [
                (order_days[i] - order_days[i - 1]).days
                for i in range(1, len(order_days))
                if (order_days[i] - order_days[i - 1]).days > 0
            ]
            avg_cycle_days = float(sum(cycles) / len(cycles)) if cycles else 7.0
        else:
            avg_cycle_days = 7.0

        expected_next = last_order_date + timedelta(days=max(1, int(round(avg_cycle_days))))
        delay_days = (report_date - expected_next).days
        if delay_days < 0:
            delay_days = 0

        if order_count == 1:
            segment = "신규"
        elif order_count <= 3:
            segment = "성장"
        elif delay_days >= max(2, int(round(avg_cycle_days * 0.5))):
            segment = "휴면위험"
        else:
            segment = "충성"

        score = min(delay_days * 10, 60) + min(order_count * 4, 20) + min(total_revenue / 100000, 20)
        if segment == "휴면위험":
            score += 10

        if segment == "신규":
            action = "첫 구매 만족도 확인 + 재구매 쿠폰 안내"
        elif segment == "휴면위험":
            action = "이탈 방지 해피콜 + 재구매 혜택 제안"
        elif segment == "성장":
            action = "주기 도래 전 리마인드 메시지/콜"
        else:
            action = "충성 고객 감사 혜택 안내"

        rows.append(
            {
                "buyer_id": buyer,
                "buyer_name": customer_name,
                "segment": segment,
                "order_count_lifetime": order_count,
                "revenue_lifetime": total_revenue,
                "last_order_date": last_order_date,
                "avg_cycle_days": avg_cycle_days,
                "expected_next_order_date": expected_next,
                "delay_days": delay_days,
                "priority_score": float(score),
                "recommended_action": action,
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values(
        ["priority_score", "delay_days", "revenue_lifetime"],
        ascending=[False, False, False],
    )
    return out


@st.cache_data(ttl=300)
def load_option_margin_snapshot(target_date: date) -> pd.DataFrame:
    """옵션별 마진 스냅샷 로드 (테이블 미생성 시 빈 결과)."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    stat_date,
                    product_name,
                    option_name,
                    delivery_fee_type,
                    order_count,
                    order_quantity,
                    net_revenue,
                    expected_settlement_amount,
                    customer_paid_shipping,
                    seller_shipping_burden,
                    estimated_cost,
                    margin_amount,
                    margin_rate_pct
                FROM agg_option_margin_daily
                WHERE stat_date = :d
                ORDER BY margin_amount DESC
                """
            ),
            {"d": target_date},
        ).mappings().all()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()
    finally:
        db.close()


st.set_page_config(page_title="네이버 모디바 대시보드", layout="wide")


def _read_secret_or_env(key: str) -> str | None:
    """Streamlit secrets 우선, 없으면 환경변수에서 읽는다."""
    try:
        value = st.secrets.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    env_value = os.environ.get(key)
    if isinstance(env_value, str) and env_value.strip():
        return env_value.strip()
    return None


def _verify_credentials(username: str, password: str) -> bool:
    """아이디/비밀번호를 안전하게 비교한다."""
    expected_username = _read_secret_or_env("DASHBOARD_USERNAME")
    expected_password = _read_secret_or_env("DASHBOARD_PASSWORD")

    if expected_username is None:
        expected_username = "admin"

    if expected_password is None:
        return False

    username_ok = hmac.compare_digest(username.strip(), expected_username)
    password_ok = hmac.compare_digest(password, expected_password)
    return username_ok and password_ok


def _require_login() -> bool:
    if st.session_state.get("authenticated", False):
        return True

    st.title("🔐 보안 접속")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submit = st.form_submit_button("로그인", type="primary")
    if submit:
        if _verify_credentials(username=username, password=password):
            st.session_state.authenticated = True
            st.session_state["auth_user"] = username.strip()
            st.session_state["_refresh_orders_after_login"] = True
            fetch_order_data.clear()
            fetch_db_stats.clear()
            st.rerun()
        else:
            st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

    st.stop()
    return False


def main_content() -> None:
    if st.session_state.pop("_refresh_orders_after_login", False):
        fetch_order_data.clear()
        fetch_db_stats.clear()

    _safe_autorefresh(interval_ms=60000, key="naver_modiba_dashboard_autorefresh")

    apply_dashboard_theme()

    api_base_url = DEFAULT_API_BASE_URL
    revenue_basis = "payment"
    db_subtitle = ""
    try:
        ds = fetch_db_stats(api_base_url)
        _mark_api_success()
        lp = ds.get("latest_payment_date")
        lb = ds.get("latest_business_date")
        db_subtitle = (
            f"DB `orders` {int(ds.get('orders_count') or 0):,}건 · "
            f"최신 결제일시 {lp or '—'} · "
            f"최신 영업일(집계 `date`){lb or '—'}"
        )
    except Exception as exc:
        _mark_api_failure(exc)
        db_subtitle = "DB 통계(`/analytics/db-stats`)를 불러오지 못했습니다."

    render_page_title(
        "네이버 친절한 모디바 주문현황",
        subtitle=db_subtitle,
    )
    _render_api_health_caption()

    anchor = _kst_anchor_business_date()
    fetch_start = anchor - timedelta(days=ORDERS_RAW_FETCH_DAYS)

    data_loaded_at = ""
    data_loaded_mode = "live"
    try:
        raw_df = fetch_order_data(api_base_url, revenue_basis, fetch_start, anchor)
        order_df = normalize_order_data(raw_df)
        st.session_state["last_good_order_df"] = order_df.copy()
        data_loaded_at = format_now_kst()
        st.session_state["last_data_loaded_at"] = data_loaded_at
        st.session_state["last_data_loaded_mode"] = "live"
        _mark_api_success()
    except Exception as exc:
        _mark_api_failure(exc)
        cached_df = st.session_state.get("last_good_order_df")
        if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
            order_df = cached_df.copy()
            data_loaded_at = st.session_state.get("last_data_loaded_at", "")
            data_loaded_mode = "fallback"
        else:
            st.error("주문 데이터를 불러오지 못했습니다. 사이드바 API URL을 확인하세요.")
            st.stop()

    if not data_loaded_at:
        data_loaded_at = st.session_state.get("last_data_loaded_at", "")

    if order_df.empty:
        st.stop()

    if "option_product_label" not in order_df.columns:
        order_df = order_df.copy()
        order_df["option_product_label"] = [
            _option_product_label(a, b) for a, b in zip(order_df["product_name"], order_df["option_name"])
        ]

    if data_loaded_mode == "fallback":
        st.warning("네트워크/API 오류로 캐시된 마지막 정상 데이터를 표시 중입니다.")

    now_kst = datetime.now(KST)
    default_end = anchor
    default_start = default_end

    tab_kpi, tab_summary, tab_customer, tab_product, tab_margin, tab_detail = st.tabs(
        ["KPI", "요약", "고객", "상품", "마진", "분석상세"]
    )

    report_date = default_end
    compare_date = report_date - timedelta(days=7)
    report_summary = _daily_summary_from_orders(order_df, report_date)
    compare_summary = _daily_summary_from_orders(order_df, compare_date)
    forecast_amount, forecast_conf = _simple_nextday_forecast(order_df, report_date)
    product_delta = _product_revenue_delta_table(order_df, report_date, compare_date)
    product_insight = _build_product_insight_table(order_df, report_date, compare_date)
    happycall_df = _build_happycall_candidates(order_df, report_date)
    margin_df = load_option_margin_snapshot(report_date)

    def _delta_text(curr: float, prev: float) -> str:
        if prev == 0:
            return "0.0%"
        return f"{((curr - prev) / prev) * 100.0:.1f}%"

    with tab_summary:
        section_heading("경영 요약 리포트")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(
            "순매출",
            f"{report_summary['total_amount']:,.0f}원",
            _delta_text(report_summary["total_amount"], compare_summary["total_amount"]),
        )
        m2.metric(
            "주문건수",
            f"{int(report_summary['order_count']):,}",
            _delta_text(report_summary["order_count"], compare_summary["order_count"]),
        )
        m3.metric(
            "주문수량",
            f"{report_summary['order_quantity']:,.0f}",
            _delta_text(report_summary["order_quantity"], compare_summary["order_quantity"]),
        )
        m4.metric(
            "판매수량",
            f"{report_summary['sold_quantity']:,.0f}",
            _delta_text(report_summary["sold_quantity"], compare_summary["sold_quantity"]),
        )
        m5.metric(
            "고객수",
            f"{int(report_summary['customer_count']):,}",
            _delta_text(report_summary["customer_count"], compare_summary["customer_count"]),
        )
        st.caption(
            f"기준일 {report_date} · 전주 비교일 {compare_date} · "
            f"내일 예상 매출 {forecast_amount:,.0f}원 (신뢰도 {forecast_conf})"
        )

        rise_df = product_delta.head(5)[
            [
                "option_product_label",
                "current_revenue",
                "prev_revenue",
                "revenue_diff",
                "revenue_diff_pct",
            ]
        ].copy()
        rise_df["option_product_label"] = rise_df["option_product_label"].map(_option_grid_display_text)
        fall_df = product_delta.sort_values("revenue_diff").head(5)[
            [
                "option_product_label",
                "current_revenue",
                "prev_revenue",
                "revenue_diff",
                "revenue_diff_pct",
            ]
        ].copy()
        fall_df["option_product_label"] = fall_df["option_product_label"].map(_option_grid_display_text)

        title_l, title_r = st.columns(2)
        with title_l:
            st.markdown("#### 전주 대비 상승 옵션 Top5")
        with title_r:
            st.markdown("#### 전주 대비 하락 옵션 Top5")
        rise_col, fall_col = st.columns(2)
        with rise_col:
            show_data_grid(
                rise_df,
                keep_input_order=True,
                height=SUMMARY_RISE_FALL_DATAFRAME_HEIGHT_PX,
            )
        with fall_col:
            show_data_grid(
                fall_df,
                keep_input_order=True,
                height=SUMMARY_RISE_FALL_DATAFRAME_HEIGHT_PX,
            )

        action_msgs: list[str] = []
        if report_summary["total_amount"] < compare_summary["total_amount"]:
            action_msgs.append("순매출이 전주 대비 하락: 하락 Top 옵션의 가격·노출·재고를 우선 점검하세요.")
        if report_summary["customer_count"] < compare_summary["customer_count"]:
            action_msgs.append("고객수가 감소: 최근 2주 미주문 고객 리마인드(해피콜/메시지) 캠페인을 진행하세요.")
        if forecast_amount < report_summary["total_amount"]:
            action_msgs.append("내일 예상 매출이 오늘보다 낮음: 상위 상품 재구매 유도 번들/쿠폰을 사전 노출하세요.")
        if not action_msgs:
            action_msgs.append("주요 지표가 안정적입니다. 상승 상품 재고/배송 품질 유지에 집중하세요.")
        st.info("오늘 실행 액션: " + " / ".join(action_msgs[:3]))

    with tab_product:
        section_heading("상품 증감/원인/내일예측", level=3)
        if product_insight.empty:
            st.caption("분석 가능한 상품 데이터가 없습니다.")
        else:
            insight_cols = [
                "option_product_label",
                "today_order_qty",
                "today_sold_qty",
                "today_revenue",
                "revenue_diff",
                "revenue_diff_pct",
                "price_diff_pct",
                "forecast_short",
                "forecast_medium",
                "forecast_balanced",
                "forecast_upper",
                "change_reason",
            ]
            st.caption(
                f"기준일 {report_date} vs 전주 동일요일 {compare_date}. "
                "단기·중기=로드된 영업일 중 최근 7일·30일(미만이면 가능한 만큼) 합계를 일수로 나눈 일평균 매출. "
                "보합=(단기+중기)/2, 상한=max(단기,중기,오늘매출). 누적 기간이 짧으면 참고용입니다."
            )
            insight_show = product_insight[insight_cols].head(20).copy()
            insight_show["option_product_label"] = insight_show["option_product_label"].map(
                _option_grid_display_text
            )
            show_data_grid(insight_show, keep_input_order=True)

    with tab_customer:
        section_heading("고객 이탈/해피콜 실행판", level=3)
        if happycall_df.empty:
            st.caption("해피콜 대상 계산을 위한 고객 데이터가 없습니다.")
        else:
            total_candidates = len(happycall_df)
            risk_count = int((happycall_df["segment"] == "휴면위험").sum())
            avg_delay = float(pd.to_numeric(happycall_df["delay_days"], errors="coerce").fillna(0).mean())
            top_revenue_target = float(
                pd.to_numeric(happycall_df.head(20)["revenue_lifetime"], errors="coerce").fillna(0).sum()
            )

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("오늘 콜 대상(추천)", f"{total_candidates:,}")
            c2.metric("휴면위험 고객", f"{risk_count:,}")
            c3.metric("평균 지연일", f"{avg_delay:.1f}일")
            c4.metric("우선대상 예상 LTV", f"{top_revenue_target:,.0f}원")

            st.caption("우선순위 점수 기준 상위 고객부터 해피콜 실행")
            action_choices = sorted(
                happycall_df["recommended_action"].dropna().astype(str).unique().tolist()
            )
            selected_actions = st.multiselect(
                "권장 액션 필터",
                options=action_choices,
                default=[],
                key="happycall_recommended_action_filter",
                help="선택한 권장 액션만 표에 표시합니다. 선택이 없으면 전체입니다.",
            )
            display_happycall = (
                happycall_df
                if not selected_actions
                else happycall_df[happycall_df["recommended_action"].isin(selected_actions)]
            )
            call_cols = [
                "buyer_id",
                "buyer_name",
                "segment",
                "last_order_date",
                "expected_next_order_date",
                "delay_days",
                "order_count_lifetime",
                "revenue_lifetime",
                "priority_score",
                "recommended_action",
            ]
            if display_happycall.empty:
                st.caption("선택한 권장 액션에 해당하는 행이 없습니다. 필터를 해제하세요.")
            else:
                show_data_grid(display_happycall[call_cols].head(30), keep_input_order=True)

    with tab_margin:
        section_heading("가격-매출-마진 방어판", level=3)
        if margin_df.empty:
            st.caption(
                "옵션 마진 스냅샷 데이터가 없습니다. "
                "`create_margin_management_tables.sql` 실행 후 "
                "`upsert_option_margin_daily.sql` 배치를 먼저 수행해 주세요."
            )
        else:
            threshold_col1, threshold_col2 = st.columns([2, 6])
            with threshold_col1:
                margin_threshold_pct = st.number_input(
                    "마진율 임계치(%)",
                    min_value=-100.0,
                    max_value=100.0,
                    value=float(st.session_state.get("margin_threshold_pct", 10.0)),
                    step=0.5,
                    key="margin_threshold_pct",
                )
            with threshold_col2:
                st.caption(
                    "임계치 미만 옵션은 경고 대상으로 분류됩니다. "
                    "쿠폰/할인은 순매출(`net_revenue`)에 이미 반영되어 마진 계산에 포함됩니다."
                )

            total_revenue = float(pd.to_numeric(margin_df["net_revenue"], errors="coerce").fillna(0).sum())
            total_cost = float(pd.to_numeric(margin_df["estimated_cost"], errors="coerce").fillna(0).sum())
            total_margin = float(pd.to_numeric(margin_df["margin_amount"], errors="coerce").fillna(0).sum())
            margin_rate = (total_margin / total_revenue * 100.0) if total_revenue > 0 else 0.0
            low_margin_df = margin_df[
                pd.to_numeric(margin_df["margin_rate_pct"], errors="coerce").fillna(0) < margin_threshold_pct
            ]
            critical_margin_df = margin_df[
                pd.to_numeric(margin_df["margin_rate_pct"], errors="coerce").fillna(0) < 0
            ]

            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("총 순매출", f"{total_revenue:,.0f}원")
            m2.metric("총 추정원가", f"{total_cost:,.0f}원")
            m3.metric("총 마진액", f"{total_margin:,.0f}원")
            m4.metric("평균 마진율", f"{margin_rate:.1f}%")
            m5.metric(f"임계치 미만({margin_threshold_pct:.1f}%↓)", f"{len(low_margin_df):,}개")
            m6.metric("긴급(마진율<0%)", f"{len(critical_margin_df):,}개")

            if len(critical_margin_df) > 0:
                st.warning("마진율이 0% 미만인 옵션이 있습니다. 가격/쿠폰/배송비 규칙을 우선 점검하세요.")
            elif len(low_margin_df) > 0:
                st.info("임계치 미만 옵션이 있습니다. 옵션 단가/프로모션 조건을 점검하세요.")

            margin_cols = [
                "product_name",
                "option_name",
                "delivery_fee_type",
                "order_count",
                "order_quantity",
                "net_revenue",
                "estimated_cost",
                "margin_amount",
                "margin_rate_pct",
            ]
            st.caption("마진율 기준 취약 옵션(하위)과 고마진 옵션(상위)을 함께 점검하세요.")
            hi_col, lo_col = st.columns(2)
            with hi_col:
                st.markdown("#### 고마진 옵션 Top10")
                hi = margin_df.sort_values("margin_rate_pct", ascending=False).head(10)
                show_data_grid(hi[margin_cols], keep_input_order=True)
            with lo_col:
                st.markdown("#### 저마진 옵션 Top10")
                lo = margin_df.sort_values("margin_rate_pct", ascending=True).head(10)
                show_data_grid(lo[margin_cols], keep_input_order=True)

    with tab_kpi:
        section_heading("KPI")
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
        prev_start = kpi_start_date - timedelta(days=7)
        prev_end = kpi_end_date - timedelta(days=7)
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
        expected_settlement_total = float(
            pd.to_numeric(kpi_filtered_df["expected_settlement_amount"], errors="coerce").fillna(0).sum()
        )
        prev_expected_settlement_total = float(
            pd.to_numeric(prev_df["expected_settlement_amount"], errors="coerce").fillna(0).sum()
        )
        settlement_ratio_pct = (
            (expected_settlement_total / whole["total_amount"]) * 100.0
            if float(whole["total_amount"]) != 0.0
            else 0.0
        )
        settlement_diff_amount = expected_settlement_total - float(whole["total_amount"])

        def _prev_delta(curr: float, base: float) -> str | None:
            if not compare_prev:
                return None
            return f"{delta_rate(curr, base):.1f}%"

        compare_info = f"vs 1주 전 ({prev_start}~{prev_end})"

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
            r2a, r2b, r2c, r2d = st.columns(4)
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
                f"정산예정금액 합계 ({compare_info})",
                f"{expected_settlement_total:,.0f}원",
                _prev_delta(expected_settlement_total, prev_expected_settlement_total),
            )
            r2c.caption(
                f"순매출 대비 {settlement_ratio_pct:.1f}% · 차액 {settlement_diff_amount:,.0f}원"
            )
            r2d.metric(
                "일수",
                f"{period_days}일",
                None,
            )

        st.markdown("")
        section_heading("일자별", level=3)
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

    with tab_detail:
        section_heading("분석상세")
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
            product_summary = _prepare_analysis_summary(
                analysis_filtered_df,
                group_key="product_name",
                revenue_column=_rev_col,
            )
            product_summary = _append_analysis_total_row(product_summary, name_col="product_name")
            product_summary = product_summary[
                [
                    "product_name",
                    "order_count",
                    "order_quantity",
                    "total_amount",
                    "sales_share_pct",
                    "sold_quantity",
                    "amount_per_order",
                ]
            ]
            show_data_grid(product_summary, keep_input_order=True)

        with tab_option_sales:
            option_name_summary = _prepare_analysis_summary(
                analysis_filtered_df,
                group_key="option_name",
                revenue_column=_rev_col,
            )
            option_name_summary = _append_analysis_total_row(option_name_summary, name_col="option_name")
            option_name_summary = option_name_summary[
                [
                    "option_name",
                    "order_count",
                    "order_quantity",
                    "total_amount",
                    "sales_share_pct",
                    "sold_quantity",
                    "amount_per_order",
                ]
            ]
            show_data_grid(option_name_summary, keep_input_order=True)

        with st.expander("상세 주문 원장 보기", expanded=False):
            detail_ledger, guidance_text = _prepare_detail_ledger_for_display(analysis_filtered_df)
            if guidance_text:
                st.caption(f"참조 · 매출집계일 안내: {guidance_text}")
            show_data_grid(detail_ledger)


if _require_login():
    main_content()
