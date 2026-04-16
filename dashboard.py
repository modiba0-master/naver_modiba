import re
import os
from datetime import date

import httpx
import pandas as pd
import streamlit as st

from streamlit_app.services.data_grid import show_data_grid

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


def normalize_order_data(frame: pd.DataFrame) -> pd.DataFrame:
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


def _get_dashboard_password() -> str:
    """로컬은 st.secrets 우선, 그 외 환경변수 PASSWORD를 사용."""
    try:
        secret_password = st.secrets.get("PASSWORD")
        if secret_password:
            return str(secret_password)
    except Exception:
        pass
    return str(os.environ.get("PASSWORD", ""))


def _require_login() -> None:
    """로그인 성공 전에는 대시보드 본문 실행을 차단."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    expected_password = _get_dashboard_password()
    if not expected_password:
        st.error("보안 비밀번호가 설정되지 않았습니다. PASSWORD 변수를 설정해 주세요.")
        st.stop()

    if st.session_state.authenticated:
        return

    st.title("네이버 커머스 주문 분석 대시보드")
    st.subheader("로그인")
    input_password = st.text_input("비밀번호", type="password")
    if st.button("로그인", type="primary"):
        if input_password == expected_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


def main_content() -> None:
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

    table_daily = daily_sales.drop(columns=["ma7"], errors="ignore").copy()
    show_data_grid(table_daily)

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
    show_data_grid(group_summary)
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
    show_data_grid(product_summary)
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
    show_data_grid(option_summary)
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
    show_data_grid(option_daily)

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
            customer_detail["buyer_name"].astype(str).str.contains(
                buyer_name_search, case=False, na=False
            )
        ]
    if selected_date:
        customer_detail = customer_detail[
            customer_detail["date"].dt.date == selected_date
        ]

    show_data_grid(customer_detail)


st.set_page_config(page_title="네이버 모디바 대시보드", layout="wide")
_require_login()
main_content()
