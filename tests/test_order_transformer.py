from datetime import date, datetime

from app.services.order_transformer import calculate_business_date


def test_order_transformer_16h_rule_no_timezone_in_function():
    """order_transformer 내부는 시각 변환 없이 hour만 본다."""
    assert calculate_business_date(datetime(2026, 4, 21, 15, 59)) == date(2026, 4, 21)
    assert calculate_business_date(datetime(2026, 4, 21, 16, 0)) == date(2026, 4, 22)
