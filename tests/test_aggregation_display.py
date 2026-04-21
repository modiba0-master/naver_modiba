from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.aggregation_display import (
    format_kpi_daily_table_window_kst,
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


def test_kpi_table_window_matches_plain_window_all_weekdays():
    """KPI 표는 요일과 무관하게 전일 16:00 ~ 당일 16:00 한 줄만 표시."""
    mon = date(2026, 4, 20)
    sat = date(2026, 4, 18)
    sun = date(2026, 4, 19)
    for d in (mon, sat, sun):
        assert format_kpi_daily_table_window_kst(d) == format_kst_sales_window(d)
    assert "월요일" not in format_kpi_daily_table_window_kst(mon)
    assert "토 귀속" not in format_kpi_daily_table_window_kst(sat)
    assert "\n" not in format_kpi_daily_table_window_kst(sat)
