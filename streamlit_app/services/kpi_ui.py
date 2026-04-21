"""KPI 영역 UI 헬퍼 — 네이버 주문 DataFrame 가공·표시만 담당 (백엔드·DB 스키마 변경 없음)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

# 판매자·운영자가 DB를 손댈 때 참고할 수 있는 제안 (대시보드 문구로만 제공)
SELLER_DB_OPTIMIZATION_MD = """
아래는 **일자별 매출·상품 분석**을 빠르게 하기 위한 DB 설계 **권장안**입니다. (이 앱만으로는 스키마를 바꾸지 않습니다.)

| 구분 | 제안 |
|------|------|
| **집계 키** | `business_date`만 사용 (`DATE(payment_date)`·SQL 재계산 금지). 인덱스로 기간 `SUM(amount)` 최적화 |
| **일별 롤업** | `orders` 풀스캔 대신 `daily_summary` 등 **일·상품 단위 집계 테이블** 유지 또는 야간 배치로 갱신 |
| **주문 묶음** | `content_order_no`(주문번호) 인덱스로 동일 결제·다중 상품줄 분석 |
| **라인 식별** | `order_id`(상품주문번호) UNIQUE로 중복 방지·동기화 upsert |
| **시계열** | 대시보드에서 자주 쓰는 필터 `(business_date, order_status)` 복합 인덱스 검토 |

※ 네이버 API 원본은 **상품주문 단위**이므로, 판매자 관점 “주문 1건” 집계가 필요하면 `content_order_no` 기준으로 한 번 더 묶어야 합니다.
"""


def render_seller_db_tips_expander() -> None:
    with st.expander("판매자용 · 일자별 DB 설계 제안 (참고)", expanded=False):
        st.markdown(SELLER_DB_OPTIMIZATION_MD)


def add_avg_ticket_to_daily(daily_kpi: pd.DataFrame) -> pd.DataFrame:
    """일자별 평균 객단가(주문금액 ÷ 상품주문 건수)."""
    out = daily_kpi.copy()
    oc = pd.to_numeric(out["order_count"], errors="coerce").fillna(0)
    ta = pd.to_numeric(out["total_amount"], errors="coerce").fillna(0.0)
    out["avg_ticket"] = 0.0
    m = oc > 0
    out.loc[m, "avg_ticket"] = ta[m] / oc[m]
    return out


def append_daily_total_row(daily_kpi: pd.DataFrame) -> pd.DataFrame:
    """합계 행(평균 객단가 = 총액 ÷ 총건수)."""
    body = daily_kpi[daily_kpi["date_label"] != "합계"].copy()
    if body.empty:
        return daily_kpi.copy()
    ta = float(body["total_amount"].sum())
    oc = int(body["order_count"].sum())
    tq = float(body["total_quantity"].sum())
    avg_t = (ta / oc) if oc > 0 else 0.0
    total_row = pd.DataFrame(
        [
            {
                "date_label": "합계",
                "total_amount": ta,
                "avg_ticket": avg_t,
                "order_count": oc,
                "total_quantity": tq,
            }
        ]
    )
    return pd.concat([body, total_row], ignore_index=True)


def render_kpi_period_header(
    kpi_start_date,
    kpi_end_date,
    period_days: int,
    whole: dict[str, float],
) -> None:
    """선택 기간 한눈에 보기."""
    st.markdown(
        f'<p style="font-size:0.95rem;color:#5f6368;margin-bottom:0.4rem;">선택 기간 · '
        f'<b>{kpi_start_date}</b> ~ <b>{kpi_end_date}</b> '
        f"({period_days}일) · 기간 총 매출 <b>{whole['total_amount']:,.0f}원</b></p>",
        unsafe_allow_html=True,
    )
