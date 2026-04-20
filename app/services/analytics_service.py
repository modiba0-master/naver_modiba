from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.schemas import OrderRawItem, OrdersByDateItem
from app.services.sync import is_valid_order_status, normalize_order_status


def get_orders_by_date(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> list[OrdersByDateItem]:
    raw_items = get_orders_raw(db, start_date=start_date, end_date=end_date)
    grouped: dict[date, dict[str, Decimal | int]] = {}

    for item in raw_items:
        day = item.date  # 결제일(달력) 기준 일별 KPI
        if day not in grouped:
            grouped[day] = {
                "total_amount": Decimal(0),
                "total_quantity": 0,
            }
        grouped[day]["total_amount"] = Decimal(grouped[day]["total_amount"]) + Decimal(
            item.amount
        )
        grouped[day]["total_quantity"] = int(grouped[day]["total_quantity"]) + int(
            item.quantity
        )

    results = []
    for day in sorted(grouped.keys()):
        results.append(
            OrdersByDateItem(
                order_date=day,
                total_amount=Decimal(grouped[day]["total_amount"]),
                total_quantity=int(grouped[day]["total_quantity"]),
            )
        )
    return results


def _parse_order_day(raw_value: str | date) -> date:
    if isinstance(raw_value, date):
        return raw_value
    return date.fromisoformat(raw_value)


def _to_date(dt):
    if not dt:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat().split("T")[0]
    if isinstance(dt, date):
        return dt.isoformat().split("T")[0]
    return str(dt).split("T")[0]


def get_orders_raw(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> list[OrderRawItem]:
    stmt = select(Order).order_by(Order.payment_date.desc())

    rows = db.scalars(stmt).all()
    seen_orders: set[str] = set()
    filtered_items: list[dict[str, Any]] = []
    for row in rows:
        normalized_status = normalize_order_status(row.order_status)
        if not is_valid_order_status(normalized_status):
            continue

        pay_day = row.payment_date.date()

        if start_date and pay_day < start_date.date():
            continue
        if end_date and pay_day > end_date.date():
            continue

        order_id = row.order_id
        if order_id in seen_orders:
            continue
        seen_orders.add(order_id)

        item: dict = {
            "payment_date": row.payment_date,
            "date": row.order_date,
        }
        payment_date = item.get("payment_date")
        order_date = item.get("date")

        business_date = _to_date(payment_date)
        if not business_date:
            business_date = _to_date(order_date)

        item["business_date"] = business_date

        bd_str = item["business_date"]
        item["order_id"] = row.order_id
        item["content_order_no"] = row.content_order_no
        item["date"] = pay_day
        item["business_date"] = (
            date.fromisoformat(bd_str) if bd_str else row.payment_date.date()
        )
        item["order_calendar_date"] = row.order_date
        item["payment_date"] = row.payment_date
        item["ordered_at"] = row.ordered_at
        item["placed_order_at"] = row.placed_order_at
        item["shipped_at"] = row.shipped_at
        item["buyer_name"] = row.buyer_name
        item["buyer_id"] = row.buyer_id
        item["receiver_name"] = row.receiver_name
        item["address"] = row.address
        item["product_name"] = row.product_name
        item["option_name"] = row.option_name
        item["quantity"] = row.quantity
        item["amount"] = row.amount
        item["order_status"] = normalized_status

        filtered_items.append(item)

    items = filtered_items
    return [OrderRawItem.model_validate(x) for x in items]


def get_total_revenue(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> Decimal:
    raw_items = get_orders_raw(db, start_date=start_date, end_date=end_date)
    return Decimal(sum(item.amount for item in raw_items))
