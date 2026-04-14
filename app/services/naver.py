from datetime import datetime, timedelta


def fetch_naver_orders() -> list[dict]:
    now = datetime.utcnow()
    return [
        {
            "orderId": f"NAVER-{now.strftime('%Y%m%d%H%M%S')}-001",
            "productName": "닭가슴살 1kg",
            "optionName": "1kg 3개",
            "quantity": 1,
            "paymentAmount": 30000,
            "orderStatus": "신규주문",
            "ordererName": "홍길동",
            "ordererId": "buyer-001",
            "receiverName": "홍길동",
            "shippingAddress": "서울특별시 강남구 테헤란로 123",
            "paymentDate": now.isoformat(),
        },
        {
            "orderId": f"NAVER-{(now - timedelta(minutes=5)).strftime('%Y%m%d%H%M%S')}-002",
            "productName": "닭안심 500g",
            "optionName": "500g x 2",
            "quantity": 2,
            "paymentAmount": 24000,
            "orderStatus": "배송준비",
            "ordererName": "김모디",
            "ordererId": "buyer-002",
            "receiverName": "김모디",
            "shippingAddress": "부산광역시 해운대구 센텀로 77",
            "paymentDate": (now - timedelta(minutes=5)).isoformat(),
        },
    ]
