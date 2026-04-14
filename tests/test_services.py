from datetime import datetime

from app.services.sync import sync_orders


def test_sync_orders_inserts_data(db_session, monkeypatch):
    payload = [
        {
            "orderId": "MOCK-001",
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
