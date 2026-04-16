from __future__ import annotations

import re
import os
import sys
from datetime import date
from pathlib import Path

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

DEFAULT_API_BASE_URL = "https://navermodiba-production.up.railway.app"
REQUIRED_COLUMNS = [
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


def format_krw(value: float | int) -> str:
    return f"{float(value):,.0f}원"


def extract_multiplier(option_name: str) -> int:
    if not isinstance(option_name, str) or not option_name.strip():
        return 1
    patterns = [r"x\s*(\d+)", r"(\d+)\s*개"]
    for pattern in patterns:
        match = re.search(pattern, option_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 1


def product_group(product_name: str) -> str:
    text = product_name if isinstance(product_name, str) else ""
    if "닭가슴살" in text:
        return "닭가슴살"
    if "닭안심" in text:
        return "닭안심"
    return "기타"


def fetch_order_data(base_url: str) -> pd.DataFrame:
    response = httpx.get(
        f"{base_url}/analytics/orders-raw",
        timeout=20,
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
    df["short_address"] = df["address"].astype(str).str.slice(0, 20)
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
        if st.button("🔄 새로고침"):
            st.rerun()

    with st.sidebar:
        st.header("API 설정")
        api_base_url = st.text_input("FastAPI 기본 URL", value=DEFAULT_API_BASE_URL).rstrip("/")

    try:
        raw_df = fetch_order_data(api_base_url)
        order_df = normalize_order_data(raw_df)
    except Exception as exc:
        st.error(f"주문 데이터 조회 실패: {exc}")
        st.stop()

    if order_df.empty:
        st.warning(
            "API 응답에 상세 주문 데이터가 없습니다. "
            "`/analytics/orders-by-date` 응답 구조에 필요한 필드가 포함되어야 합니다."
        )
        st.stop()

    with st.sidebar:
        st.header("조회 필터")
        today_date = date.today()
        start_date = st.date_input("시작일", value=today_date, key="main_start_date")
        end_date = st.date_input("종료일", value=today_date, key="main_end_date")
        buyer_name_search = st.text_input("구매자명 검색", "", key="main_buyer_search")

    if start_date > end_date:
        st.error("시작일은 종료일보다 클 수 없습니다.")
        st.stop()

    period_mask = (
        (order_df["date"].dt.date >= start_date)
        & (order_df["date"].dt.date <= end_date)
    )
    filtered_df = order_df[period_mask].copy()
    if buyer_name_search:
        filtered_df = filtered_df[
            filtered_df["buyer_name"].astype(str).str.contains(
                buyer_name_search, case=False, na=False
            )
        ]

    if filtered_df.empty:
        st.warning("선택한 조건에 맞는 데이터가 없습니다.")
        st.stop()

    def _delta_rate(current: float, previous: float) -> str:
        if previous == 0:
            if current == 0:
                return "0.0%"
            return "+100.0%"
        rate = ((current - previous) / previous) * 100
        return f"{'+' if rate >= 0 else ''}{rate:.1f}%"

    today = pd.Timestamp.now().normalize().date()
    yesterday = today - pd.Timedelta(days=1)

    today_df = order_df[order_df["date"].dt.date == today]
    prev_df = order_df[order_df["date"].dt.date == yesterday]

    today_sales = float(today_df["amount"].sum())
    prev_sales = float(prev_df["amount"].sum())
    today_real_quantity = float(today_df["real_quantity"].sum())
    prev_real_quantity = float(prev_df["real_quantity"].sum())
    total_customers = int(filtered_df["buyer_id"].astype(str).nunique())
    prev_customers = int(prev_df["buyer_id"].astype(str).nunique())

    recent_7d = order_df[
        order_df["date"].dt.date >= (today - pd.Timedelta(days=6))
    ]
    recent_qty = float(recent_7d["real_quantity"].sum())
    avg_unit_price = (
        float(recent_7d["amount"].sum() / recent_qty)
        if recent_qty > 0
        else 0.0
    )
    expected_revenue = today_real_quantity * avg_unit_price
    prev_expected_revenue = prev_real_quantity * avg_unit_price

    st.markdown("### KPI 요약")
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("오늘 주문 금액", format_krw(today_sales), _delta_rate(today_sales, prev_sales))
    kpi2.metric(
        "오늘 실판매수량",
        f"{today_real_quantity:,.0f}",
        _delta_rate(today_real_quantity, prev_real_quantity),
    )
    kpi3.metric(
        "총 고객수",
        f"{total_customers:,}",
        _delta_rate(float(total_customers), float(prev_customers)),
    )
    kpi4.metric(
        "실제예상매출액",
        format_krw(expected_revenue),
        _delta_rate(expected_revenue, prev_expected_revenue),
    )

    st.markdown("")
    with st.container(border=True):
        st.subheader("시간단위 주문 금액 추이")
        hourly_df = filtered_df.copy()
        hourly_df["hour_of_day"] = (
            hourly_df["payment_date"].dt.hour.fillna(-1).astype(int)
        )
        hourly_amount = (
            hourly_df[hourly_df["hour_of_day"] >= 0]
            .groupby("hour_of_day", as_index=False)
            .agg(total_amount=("amount", "sum"))
            .sort_values("hour_of_day")
        )
        if hourly_amount.empty:
            st.info("시간단위 차트 데이터가 없습니다.")
        else:
            st.bar_chart(hourly_amount, x="hour_of_day", y="total_amount")

    st.markdown("")
    st.subheader("상세 분석")
    tab_product, tab_option, tab_option_daily = st.tabs(
        ["기존칼럼유지 - 상품 분석", "기존칼럼유지 - 옵션 분석", "기존칼럼유지 - 옵션 일자"]
    )

    with tab_product:
        product_summary = (
            filtered_df.groupby("product_name", as_index=False)
            .agg(
                total_quantity=("real_quantity", "sum"),
                total_amount=("amount", "sum"),
            )
            .sort_values("total_amount", ascending=False)
        )
        show_data_grid(product_summary)

    with tab_option:
        option_summary = (
            filtered_df.groupby("option_name", as_index=False)
            .agg(
                order_count=("quantity", "sum"),
                real_quantity=("real_quantity", "sum"),
                total_amount=("amount", "sum"),
            )
            .sort_values("total_amount", ascending=False)
        )
        show_data_grid(option_summary)

    with tab_option_daily:
        option_daily = (
            filtered_df.groupby(
                [filtered_df["date"].dt.date, "option_name"], as_index=False
            )
            .agg(
                order_count=("quantity", "sum"),
                real_quantity=("real_quantity", "sum"),
                total_amount=("amount", "sum"),
            )
            .rename(columns={"date": "date"})
            .sort_values(["date", "total_amount"], ascending=[False, False])
        )
        show_data_grid(option_daily)

    st.markdown("")
    st.subheader("원본 주문 데이터")
    st.dataframe(filtered_df, use_container_width=True, hide_index=True)


if _require_login():
    main_content()
