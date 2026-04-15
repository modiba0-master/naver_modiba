from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from services.db import get_engine
from services.queries import (
    get_main_kpis,
    get_option_analysis,
    get_product_analysis,
    get_time_analysis,
    get_top_products,
)

WEEKDAY_LABELS = {
    0: "월",
    1: "화",
    2: "수",
    3: "목",
    4: "금",
    5: "토",
    6: "일",
}


def format_krw(value: float | int) -> str:
    return f"{float(value):,.0f}원"


@st.cache_resource
def load_engine():
    return get_engine()


@st.cache_data(ttl=120)
def load_main_kpis(target_date: date) -> pd.DataFrame:
    return get_main_kpis(load_engine(), target_date)


@st.cache_data(ttl=120)
def load_top_products(target_date: date) -> pd.DataFrame:
    return get_top_products(load_engine(), target_date, limit=5)


@st.cache_data(ttl=120)
def load_product_analysis(start_date: date | None, end_date: date | None) -> pd.DataFrame:
    return get_product_analysis(load_engine(), start_date, end_date)


@st.cache_data(ttl=120)
def load_option_analysis(start_date: date | None, end_date: date | None) -> pd.DataFrame:
    return get_option_analysis(load_engine(), start_date, end_date)


@st.cache_data(ttl=120)
def load_time_analysis(start_date: date | None, end_date: date | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    return get_time_analysis(load_engine(), start_date, end_date)


def render_main_dashboard(today: date) -> None:
    st.subheader("메인 대시보드")
    kpi_df = load_main_kpis(today)
    top_df = load_top_products(today)

    if kpi_df.empty:
        st.info("KPI 데이터가 없습니다.")
        return

    row = kpi_df.iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("오늘 매출", format_krw(row["today_revenue"]))
    c2.metric("총 주문수", f"{int(row['total_orders']):,}")
    c3.metric("총 이익", format_krw(row["total_profit"]))

    st.markdown("### Top 5 상품")
    if top_df.empty:
        st.info("오늘 기준 상품 데이터가 없습니다.")
    else:
        top_df = top_df.rename(columns={"product_name": "상품명", "revenue": "매출"})
        top_df["매출"] = top_df["매출"].apply(format_krw)
        st.dataframe(top_df, use_container_width=True, hide_index=True)


def render_product_analysis(start_date: date | None, end_date: date | None) -> None:
    st.subheader("상품 분석")
    df = load_product_analysis(start_date, end_date)
    if df.empty:
        st.info("상품 분석 데이터가 없습니다.")
        return

    summary = (
        df.groupby("product_name", as_index=False)
        .agg(revenue=("revenue", "sum"), profit=("profit", "sum"), orders=("orders", "sum"))
        .sort_values("revenue", ascending=False)
    )

    col1, col2 = st.columns(2)
    with col1:
        fig_rev = px.bar(summary, x="product_name", y="revenue", title="상품별 매출", labels={"product_name": "상품명", "revenue": "매출"})
        st.plotly_chart(fig_rev, use_container_width=True)
    with col2:
        fig_profit = px.bar(summary, x="product_name", y="profit", title="상품별 이익", labels={"product_name": "상품명", "profit": "이익"})
        st.plotly_chart(fig_profit, use_container_width=True)

    trend = (
        df.groupby("order_date", as_index=False)
        .agg(revenue=("revenue", "sum"), profit=("profit", "sum"))
        .sort_values("order_date")
    )
    fig_trend = px.line(
        trend,
        x="order_date",
        y=["revenue", "profit"],
        title="일자별 추이",
        labels={"order_date": "날짜", "value": "금액", "variable": "지표"},
    )
    st.plotly_chart(fig_trend, use_container_width=True)


def render_option_analysis(start_date: date | None, end_date: date | None) -> None:
    st.subheader("옵션 분석")
    df = load_option_analysis(start_date, end_date)
    if df.empty:
        st.info("옵션 분석 데이터가 없습니다.")
        return

    df = df.sort_values("orders", ascending=False)
    df["cancel_rate_pct"] = (df["cancel_rate"] * 100).round(2)

    c1, c2 = st.columns(2)
    with c1:
        fig_orders = px.bar(df, x="option_name", y="orders", title="옵션별 주문수", labels={"option_name": "옵션명", "orders": "주문수"})
        st.plotly_chart(fig_orders, use_container_width=True)
    with c2:
        fig_cancel = px.bar(
            df,
            x="option_name",
            y="cancel_rate_pct",
            title="옵션별 취소율(%)",
            labels={"option_name": "옵션명", "cancel_rate_pct": "취소율(%)"},
        )
        st.plotly_chart(fig_cancel, use_container_width=True)

    risky = df[df["cancel_rate"] > 0.10].copy()
    st.markdown("### 위험 옵션 (취소율 > 10%)")
    if risky.empty:
        st.success("위험 옵션이 없습니다.")
    else:
        risky = risky.rename(
            columns={
                "option_name": "옵션명",
                "orders": "주문수",
                "cancel_count": "취소건수",
                "cancel_rate_pct": "취소율(%)",
            }
        )
        st.dataframe(risky[["옵션명", "주문수", "취소건수", "취소율(%)"]], use_container_width=True, hide_index=True)


def render_time_analysis(start_date: date | None, end_date: date | None) -> None:
    st.subheader("시간 분석")
    hour_df, weekday_df = load_time_analysis(start_date, end_date)

    col1, col2 = st.columns(2)

    with col1:
        if hour_df.empty:
            st.info("시간대 데이터가 없습니다.")
        else:
            fig_hour = px.bar(
                hour_df,
                x="hour_of_day",
                y="orders",
                title="시간대별 주문수",
                labels={"hour_of_day": "시간(시)", "orders": "주문수"},
            )
            st.plotly_chart(fig_hour, use_container_width=True)

    with col2:
        if weekday_df.empty:
            st.info("요일 데이터가 없습니다.")
        else:
            weekday_df = weekday_df.copy()
            weekday_df["weekday"] = weekday_df["weekday_num"].map(WEEKDAY_LABELS).fillna("기타")
            fig_weekday = px.bar(
                weekday_df,
                x="weekday",
                y="orders",
                title="요일별 주문수",
                labels={"weekday": "요일", "orders": "주문수"},
            )
            st.plotly_chart(fig_weekday, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="이커머스 분석 대시보드", layout="wide")
    st.title("이커머스 분석 대시보드")

    with st.sidebar:
        st.header("네비게이션")
        page = st.radio(
            "페이지",
            ["Main Dashboard", "Product Analysis", "Option Analysis", "Time Analysis"],
            index=0,
        )
        st.markdown("---")
        today = date.today()
        default_start = today - timedelta(days=30)
        start_date = st.date_input("시작일", value=default_start)
        end_date = st.date_input("종료일", value=today)
        st.caption("집계 기준: 영업일 16:00 컷오프, 금요일 16:00 이후 주문은 월요일 매출로 귀속")
        st.caption("판매관리 집계 상태: 신규주문, 배송준비")

    if start_date and end_date and start_date > end_date:
        st.error("시작일은 종료일보다 클 수 없습니다.")
        return

    if page == "Main Dashboard":
        render_main_dashboard(today=end_date if end_date else today)
    elif page == "Product Analysis":
        render_product_analysis(start_date, end_date)
    elif page == "Option Analysis":
        render_option_analysis(start_date, end_date)
    elif page == "Time Analysis":
        render_time_analysis(start_date, end_date)


if __name__ == "__main__":
    main()

