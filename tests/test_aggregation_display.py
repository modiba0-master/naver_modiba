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


def test_kpi_table_monday_label():
    mon = date(2026, 4, 20)  # 월
    t = format_kpi_daily_table_window_kst(mon)
    assert "월요일 집계" in t


def test_kpi_table_saturday_splits_sat_sun():
    sat = date(2026, 4, 18)  # 토
    t = format_kpi_daily_table_window_kst(sat)
    assert "토 귀속" in t
    assert "일 귀속" in t
    assert "\n" in t
