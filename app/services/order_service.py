from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order
from app.services.mock_naver_api import fetch_mock_orders
from app.services.notification_service import notify_new_order


def calculate_customer_tag(total_amount: Decimal) -> str:
    if total_amount >= Decimal("1000000"):
        return "VIP"
    if total_amount >= Decimal("300000"):
        return "Regular"
    return "Normal"


def _customer_total_amount(db: Session, customer_id: str) -> Decimal:
    total = db.scalar(
        select(func.sum(Order.amount)).where(Order.customer_id == customer_id)
    )
    return Decimal(total or 0)


def _latest_order_date(db: Session) -> datetime | None:
    return db.scalar(select(func.max(Order.order_date)))


def sync_orders(db: Session) -> int:
    since = _latest_order_date(db)
    new_orders = fetch_mock_orders(since=since)
    inserted_count = 0

    for payload in new_orders:
        exists = db.scalar(
            select(Order.id).where(Order.order_id == payload["order_id"])
        )
        if exists:
            continue

        margin = payload["amount"] - payload["cost"] - payload["shipping_fee"]
        order = Order(
            order_id=payload["order_id"],
            customer_id=payload["customer_id"],
            customer_name=payload["customer_name"],
            order_date=payload["order_date"],
            amount=payload["amount"],
            cost=payload["cost"],
            shipping_fee=payload["shipping_fee"],
            margin=margin,
            customer_tag="Normal",
        )
        db.add(order)
        db.flush()

        cumulative_amount = _customer_total_amount(db, payload["customer_id"])
        order.customer_tag = calculate_customer_tag(cumulative_amount)
        notify_new_order(settings.ntfy_topic, order.order_id)
        inserted_count += 1

    db.commit()
    return inserted_count
