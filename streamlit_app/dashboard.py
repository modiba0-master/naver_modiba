from __future__ import annotations

import re
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

from column_map import COLUMN_MAP

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


def _comma_format_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """숫자 dtype 컬럼만 천 단위 콤마 문자열로 변환."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_numeric_dtype(out[c]):
            out[c] = out[c].map(
                lambda x: f"{int(x):,}"
                if pd.notna(x) and not isinstance(x, bool)
                else ""
            )
    return out


def _ensure_dataframe(data: pd.DataFrame | list | dict) -> pd.DataFrame:
    """st.dataframe 직전에 항상 DataFrame으로 통일."""
    if isinstance(data, pd.DataFrame):
        return data
    return pd.DataFrame(data)


def show_summary_table(data: pd.DataFrame | list | dict) -> None:
    """소형 요약(orders/daily_summary 등): st.dataframe으로 표시."""
    show_data_grid(data)


def show_data_grid(data: pd.DataFrame | list | dict) -> None:
    """일반 표: 숫자 콤마 포맷 후 st.dataframe(df, use_container_width=True, hide_index=True)."""
    df = _ensure_dataframe(data)
    df = df.rename(columns=COLUMN_MAP)
    df = df[
        [col for col in dict.fromkeys(COLUMN_MAP.values()) if col in df.columns]
    ]
    df = _comma_format_numeric_columns(df)
    st.dataframe(df, use_container_width=True, hide_index=True)


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
    df = frame.copy()
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

today = pd.Timestamp.now().normalize()

tab_live, tab_date = st.tabs(["📊 오늘 실시간", "📅 일자별 조회"])

with tab_live:
    data: dict[str, int] = {"order_count": 0, "total_sales": 0}
    try:
        data = get_today_summary(date.today())
    except Exception:
        pass
    order_count = int(data.get("order_count") or 0)
    total_sales = int(data.get("total_sales") or 0)

    m1, m2 = st.columns(2)
    m1.metric("주문 수", f"{order_count:,}")
    m2.metric("매출", f"{total_sales:,}원")

    df_today = pd.DataFrame(
        [{"order_count": order_count, "total_sales": total_sales}]
    )
    show_data_grid(df_today)

    # 1) KPI 대시보드
    st.subheader("1) KPI 대시보드")
    today_mask = order_df["date"].dt.normalize() == today
    today_sales = float(order_df.loc[today_mask, "amount"].sum())
    today_real_quantity = float(order_df.loc[today_mask, "real_quantity"].sum())
    total_customers = int(order_df["buyer_id"].astype(str).nunique())
    kpi1, kpi2, kpi3 = st.columns(3)
    kpi1.metric("오늘 매출", format_krw(today_sales))
    kpi2.metric("오늘 실판매수량", f"{today_real_quantity:,.0f}")
    kpi3.metric("총 고객 수", total_customers)

with tab_date:
    query_date = st.date_input("조회일", value=date.today(), key="tab_date_query_day")
    filtered_df = order_df[order_df["date"].dt.date == query_date].copy()
    if filtered_df.empty:
        st.info(f"{query_date} 기준 주문 데이터가 없습니다. 다른 날짜를 선택해 주세요.")

    try:
        ds = get_daily_summary(query_date)
    except Exception:
        ds = {"total_orders": 0, "total_sales": 0}
    if ds["total_orders"] == 0 and ds["total_sales"] == 0:
        st.warning("데이터 없음")
    df_daily = pd.DataFrame(
        [
            {
                "total_orders": int(ds.get("total_orders") or 0),
                "total_sales": int(ds.get("total_sales") or 0),
            }
        ]
    )
    show_summary_table(df_daily)

    # 2) 일별 매출 추이 (선택일 기준)
    st.subheader("2) 일별 매출 추이")
    daily_sales = (
        filtered_df.groupby(filtered_df["date"].dt.date, as_index=False)
        .agg(
            total_amount=("amount", "sum"),
            total_quantity=("real_quantity", "sum"),
        )
        .rename(columns={"date": "date"})
        .sort_values("date")
    )
    daily_sales["ma7"] = daily_sales["total_amount"].rolling(window=7, min_periods=1).mean()

    table_daily = daily_sales.drop(columns=["ma7"], errors="ignore").copy()
    table_daily["total_amount"] = table_daily["total_amount"].apply(format_krw)
    table_daily["total_quantity"] = table_daily["total_quantity"].apply(lambda x: f"{x:,.0f}")
    df = table_daily.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    show_data_grid(df)

    chart_daily = daily_sales.copy()
    chart_daily["date"] = pd.to_datetime(chart_daily["date"])
    if not chart_daily.empty:
        st.line_chart(chart_daily, x="date", y=["total_amount", "ma7"])

    # 3) 상품군 매출
    st.subheader("3) 상품군 매출")
    group_df = filtered_df.copy()
    group_df["product_group"] = group_df["product_name"].apply(product_group)
    group_summary = (
        group_df.groupby("product_group", as_index=False)
        .agg(
            total_quantity=("real_quantity", "sum"),
            total_amount=("amount", "sum"),
        )
        .sort_values("total_amount", ascending=False)
    )
    group_table = group_summary.copy()
    group_table["total_quantity"] = group_table["total_quantity"].apply(lambda x: f"{x:,.0f}")
    group_table["total_amount"] = group_table["total_amount"].apply(format_krw)
    df = group_table.copy()
    show_data_grid(df)
    if not group_summary.empty:
        st.bar_chart(group_summary, x="product_group", y="total_amount")

    # 4) 상품별 매출
    st.subheader("4) 상품별 매출")
    product_summary = (
        filtered_df.groupby("product_name", as_index=False)
        .agg(
            total_quantity=("real_quantity", "sum"),
            total_amount=("amount", "sum"),
        )
        .sort_values("total_amount", ascending=False)
    )
    product_table = product_summary.copy()
    product_table["total_quantity"] = product_table["total_quantity"].apply(lambda x: f"{x:,.0f}")
    product_table["total_amount"] = product_table["total_amount"].apply(format_krw)
    df = product_table.copy()
    show_data_grid(df)
    if not product_summary.empty:
        st.bar_chart(product_summary.head(10), x="product_name", y="total_amount")

    # 5) 옵션별 매출
    st.subheader("5) 옵션별 매출")
    option_summary = (
        filtered_df.groupby("option_name", as_index=False)
        .agg(
            order_count=("quantity", "sum"),
            real_quantity=("real_quantity", "sum"),
            total_amount=("amount", "sum"),
        )
        .sort_values("total_amount", ascending=False)
    )
    option_table = option_summary.copy()
    option_table["order_count"] = option_table["order_count"].apply(lambda x: f"{x:,.0f}")
    option_table["real_quantity"] = option_table["real_quantity"].apply(lambda x: f"{x:,.0f}")
    option_table["total_amount"] = option_table["total_amount"].apply(format_krw)
    df = option_table.copy()
    show_data_grid(df)
    if not option_summary.empty:
        st.bar_chart(option_summary.head(10), x="option_name", y="total_amount")

    # 6) 옵션 일자 상세
    st.subheader("6) 옵션 일자 상세")
    option_daily = (
        filtered_df.groupby([filtered_df["date"].dt.date, "option_name"], as_index=False)
        .agg(
            order_count=("quantity", "sum"),
            real_quantity=("real_quantity", "sum"),
            total_amount=("amount", "sum"),
        )
        .rename(columns={"date": "date"})
        .sort_values(["date", "total_amount"], ascending=[False, False])
    )
    option_daily_table = option_daily.copy()
    option_daily_table["order_count"] = option_daily_table["order_count"].apply(lambda x: f"{x:,.0f}")
    option_daily_table["real_quantity"] = option_daily_table["real_quantity"].apply(
        lambda x: f"{x:,.0f}"
    )
    option_daily_table["total_amount"] = option_daily_table["total_amount"].apply(format_krw)
    df = option_daily_table.copy()
    show_data_grid(df)

    # 7) 고객 상세 테이블 (상단 조회일과 동일)
    st.subheader("7) 고객 상세 테이블")
    buyer_name_search = st.text_input("구매자명 검색", "", key="tab_date_buyer_search")

    customer_detail = filtered_df.copy()
    if buyer_name_search:
        customer_detail = customer_detail[
            customer_detail["buyer_name"].astype(str).str.contains(buyer_name_search, case=False, na=False)
        ]

    detail_table = customer_detail.copy()
    detail_table["date"] = detail_table["date"].dt.strftime("%Y-%m-%d")
    detail_table["payment_date"] = detail_table["payment_date"].dt.strftime("%Y-%m-%d %H:%M:%S")
    detail_table["quantity"] = detail_table["quantity"].apply(lambda x: f"{x:,.0f}")
    detail_table["real_quantity"] = detail_table["real_quantity"].apply(lambda x: f"{x:,.0f}")
    detail_table["amount"] = detail_table["amount"].apply(format_krw)
    df = detail_table.copy()
    show_data_grid(df)
