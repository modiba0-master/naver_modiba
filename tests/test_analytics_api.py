from datetime import datetime

from app.services.sync import sync_orders


def test_analytics_endpoints(client, db_session, monkeypatch):
    payload = [
        {
            "orderId": "MOCK-API-001",
            "productName": "닭가슴살",
            "optionName": "1kg 2개",
            "quantity": 1,
            "paymentAmount": 20000,
            "orderStatus": "신규주문",
            "ordererName": "테스터",
            "ordererId": "buyer-api-001",
            "receiverName": "테스터",
            "shippingAddress": "서울시 강남구",
            "paymentDate": datetime(2026, 1, 2, 11, 0, 0).isoformat(),
        }
    ]
    monkeypatch.setattr("app.services.sync.fetch_naver_orders", lambda: payload)
    inserted = sync_orders(db_session)
    assert inserted == 1

    stats = client.get("/analytics/db-stats")
    assert stats.status_code == 200
    sj = stats.json()
    assert sj["orders_count"] >= 1
    assert sj.get("latest_payment_date")
    assert "latest_business_date" in sj

    by_date_response = client.get("/analytics/orders-by-date")
    assert by_date_response.status_code == 200
    assert "items" in by_date_response.json()

    raw_response = client.get("/analytics/orders-raw")
    assert raw_response.status_code == 200
    body = raw_response.json()
    assert "items" in body
    assert body["items"]
    first = body["items"][0]
    assert "order_calendar_date" in first
    assert "date" in first
    assert "aggregation_window_kst" in first
    assert "2026-01-02" in first["aggregation_window_kst"]

    margin_response = client.get("/analytics/margin")
    assert margin_response.status_code == 200
    body = margin_response.json()
    assert "total_revenue" in body

    hour_response = client.get("/analytics/revenue-by-hour")
    assert hour_response.status_code == 200
    assert "items" in hour_response.json()

    heat_response = client.get("/analytics/revenue-heatmap")
    assert heat_response.status_code == 200
    assert "items" in heat_response.json()

    raw_payment = client.get("/analytics/orders-raw", params={"revenue_basis": "payment"})
    assert raw_payment.status_code == 200

    ledger_response = client.get("/analytics/orders-ledger")
    assert ledger_response.status_code == 200
    ledger = ledger_response.json()
    assert "items" in ledger
    assert ledger["items"]
    first_ledger = ledger["items"][0]
    assert "order_id" in first_ledger
    assert "product_name" in first_ledger
    assert "payment_date" in first_ledger
    assert "order_detail_status" in first_ledger
