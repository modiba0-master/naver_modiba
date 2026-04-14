from datetime import date

import httpx
import pandas as pd
import streamlit as st


DEFAULT_API_BASE_URL = "https://navermodiba-production.up.railway.app"


def format_krw(value: float | int) -> str:
    return f"{value:,.0f}원"


def get_json(url: str, params: dict | None = None) -> dict:
    response = httpx.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: dict | None = None) -> dict:
    response = httpx.post(url, json=payload or {}, timeout=20)
    response.raise_for_status()
    return response.json()


st.set_page_config(page_title="네이버 모디바 대시보드", layout="wide")
st.title("네이버 커머스 주문 분석 대시보드")

with st.sidebar:
    st.header("API 설정")
    api_base_url = st.text_input("FastAPI 기본 URL", value=DEFAULT_API_BASE_URL).rstrip("/")
    st.caption("예시: https://navermodiba-production.up.railway.app")

st.subheader("1) 주문 동기화")
if st.button("주문 동기화 실행", type="primary"):
    try:
        sync_result = post_json(f"{api_base_url}/analytics/sync-orders")
        st.success(f"신규 저장 주문 수: {sync_result.get('inserted_count', 0)}")
    except Exception as exc:
        st.error(f"주문 동기화 실패: {exc}")

st.divider()
st.subheader("2) 분석 필터")

filter_col1, filter_col2 = st.columns(2)
with filter_col1:
    use_start_date = st.checkbox("시작일 사용", value=False)
    start_date = st.date_input("시작일", value=date.today(), disabled=not use_start_date)
with filter_col2:
    use_end_date = st.checkbox("종료일 사용", value=False)
    end_date = st.date_input("종료일", value=date.today(), disabled=not use_end_date)


def to_query_params(start: date | None, end: date | None) -> dict:
    params: dict[str, str] = {}
    if use_start_date and start:
        params["start_date"] = f"{start.isoformat()}T00:00:00"
    if use_end_date and end:
        params["end_date"] = f"{end.isoformat()}T23:59:59"
    return params


params = to_query_params(start_date, end_date)

orders_col, margin_col = st.columns(2)

with orders_col:
    st.markdown("### 날짜별 주문 집계")
    try:
        orders_by_date = get_json(f"{api_base_url}/analytics/orders-by-date", params=params)
        items = orders_by_date.get("items", [])
        if items:
            orders_df = pd.DataFrame(items)
            orders_df["order_date"] = pd.to_datetime(orders_df["order_date"], errors="coerce")
            orders_df = orders_df.dropna(subset=["order_date"])

            start_cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=7)
            filtered_df = orders_df[orders_df["order_date"] >= start_cutoff].copy()
            filtered_df = filtered_df.sort_values("order_date")

            st.markdown("#### Last 7 Days Orders")
            if filtered_df.empty:
                st.info("최근 7일 주문 데이터가 없습니다.")
            else:
                display_df = filtered_df.copy()
                display_df["order_date"] = display_df["order_date"].dt.strftime("%Y-%m-%d")
                if "total_amount" in display_df.columns:
                    display_df["total_amount"] = display_df["total_amount"].apply(
                        lambda value: format_krw(float(value))
                    )
                st.dataframe(display_df, use_container_width=True)

                line_df = filtered_df.copy()
                line_df["order_date"] = line_df["order_date"].dt.strftime("%Y-%m-%d")
                st.line_chart(line_df, x="order_date", y="order_count")
        else:
            st.info("현재 필터 조건에 해당하는 주문 데이터가 없습니다.")
    except Exception as exc:
        st.error(f"주문 집계 조회 실패: {exc}")

with margin_col:
    st.markdown("### 마진 요약")
    try:
        margin = get_json(f"{api_base_url}/analytics/margin", params=params)
        kpi_col1, kpi_col2 = st.columns(2)
        with kpi_col1:
            st.metric("총 매출", format_krw(float(margin.get("total_revenue", 0))))
            st.metric("총 원가", format_krw(float(margin.get("total_cost", 0))))
            st.metric("배송비", format_krw(float(margin.get("total_shipping", 0))))
        with kpi_col2:
            st.metric("총 마진", format_krw(float(margin.get("total_margin", 0))))
            st.metric("마진율", f"{float(margin.get('margin_rate', 0)):.2f}%")
    except Exception as exc:
        st.error(f"마진 분석 조회 실패: {exc}")

st.divider()
st.subheader("3) 고객 등급 분류")


def classify_tier(order_count: int) -> str:
    if order_count >= 5:
        return "VIP"
    if 3 <= order_count < 5:
        return "Gold"
    if 2 <= order_count < 3:
        return "Silver"
    return "미분류"


def build_customer_tier_table(items: list[dict]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame(columns=["customer_id", "order_count", "tier"])

    frame = pd.DataFrame(items)
    if "customer_id" not in frame.columns:
        return pd.DataFrame(columns=["customer_id", "order_count", "tier"])

    if "order_count" in frame.columns:
        grouped = frame.groupby("customer_id", as_index=False)["order_count"].sum()
    else:
        grouped = frame.groupby("customer_id", as_index=False).size().rename(
            columns={"size": "order_count"}
        )

    grouped["tier"] = grouped["order_count"].apply(classify_tier)
    return grouped.sort_values(by=["order_count", "customer_id"], ascending=[False, True])


try:
    orders_by_date = get_json(f"{api_base_url}/analytics/orders-by-date", params=params)
    orders_items = orders_by_date.get("items", [])
    customer_tier_df = build_customer_tier_table(orders_items)

    if customer_tier_df.empty:
        st.warning(
            "현재 `/analytics/orders-by-date` 응답에 `customer_id`가 없습니다. "
            "고객 등급 분류를 위해 고객 단위 데이터가 필요합니다."
        )
    else:
        silver_count = int((customer_tier_df["tier"] == "Silver").sum())
        gold_count = int((customer_tier_df["tier"] == "Gold").sum())
        vip_count = int((customer_tier_df["tier"] == "VIP").sum())
        total_customers = int(len(customer_tier_df))

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("전체 고객 수", total_customers)
        kpi2.metric("Silver", silver_count)
        kpi3.metric("Gold", gold_count)
        kpi4.metric("VIP", vip_count)

        st.markdown("#### 등급 분포")
        tier_counts = (
            customer_tier_df.groupby("tier", as_index=False)
            .size()
            .rename(columns={"size": "customer_count"})
            .sort_values(by="customer_count", ascending=False)
        )
        st.bar_chart(tier_counts, x="tier", y="customer_count")

        st.markdown("#### 고객 등급 테이블")
        st.dataframe(
            customer_tier_df.rename(
                columns={
                    "customer_id": "고객 ID",
                    "order_count": "주문 수",
                    "tier": "등급",
                }
            ),
            use_container_width=True,
        )

    st.markdown("#### 등급 혜택 안내")
    benefit_col1, benefit_col2, benefit_col3, benefit_col4 = st.columns(4)
    benefit_col1.info("Silver\n\n- 1,000원 쿠폰")
    benefit_col2.info("Gold\n\n- 1,500원 쿠폰")
    benefit_col3.info("VIP\n\n- 3,000원 + 배송비 쿠폰")
    benefit_col4.info("VVIP\n\n- 플레이스홀더 (추후 로직 추가 예정)")
except Exception as exc:
    st.error(f"고객 등급 섹션 로딩 실패: {exc}")
