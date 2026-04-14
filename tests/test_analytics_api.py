def test_sync_and_analytics_endpoints(client, monkeypatch):
    sync_response = client.post("/analytics/sync-orders")
    assert sync_response.status_code == 200
    assert sync_response.json()["inserted_count"] >= 1

    by_date_response = client.get("/analytics/orders-by-date")
    assert by_date_response.status_code == 200
    assert "items" in by_date_response.json()

    raw_response = client.get("/analytics/orders-raw")
    assert raw_response.status_code == 200
    assert "items" in raw_response.json()

    margin_response = client.get("/analytics/margin")
    assert margin_response.status_code == 200
    body = margin_response.json()
    assert "total_revenue" in body
