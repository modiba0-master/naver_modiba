from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import Order
from app.services.sync import (
    calculate_business_date,
    parse_payment_datetime_string,
    sync_orders,
    to_kst_naive,
)


def test_calculate_business_date_16h_cutoff_kst_wall_clock():
    assert calculate_business_date(datetime(2026, 4, 21, 15, 0)) == date(2026, 4, 21)
    assert calculate_business_date(datetime(2026, 4, 21, 15, 59)) == date(2026, 4, 21)
    assert calculate_business_date(datetime(2026, 4, 21, 16, 0)) == date(2026, 4, 22)
    assert calculate_business_date(datetime(2026, 4, 21, 23, 59)) == date(2026, 4, 22)


def test_calculate_business_date_utc_converts_to_kst_before_16h_cut():
    # 2026-04-14 07:00 UTC = 2026-04-14 16:00 KST → 익일 영업일
    assert calculate_business_date(datetime(2026, 4, 14, 7, 0, tzinfo=ZoneInfo("UTC"))) == date(
        2026, 4, 15
    )


def test_to_kst_naive_and_parse_payment_string():
    assert to_kst_naive(datetime(2026, 4, 21, 15, 30, 0)) == datetime(2026, 4, 21, 15, 30, 0)
    assert parse_payment_datetime_string("2026-04-21 15:30:00") == datetime(
        2026, 4, 21, 15, 30, 0
    )
    # Z: UTC 벽시각 naive 파싱 후 +9h → KST naive
    assert parse_payment_datetime_string("2026-04-21T06:00:00Z") == datetime(2026, 4, 21, 15, 0, 0)
    assert parse_payment_datetime_string("2026-04-21T06:00:00z") == datetime(2026, 4, 21, 15, 0, 0)
    assert parse_payment_datetime_string("2026-04-21T15:00:00+09:00") == datetime(
        2026, 4, 21, 15, 0, 0
    )
    assert parse_payment_datetime_string("") is None
    assert parse_payment_datetime_string("   ") is None


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
    assert row.business_date == date(2026, 1, 1)


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
