from datetime import date, datetime

from app.aggregation_display import (
    format_kpi_daily_table_window_kst,
    format_kst_sales_window,
    kst_sales_window_for_business_date,
)


def test_kst_sales_window_16h_business_day_span():
    d = date(2026, 4, 21)
    start, end = kst_sales_window_for_business_date(d)
    assert start == datetime(2026, 4, 20, 16, 0, 0)
    assert end == datetime(2026, 4, 21, 15, 59, 59)


def test_format_kst_sales_window_describes_cutoff():
    d = date(2026, 4, 21)
    s = format_kst_sales_window(d)
    assert "2026-04-21" in s
    assert "16:00" in s


def test_kpi_table_window_matches_plain_window():
    mon = date(2026, 4, 20)
    sat = date(2026, 4, 18)
    for d in (mon, sat):
        assert format_kpi_daily_table_window_kst(d) == format_kst_sales_window(d)
