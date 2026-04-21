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

from services.aggregation_display import format_kst_sales_window

from services.data_grid import show_data_grid, show_summary_table
from services.db import SessionLocal
from services.kpi_from_filtered import (
    delta_rate,
    expected_sales_from_recent_7d,
    kpi_aggregate,
)
from services.kpi_ui import (
    add_avg_ticket_to_daily,
    append_daily_total_row,
    render_kpi_period_header,
    render_seller_db_tips_expander,
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
    # 결제일시: DB는 KST naive(UTC 등은 동기화 시 변환됨). 매출 집계는 `date`(business_date)만 사용.
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
    ok_dates = df["date"].notna()
    df["aggregation_window_kst"] = ""
    df.loc[ok_dates, "aggregation_window_kst"] = (
        df.loc[ok_dates, "date"].dt.date.map(format_kst_sales_window)
    )
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
            for _k in ("last_good_order_df", "kpi_start_date", "kpi_end_date",
                       "analysis_start_date", "analysis_end_date"):
                st.session_state.pop(_k, None)
            st.rerun()

    with st.sidebar:
        st.header("API 설정")
        api_base_url = st.text_input("FastAPI 기본 URL", value=DEFAULT_API_BASE_URL).rstrip("/")
        revenue_basis = st.selectbox(
            "매출 집계 기준",
            options=["payment", "order", "shipping"],
            index=0,
            format_func=lambda x: {
                "payment": "결제 기준 (기본)",
                "order": "주문 기준",
                "shipping": "발송 기준",
            }[x],
            key="revenue_basis_select",
            help="KPI·일자 표는 선택한 영업일(`*_business_date`)로 묶습니다. 발송 미완 주문은 발송 기준에서 제외됩니다.",
        )

    data_loaded_at = ""
    data_loaded_mode = "live"
    try:
        raw_df = fetch_order_data(api_base_url, revenue_basis)
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
    with st.expander("일시·매출 집계 기준 (네이버 API → DB)", expanded=False):
        st.markdown(
            """
1. **결제일시(`payment_date`)** — 네이버 API ISO8601. **이미 KST인 naive `YYYY-MM-DD HH:MM:SS`는 그대로** 저장합니다. **UTC(`Z`)·타임존 있음**은 KST로 변환한 뒤 naive로 저장합니다. (`app.services.sync`: `parse_payment_datetime_string` / `to_kst_naive`)
2. **매출 집계일(`business_date`)** — KST 결제 시각 기준 **00:00~15:59 → 당일, 16:00~23:59 → 익일** 영업일. 동기화 시에만 계산하며, **집계·필터는 항상 `business_date` 컬럼만** 사용합니다 (`DATE(payment_date)` 금지).
3. **불일치 시** — 서버에서 `python scripts/recompute_business_dates.py` 로 `business_date`만 재맞춤합니다.
            """.strip()
        )
    kpi_col1, kpi_col2 = st.columns(2)
    with kpi_col1:
        kpi_start_date = st.date_input(
            "KPI 시작일 (매출 집계일)",
            value=default_start,
            key="kpi_start_date",
        )
    with kpi_col2:
        kpi_end_date = st.date_input(
            "KPI 종료일 (매출 집계일)",
            value=default_end,
            key="kpi_end_date",
        )

    st.caption(
        "KPI는 **영업일**(`business_date`, 16:00 KST 컷) 기준입니다. 실제 결제 시각은 `payment_date`를 보세요."
    )

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

    compare_info = f"vs {prev_start}~{prev_end}"

    with st.container(border=True):
        st.markdown("### 주문·매출 KPI")
        render_kpi_period_header(
            kpi_start_date, kpi_end_date, period_days, whole
        )
        r1a, r1b, r1c, r1d = st.columns(4)
        r1a.metric(
            f"기간 순매출 ({compare_info})",
            f"{whole['total_amount']:,.0f}원",
            _prev_delta(whole["total_amount"], prev_m["total_amount"]),
            help="환불·취소 반영 net_revenue 합계.",
        )
        r1b.metric(
            f"상품주문 건수 ({compare_info})",
            f"{int(whole['order_count']):,}",
            _prev_delta(whole["order_count"], prev_m["order_count"]),
            help="네이버 API 기준 상품주문번호(줄) 단위 건수입니다.",
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
            f"기간 평균 객단가 ({compare_info})",
            f"{whole['avg_order_value']:,.0f}원",
            _prev_delta(whole["avg_order_value"], prev_m["avg_order_value"]),
            help="기간 주문 금액 ÷ 상품주문 건수.",
        )
        r2b.metric(
            f"최근7일 평균 일매출 ({compare_info})",
            f"{expected_sales:,.0f}원",
            _prev_delta(expected_sales, prev_expected_sales),
            help="선택 기간 내 일자별 매출의 최근 7일 평균.",
        )
        r2c.metric(
            "선택 일수",
            f"{period_days}일",
            None,
        )

    render_seller_db_tips_expander()

    st.markdown("")
    st.subheader("최근 7일 일자별 매출")
    st.caption(
        "네이버에서 불러온 주문을 **매출 집계일**로 묶은 금액입니다. "
        "상세 주문 표의 「매출집계일 안내」는 같은 집계일을 설명하는 문구입니다."
    )
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

    chart_for_plot = daily_kpi.sort_values("date", ascending=True)
    if not chart_for_plot.empty:
        st.caption("일자별 순매출 (막대)")
        st.bar_chart(
            chart_for_plot.set_index("date_label")[["total_amount"]],
            use_container_width=True,
            height=260,
        )

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
    st.subheader("시간대별 순매출 (결제 시각 기준)")
    st.caption(
        "시간 버킷은 `payment_date`의 시(hour)만 사용합니다. "
        "기간은 위 최근 7일과 동일한 매출 집계일 범위입니다."
    )
    try:
        hr_resp = httpx.get(
            f"{api_base_url}/analytics/revenue-by-hour",
            params={
                "start_date": kpi_daily_start.isoformat(),
                "end_date": default_end.isoformat(),
            },
            timeout=30,
        )
        hr_resp.raise_for_status()
        hitems = hr_resp.json().get("items") or []
        if hitems:
            hdf = pd.DataFrame(hitems)
            hdf["revenue"] = pd.to_numeric(hdf["revenue"], errors="coerce").fillna(0)
            st.bar_chart(
                hdf.set_index("hour")[["revenue"]],
                use_container_width=True,
                height=220,
            )
        else:
            st.info("시간대별 데이터가 없습니다.")
    except Exception as exc:
        st.warning(f"시간대별 API 조회 실패: {exc}")

    st.subheader("요일 × 시간 순매출 (결제 기준)")
    st.caption("요일은 `payment_business_date`, 시는 `payment_date` 기준입니다.")
    try:
        hm_resp = httpx.get(
            f"{api_base_url}/analytics/revenue-heatmap",
            params={
                "start_date": kpi_daily_start.isoformat(),
                "end_date": default_end.isoformat(),
            },
            timeout=30,
        )
        hm_resp.raise_for_status()
        hm_items = hm_resp.json().get("items") or []
        if hm_items:
            hm = pd.DataFrame(hm_items)
            hm["revenue"] = pd.to_numeric(hm["revenue"], errors="coerce").fillna(0)
            pivot = hm.pivot_table(
                index="day_of_week",
                columns="hour",
                values="revenue",
                aggfunc="sum",
                fill_value=0,
            )
            dow_labels = ["월", "화", "수", "목", "금", "토", "일"]
            pivot.index = [dow_labels[i] for i in pivot.index]
            st.dataframe(pivot, use_container_width=True)
        else:
            st.info("히트맵 데이터가 없습니다.")
    except Exception as exc:
        st.warning(f"히트맵 API 조회 실패: {exc}")

    st.markdown("---")
    st.markdown("## 분석 영역")
    ana_col1, ana_col2 = st.columns(2)
    with ana_col1:
        analysis_start_date = st.date_input(
            "분석 시작일 (매출 집계일)",
            value=default_start,
            key="analysis_start_date",
        )
    with ana_col2:
        analysis_end_date = st.date_input(
            "분석 종료일 (매출 집계일)",
            value=default_end,
            key="analysis_end_date",
        )
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

    _rev_col = (
        "net_revenue" if "net_revenue" in analysis_filtered_df.columns else "amount"
    )

    tab_product_sales, tab_option_sales = st.tabs(["상품별 매출", "옵션별 매출"])

    with tab_product_sales:
        st.subheader("상품별 매출")
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
