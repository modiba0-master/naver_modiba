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
