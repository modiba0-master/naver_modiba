from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.services.naver import fetch_naver_orders


def _parse_payment_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sync_orders(db: Session) -> int:
    payloads = fetch_naver_orders()
    inserted_count = 0

    for payload in payloads:
        order_id = payload["orderId"]
        exists = db.scalar(select(Order.id).where(Order.order_id == order_id))
        if exists:
            continue

        payment_date = _parse_payment_date(payload["paymentDate"])
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
            payment_date=payment_date,
            order_date=payment_date.date(),
        )
        db.add(order)
        inserted_count += 1

    db.commit()
    return inserted_count
