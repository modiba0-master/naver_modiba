from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.aggregation_display import (
    format_kst_sales_window,
    kst_sales_window_for_business_date,
)

KST = ZoneInfo("Asia/Seoul")


def test_kst_sales_window_for_april_21():
    d = date(2026, 4, 21)
    start, end_excl = kst_sales_window_for_business_date(d)
    assert start == datetime(2026, 4, 20, 16, 0, tzinfo=KST)
    assert end_excl == datetime(2026, 4, 21, 16, 0, tzinfo=KST)
    s = format_kst_sales_window(d)
    assert "2026-04-20 16:00" in s
    assert "2026-04-21 16:00" in s
    assert "KST" in s
