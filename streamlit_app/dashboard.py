from __future__ import annotations

import re
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st
from sqlalchemy import text
from streamlit_autorefresh import st_autorefresh

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.data_grid import show_data_grid, show_summary_table
from services.db import SessionLocal
from services.kpi_from_filtered import (
    delta_rate,
    expected_sales_from_recent_7d,
    kpi_aggregate,
)

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


@st.cache_data(ttl=60)
def fetch_order_data(base_url: str) -> pd.DataFrame:
    url = base_url.rstrip("/")
    response = httpx.get(
        f"{url}/analytics/orders-raw",
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", [])
    return pd.DataFrame(items)


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
                    COALESCE(SUM(amount), 0) AS total_sales
                FROM orders
                WHERE DATE(order_date) = :today
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
    df["payment_date"] = pd.to_datetime(df["payment_date"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
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


def build_weekend_settlement_summary(
    frame: pd.DataFrame, start_date: date, end_date: date
) -> pd.DataFrame:
    """
    결제일 기준 토/일 매출을 월요일 정산일에 매핑해 확인용 테이블을 만든다.
    - saturday_amount: 토요일 결제 금액
    - sunday_amount: 일요일 결제 금액
    - weekend_total_amount: 토/일 합산 금액(월요일 정산 반영분)
    - monday_business_total_amount: 해당 월요일 business_date 전체 금액
    - monday_non_weekend_amount: 월요일 business_date 중 주말분 제외 금액
    """
    if frame.empty or "payment_date" not in frame.columns:
        return pd.DataFrame()

    payment_window_df = frame[
        (frame["payment_date"].dt.date >= start_date)
        & (frame["payment_date"].dt.date <= end_date)
    ].copy()
    if payment_window_df.empty:
        return pd.DataFrame()

    grouped_df = payment_window_df[payment_window_df["payment_date"].dt.weekday.isin([5, 6, 0])].copy()
    if grouped_df.empty:
        return pd.DataFrame()

    weekday_offset = grouped_df["payment_date"].dt.weekday.map({5: 2, 6: 1, 0: 0}).fillna(0)
    grouped_df["settlement_monday"] = (
        grouped_df["payment_date"].dt.normalize() + pd.to_timedelta(weekday_offset, unit="D")
    ).dt.date
    grouped_df["payment_weekday"] = grouped_df["payment_date"].dt.weekday

    pivot = (
        grouped_df.groupby(["settlement_monday", "payment_weekday"], as_index=False)["amount"]
        .sum()
        .pivot(index="settlement_monday", columns="payment_weekday", values="amount")
        .fillna(0.0)
    )
    pivot = pivot.rename(
        columns={
            5: "saturday_amount",
            6: "sunday_amount",
            0: "monday_payment_amount",
        }
    )
    summary = pivot.reset_index()
    for col in ["saturday_amount", "sunday_amount", "monday_payment_amount"]:
        if col not in summary.columns:
            summary[col] = 0.0

    summary["weekend_total_amount"] = summary["saturday_amount"] + summary["sunday_amount"]
    summary["sat_sun_mon_total_amount"] = (
        summary["saturday_amount"] + summary["sunday_amount"] + summary["monday_payment_amount"]
    )

    return summary[
        [
            "settlement_monday",
            "saturday_amount",
            "sunday_amount",
            "monday_payment_amount",
            "weekend_total_amount",
            "sat_sun_mon_total_amount",
        ]
    ].sort_values("settlement_monday", ascending=False)


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
    st.markdown(
        """
        <style>
        [data-testid="stDataFrame"] table { width: 100%; }
        [data-testid="stDataFrame"] th,
        [data-testid="stDataFrame"] td { text-align: center !important; vertical-align: middle !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st_autorefresh(interval=60000, key="naver_modiba_dashboard_autorefresh")

    header_left, header_right = st.columns([5, 1])
    with header_left:
        st.title("네이버 커머스 주문 분석 대시보드")
    with header_right:
        st.markdown("")  # vertical align with title
        if "last_click" not in st.session_state:
            st.session_state.last_click = 0.0
        if st.button("🔄 새로고침"):
            now = time.time()
            if now - st.session_state.last_click < 5:
                st.warning("5초 후 다시 시도하세요")
            else:
                st.session_state.last_click = now
                st.rerun()
        if st.button("⚠ 실시간 강제 새로고침"):
            st.warning("강제 새로고침은 캐시를 비우고 API를 재호출합니다. 일시 장애 시 오류가 노출될 수 있습니다.")
            st.cache_data.clear()
            st.session_state.pop("last_good_order_df", None)
            st.rerun()

    with st.sidebar:
        st.header("API 설정")
        api_base_url = st.text_input("FastAPI 기본 URL", value=DEFAULT_API_BASE_URL).rstrip("/")

    data_loaded_at = ""
    data_loaded_mode = "live"
    try:
        raw_df = fetch_order_data(api_base_url)
        order_df = normalize_order_data(raw_df)
        st.session_state["last_good_order_df"] = order_df.copy()
        data_loaded_at = format_now_kst()
        st.session_state["last_data_loaded_at"] = data_loaded_at
        st.session_state["last_data_loaded_mode"] = "live"
    except Exception as exc:
        cached_df = st.session_state.get("last_good_order_df")
        if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
            st.warning(f"실시간 조회 실패로 직전 데이터로 표시합니다: {exc}")
            order_df = cached_df.copy()
            data_loaded_at = st.session_state.get("last_data_loaded_at", "")
            data_loaded_mode = "fallback"
        else:
            st.error(f"주문 데이터 조회 실패: {exc}")
            st.stop()

    if not data_loaded_at:
        data_loaded_at = st.session_state.get("last_data_loaded_at", "")
    if data_loaded_mode == "fallback":
        st.caption(f"최근 데이터 불러온 시각: {data_loaded_at} (직전 성공 데이터)")
    elif data_loaded_at:
        st.caption(f"최근 데이터 불러온 시각: {data_loaded_at}")

    if order_df.empty:
        st.warning(
            "API 응답에 상세 주문 데이터가 없습니다. "
            "`/analytics/orders-by-date` 응답 구조에 필요한 필드가 포함되어야 합니다."
        )
        st.stop()

    default_end = datetime.now(KST).date()
    default_start = default_end - timedelta(days=6)

    st.markdown("## KPI 영역")
    kpi_col1, kpi_col2 = st.columns(2)
    with kpi_col1:
        kpi_start_date = st.date_input("KPI 시작일", value=default_start, key="kpi_start_date")
    with kpi_col2:
        kpi_end_date = st.date_input("KPI 종료일", value=default_end, key="kpi_end_date")

    if kpi_start_date > kpi_end_date:
        st.error("KPI 시작일은 KPI 종료일보다 클 수 없습니다.")
        st.stop()

    kpi_mask = (
        (order_df["date"].dt.date >= kpi_start_date)
        & (order_df["date"].dt.date <= kpi_end_date)
    )
    kpi_filtered_df = order_df[kpi_mask].copy()
    if kpi_filtered_df.empty:
        st.warning("선택한 KPI 조건에 맞는 데이터가 없습니다.")
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

    st.markdown("### KPI Metric")
    compare_info = f"vs {prev_start}~{prev_end}"
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric(
        f"기간 주문 금액 ({compare_info})",
        f"{whole['total_amount']:,.0f}원",
        _prev_delta(whole["total_amount"], prev_m["total_amount"]),
    )
    kpi2.metric(
        f"주문 건수 ({compare_info})",
        f"{int(whole['order_count']):,}",
        _prev_delta(whole["order_count"], prev_m["order_count"]),
    )
    kpi3.metric(
        f"판매 수량 합계 ({compare_info})",
        f"{whole['total_quantity']:,.0f}",
        _prev_delta(whole["total_quantity"], prev_m["total_quantity"]),
    )
    kpi4.metric(
        f"고객 수 ({compare_info})",
        f"{int(whole['customer_count']):,}",
        _prev_delta(whole["customer_count"], prev_m["customer_count"]),
    )

    st.metric(
        f"최근7일 평균 일매출 ({compare_info})",
        f"{expected_sales:,.0f}원",
        _prev_delta(expected_sales, prev_expected_sales),
    )

    st.markdown("")
    st.subheader("KPI 일자 테이블 (최근 7일)")
    kpi_daily_start = default_end - timedelta(days=6)
    kpi_daily_mask = (
        (order_df["date"].dt.date >= kpi_daily_start)
        & (order_df["date"].dt.date <= default_end)
    )
    kpi_daily_table = order_df[kpi_daily_mask].copy()
    kpi_daily_table["date"] = kpi_daily_table["date"].dt.date
    daily_kpi = (
        kpi_daily_table.groupby("date", as_index=False)
        .agg(
            order_count=(
                "order_id",
                lambda s: s.astype(str).replace("", pd.NA).dropna().nunique(),
            ),
            total_amount=("amount", "sum"),
            total_quantity=("quantity", "sum"),
        )
        .sort_values("date", ascending=False)
    )
    total_row = pd.DataFrame(
        [
            {
                "date": "합계",
                "order_count": daily_kpi["order_count"].sum(),
                "total_amount": daily_kpi["total_amount"].sum(),
                "total_quantity": daily_kpi["total_quantity"].sum(),
            }
        ]
    )
    daily_kpi = pd.concat([daily_kpi, total_row], ignore_index=True)
    show_data_grid(daily_kpi)

    st.subheader("토/일/월 결제 합산 확인 (결제일 기준)")
    weekend_summary = build_weekend_settlement_summary(order_df, kpi_start_date, kpi_end_date)
    if weekend_summary.empty:
        st.caption("선택한 KPI 기간에 토/일 결제 데이터가 없습니다.")
    else:
        show_data_grid(weekend_summary)

    st.markdown("---")
    st.markdown("## 분석 영역")
    ana_col1, ana_col2 = st.columns(2)
    with ana_col1:
        analysis_start_date = st.date_input("분석 시작일", value=default_start, key="analysis_start_date")
    with ana_col2:
        analysis_end_date = st.date_input("분석 종료일", value=default_end, key="analysis_end_date")
    buyer_name_search = st.text_input("구매자명 검색", "", key="main_buyer_search")

    if analysis_start_date > analysis_end_date:
        st.error("분석 시작일은 분석 종료일보다 클 수 없습니다.")
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
        st.warning("선택한 분석 조건에 맞는 데이터가 없습니다.")
        st.stop()

    tab_product_sales, tab_option_sales = st.tabs(["상품별 매출", "옵션별 매출"])

    with tab_product_sales:
        st.subheader("상품별 매출")
        product_summary = (
            analysis_filtered_df.groupby("product_name", as_index=False)
            .agg(
                total_amount=("amount", "sum"),
                quantity=("quantity", "sum"),
                real_quantity=("real_quantity", "sum"),
                order_count=(
                    "order_id",
                    lambda s: s.astype(str).replace("", pd.NA).dropna().nunique(),
                ),
            )
            .sort_values("total_amount", ascending=False)
        )
        total_amount = float(product_summary["total_amount"].sum())
        top_sales = float(product_summary.iloc[0]["total_amount"]) if not product_summary.empty else 0.0
        ratio = (top_sales / total_amount) if total_amount > 0 else 0.0
        st.metric("TOP 상품 매출 비중(상품명 기준)", f"{ratio * 100:.1f}%")
        show_data_grid(product_summary)

    with tab_option_sales:
        st.subheader("옵션별 매출")
        option_name_summary = (
            analysis_filtered_df.groupby("option_name", as_index=False)
            .agg(
                weight_unit=(
                    "weight_unit",
                    lambda s: next((x for x in s if str(x).strip()), ""),
                ),
                total_amount=("amount", "sum"),
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
        total_amount = float(option_name_summary["total_amount"].sum())
        top_sales = (
            float(option_name_summary.iloc[0]["total_amount"])
            if not option_name_summary.empty
            else 0.0
        )
        ratio = (top_sales / total_amount) if total_amount > 0 else 0.0
        st.metric("TOP 상품 매출 비중(옵션명 기준)", f"{ratio * 100:.1f}%")
        show_data_grid(option_name_summary)

    st.subheader("상세 데이터")
    show_data_grid(analysis_filtered_df)


if _require_login():
    main_content()
