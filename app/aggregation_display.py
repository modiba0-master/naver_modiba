"""매출 집계일(`business_date`) 표시 — 영업일 16:00 컷과 정합.

영업일 D에 묶이는 결제 시각(KST naive): **전일 16:00:00 ~ 당일 15:59:59**.

Streamlit 전용 복제: `streamlit_app/services/aggregation_display.py` (로직 동일, 함께 유지).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def kst_sales_window_for_business_date(business_day: date) -> tuple[datetime, datetime]:
    """집계일 ``business_day``에 묶이는 결제 시각(KST naive) 닫힌 구간 [start, end]."""
    start = datetime.combine(business_day - timedelta(days=1), time(16, 0, 0))
    end = datetime.combine(business_day, time(15, 59, 59))
    return start, end


def format_kst_sales_window(business_day: date) -> str:
    """상세 표용: 전일 16:00~당일 15:59 KST 결제 → ``business_date``."""
    return (
        f"{business_day.isoformat()} (영업일: 전일 16:00~당일 15:59 KST 결제 → DB business_date)"
    )


def format_kpi_daily_table_window_kst(business_day: date) -> str:
    """`format_kst_sales_window`와 동일."""
    return format_kst_sales_window(business_day)
