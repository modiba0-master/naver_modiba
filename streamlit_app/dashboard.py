from __future__ import annotations

import re
import os
import sys
import time
from typing import Any
from datetime import date, datetime, timedelta
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

_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


def _format_sales_date_label(d: date) -> str:
    """매출 집계일(달력) + 요일 — KPI 일자 행에 통일 표기."""
    return f"{d.isoformat()} ({_WEEKDAY_KO[d.weekday()]})"


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
def fetch_order_data(base_url: str, revenue_basis: str = "payment") -> pd.DataFrame:
    url = base_url.rstrip("/")
    payload = _http_get_json_with_retry(
        f"{url}/analytics/orders-raw",
        params={"revenue_basis": revenue_basis},
    )
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
            st.session_state["_refresh_orders_after_login"] = True
            fetch_order_data.clear()
            fetch_db_stats.clear()
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")

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

    data_loaded_at = ""
    data_loaded_mode = "live"
    try:
        raw_df = fetch_order_data(api_base_url, revenue_basis)
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

    if data_loaded_mode == "fallback":
        st.warning("네트워크/API 오류로 캐시된 마지막 정상 데이터를 표시 중입니다.")

    default_end = datetime.now(KST).date()
    default_start = default_end

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

    st.markdown("---")
    section_heading("분석")
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
        product_summary = product_summary[
            ["product_name", "quantity", "order_count", "total_amount", "real_quantity"]
        ]
        show_data_grid(product_summary)

    with tab_option_sales:
        option_name_summary = (
            analysis_filtered_df.groupby("option_name", as_index=False)
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
        option_name_summary = option_name_summary[
            ["option_name", "quantity", "order_count", "total_amount", "real_quantity"]
        ]
        show_data_grid(option_name_summary)

    show_data_grid(analysis_filtered_df)


if _require_login():
    main_content()
