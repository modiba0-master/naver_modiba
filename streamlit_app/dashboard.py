import re
from datetime import date

import httpx
import pandas as pd
import streamlit as st

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
st.title("네이버 커머스 주문 분석 대시보드")

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

# 2) 일별 매출 추이
st.subheader("2) 일별 매출 추이")
daily_sales = (
    order_df.groupby(order_df["date"].dt.date, as_index=False)
    .agg(
        total_amount=("amount", "sum"),
        total_quantity=("real_quantity", "sum"),
    )
    .rename(columns={"date": "date"})
    .sort_values("date")
)
daily_sales["ma7"] = daily_sales["total_amount"].rolling(window=7, min_periods=1).mean()

table_daily = daily_sales.copy()
table_daily["total_amount"] = table_daily["total_amount"].apply(format_krw)
table_daily["total_quantity"] = table_daily["total_quantity"].apply(lambda x: f"{x:,.0f}")
st.dataframe(table_daily[["date", "total_amount", "total_quantity"]], width="stretch")

chart_daily = daily_sales.copy()
chart_daily["date"] = pd.to_datetime(chart_daily["date"])
st.line_chart(chart_daily, x="date", y=["total_amount", "ma7"])

# 3) 상품군 매출
st.subheader("3) 상품군 매출")
group_df = order_df.copy()
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
st.dataframe(group_table, width="stretch")
st.bar_chart(group_summary, x="product_group", y="total_amount")

# 4) 상품별 매출
st.subheader("4) 상품별 매출")
product_summary = (
    order_df.groupby("product_name", as_index=False)
    .agg(
        total_quantity=("real_quantity", "sum"),
        total_amount=("amount", "sum"),
    )
    .sort_values("total_amount", ascending=False)
)
product_table = product_summary.copy()
product_table["total_quantity"] = product_table["total_quantity"].apply(lambda x: f"{x:,.0f}")
product_table["total_amount"] = product_table["total_amount"].apply(format_krw)
st.dataframe(product_table, width="stretch")
st.bar_chart(product_summary.head(10), x="product_name", y="total_amount")

# 5) 옵션별 매출
st.subheader("5) 옵션별 매출")
option_summary = (
    order_df.groupby("option_name", as_index=False)
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
st.dataframe(option_table, width="stretch")
st.bar_chart(option_summary.head(10), x="option_name", y="total_amount")

# 6) 옵션 일자 상세
st.subheader("6) 옵션 일자 상세")
option_daily = (
    order_df.groupby([order_df["date"].dt.date, "option_name"], as_index=False)
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
st.dataframe(option_daily_table, width="stretch")

# 7) 고객 상세 테이블
st.subheader("7) 고객 상세 테이블")
f_col1, f_col2 = st.columns(2)
with f_col1:
    buyer_name_search = st.text_input("구매자명 검색", "")
with f_col2:
    selected_date = st.date_input("집계일 필터(단일일)", value=None)

customer_detail = order_df.copy()
if buyer_name_search:
    customer_detail = customer_detail[
        customer_detail["buyer_name"].astype(str).str.contains(buyer_name_search, case=False, na=False)
    ]
if selected_date:
    customer_detail = customer_detail[
        customer_detail["date"].dt.date == selected_date
    ]

detail_cols = [
    "date",
    "payment_date",
    "buyer_name",
    "buyer_id",
    "receiver_name",
    "short_address",
    "product_name",
    "option_name",
    "quantity",
    "real_quantity",
    "amount",
]
detail_table = customer_detail[detail_cols].copy()
detail_table["date"] = detail_table["date"].dt.strftime("%Y-%m-%d")
detail_table["payment_date"] = detail_table["payment_date"].dt.strftime("%Y-%m-%d %H:%M:%S")
detail_table["quantity"] = detail_table["quantity"].apply(lambda x: f"{x:,.0f}")
detail_table["real_quantity"] = detail_table["real_quantity"].apply(lambda x: f"{x:,.0f}")
detail_table["amount"] = detail_table["amount"].apply(format_krw)
st.dataframe(detail_table, width="stretch")
