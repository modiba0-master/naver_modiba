from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.services.naver import fetch_naver_orders

VALID_ORDER_STATUSES = {
    "신규주문",
    "배송준비",
    "배송중",
    "배송완료",
    "구매확정",
}


def normalize_order_status(value: str) -> str:
    return (value or "").strip()


def is_valid_order_status(value: str) -> bool:
    return normalize_order_status(value) in VALID_ORDER_STATUSES


def _parse_payment_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def calculate_business_date(payment_date: datetime):
    cutoff = time(hour=16, minute=0)
    weekday = payment_date.weekday()  # Monday=0 ... Sunday=6

    if weekday == 5:  # Saturday -> Monday
        return payment_date.date() + timedelta(days=2)
    if weekday == 6:  # Sunday -> Monday
        return payment_date.date() + timedelta(days=1)

    if payment_date.time() < cutoff:
        return payment_date.date()

    if weekday == 4:  # Friday 16:00+ -> next Monday
        return payment_date.date() + timedelta(days=3)

    return payment_date.date() + timedelta(days=1)


def sync_orders(db: Session) -> int:
    payloads = fetch_naver_orders()
    inserted_count = 0

    for payload in payloads:
        order_id = payload["orderId"]
        exists = db.scalar(select(Order.id).where(Order.order_id == order_id))
        if exists:
            continue

        order_status = normalize_order_status(payload.get("orderStatus", ""))
        if not is_valid_order_status(order_status):
            continue

        payment_date = _parse_payment_date(payload["paymentDate"])
        business_date = calculate_business_date(payment_date)
        order = Order(
            order_id=order_id,
            product_name=payload["productName"],
            option_name=payload["optionName"],
            quantity=int(payload["quantity"]),
            amount=int(payload["paymentAmount"]),
            buyer_name=payload["ordererName"],
            buyer_id=payload["ordererId"],
            receiver_name=payload["receiverName"],
            address=payload["shippingAddress"],
            order_status=order_status,
            payment_date=payment_date,
            order_date=payment_date.date(),
            business_date=business_date,
        )
        db.add(order)
        inserted_count += 1

    db.commit()
    return inserted_count
