from datetime import datetime
from decimal import Decimal

from app.services.order_service import calculate_customer_tag, sync_orders


def test_calculate_customer_tag():
    assert calculate_customer_tag(Decimal("1000000")) == "VIP"
    assert calculate_customer_tag(Decimal("500000")) == "Regular"
    assert calculate_customer_tag(Decimal("50000")) == "Normal"


def test_sync_orders_inserts_data(db_session, monkeypatch):
    payload = [
        {
            "order_id": "MOCK-001",
            "customer_id": "CUST-1",
            "customer_name": "Test User",
            "order_date": datetime(2026, 1, 1, 12, 0, 0),
            "amount": Decimal("500000.00"),
            "cost": Decimal("300000.00"),
            "shipping_fee": Decimal("3000.00"),
        }
    ]

    monkeypatch.setattr(
        "app.services.order_service.fetch_mock_orders", lambda since=None: payload
    )
    monkeypatch.setattr(
        "app.services.order_service.notify_new_order", lambda topic, order_id: None
    )

    inserted = sync_orders(db_session)
    assert inserted == 1
