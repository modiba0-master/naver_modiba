from datetime import datetime

from sqlalchemy import select

from app.models import Order
from app.services.sync import calculate_business_date, sync_orders


def test_calculate_business_date_cutoff_and_weekend():
    assert calculate_business_date(datetime(2026, 4, 14, 15, 59)).isoformat() == "2026-04-14"  # Tue before 16
    assert calculate_business_date(datetime(2026, 4, 14, 16, 0)).isoformat() == "2026-04-15"  # Tue after 16
    assert calculate_business_date(datetime(2026, 4, 17, 16, 0)).isoformat() == "2026-04-20"  # Fri after 16 -> Mon
    assert calculate_business_date(datetime(2026, 4, 18, 10, 0)).isoformat() == "2026-04-20"  # Sat -> Mon
    assert calculate_business_date(datetime(2026, 4, 19, 10, 0)).isoformat() == "2026-04-20"  # Sun -> Mon


def test_sync_orders_inserts_data(db_session, monkeypatch):
    payload = [
        {
            "orderId": "MOCK-001",
            "contentOrderNo": "2026041870238181",
            "productName": "닭가슴살",
            "optionName": "1kg 2개",
            "quantity": 1,
            "paymentAmount": 20000,
            "orderStatus": "신규주문",
            "ordererName": "테스터",
            "ordererId": "buyer-001",
            "receiverName": "테스터",
            "shippingAddress": "서울시 강남구",
            "paymentDate": datetime(2026, 1, 1, 12, 0, 0).isoformat(),
        }
    ]

    monkeypatch.setattr("app.services.sync.fetch_naver_orders", lambda: payload)

    inserted = sync_orders(db_session)
    assert inserted == 1
    row = db_session.scalar(select(Order).where(Order.order_id == "MOCK-001"))
    assert row is not None
    assert row.content_order_no == "2026041870238181"


def test_sync_orders_merges_place_and_ship_times(db_session, monkeypatch):
    base = {
        "orderId": "MOCK-002",
        "productName": "닭가슴살",
        "optionName": "1kg",
        "quantity": 1,
        "paymentAmount": 10000,
        "orderStatus": "신규주문",
        "ordererName": "테스터",
        "ordererId": "buyer-002",
        "receiverName": "테스터",
        "shippingAddress": "서울",
        "paymentDate": datetime(2026, 2, 1, 12, 0, 0).isoformat(),
    }
    monkeypatch.setattr(
        "app.services.sync.fetch_naver_orders",
        lambda: [base],
    )
    assert sync_orders(db_session) == 1

    place_iso = datetime(2026, 2, 2, 9, 0, 0).isoformat()
    ship_iso = datetime(2026, 2, 3, 15, 30, 0).isoformat()
    monkeypatch.setattr(
        "app.services.sync.fetch_naver_orders",
        lambda: [
            {
                **base,
                "placeOrderDate": place_iso,
                "sendDate": ship_iso,
                "orderStatus": "배송중",
            }
        ],
    )
    assert sync_orders(db_session) == 0

    row = db_session.scalar(select(Order).where(Order.order_id == "MOCK-002"))
    assert row is not None
    assert row.placed_order_at is not None
    assert row.shipped_at is not None
    assert row.order_status == "배송중"
