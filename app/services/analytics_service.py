from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.schemas import OrderRawItem, OrdersByDateItem
from app.services.sync import calculate_business_date, is_valid_order_status, normalize_order_status


def get_orders_by_date(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> list[OrdersByDateItem]:
    raw_items = get_orders_raw(db, start_date=start_date, end_date=end_date)
    grouped: dict[date, dict[str, Decimal | int]] = {}

    for item in raw_items:
        day = item.business_date
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


def get_orders_raw(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> list[OrderRawItem]:
    stmt = select(Order).order_by(Order.payment_date.desc())

    rows = db.scalars(stmt).all()
    items: list[OrderRawItem] = []
    for row in rows:
        normalized_status = normalize_order_status(row.order_status)
        if not is_valid_order_status(normalized_status):
            continue

        business_date = calculate_business_date(row.payment_date)
        if start_date and business_date < start_date.date():
            continue
        if end_date and business_date > end_date.date():
            continue

        items.append(
            OrderRawItem(
                order_id=row.order_id,
                date=business_date,
                business_date=business_date,
                payment_date=row.payment_date,
                buyer_name=row.buyer_name,
                buyer_id=row.buyer_id,
                receiver_name=row.receiver_name,
                address=row.address,
                product_name=row.product_name,
                option_name=row.option_name,
                quantity=row.quantity,
                amount=row.amount,
                order_status=normalized_status,
            )
        )
    return items


def get_total_revenue(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> Decimal:
    raw_items = get_orders_raw(db, start_date=start_date, end_date=end_date)
    return Decimal(sum(item.amount for item in raw_items))
