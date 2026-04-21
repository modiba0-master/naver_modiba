"""매출 귀속일(business_date) ↔ KST 집계 구간 표시.

`app/aggregation_display.py`와 동일한 로직. 대시보드만 배포(streamlit_app 루트)할 때
상위 `app/` 패키지 없이 동작하도록 복제해 둔다 — 변경 시 양쪽을 맞출 것.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_sales_window_for_business_date(business_day: date) -> tuple[datetime, datetime]:
    """집계 귀속일 D에 매출이 묶이는 결제 시각(KST) 구간 (시작 포함, 끝 제외)."""
    end_exclusive = datetime.combine(business_day, time(16, 0), tzinfo=KST)
    start_inclusive = end_exclusive - timedelta(days=1)
    return start_inclusive, end_exclusive


def format_kst_sales_window(business_day: date) -> str:
    """예: 2026-04-20 16:00 ~ 2026-04-21 16:00 (KST)"""
    start, end_excl = kst_sales_window_for_business_date(business_day)
    return (
        f"{start.strftime('%Y-%m-%d %H:%M')} ~ "
        f"{end_excl.strftime('%Y-%m-%d %H:%M')} (KST)"
    )


def format_kpi_daily_table_window_kst(business_day: date) -> str:
    """KPI 일자 테이블 전용: `app/aggregation_display.py` 와 동일 로직."""
    wd = business_day.weekday()
    start, end_excl = kst_sales_window_for_business_date(business_day)
    if wd == 0:
        return (
            f"월요일 집계: {start.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{end_excl.strftime('%Y-%m-%d %H:%M')} (KST)"
        )
    if wd == 5:
        sun_bd = business_day + timedelta(days=1)
        s2, e2 = kst_sales_window_for_business_date(sun_bd)
        line_sat = (
            f"토 귀속: {start.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{end_excl.strftime('%Y-%m-%d %H:%M')} (KST)"
        )
        line_sun = (
            f"일 귀속: {s2.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{e2.strftime('%Y-%m-%d %H:%M')} (KST)"
        )
        return line_sat + "\n" + line_sun
    if wd == 6:
        return (
            f"일요일: {start.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{end_excl.strftime('%Y-%m-%d %H:%M')} (KST)"
        )
    return format_kst_sales_window(business_day)
