from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order
from app.schemas import OrderRawItem, OrdersByDateItem


def get_orders_by_date(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> list[OrdersByDateItem]:
    order_day = Order.order_date
    stmt = (
        select(
            order_day.label("order_day"),
            func.sum(Order.amount).label("total_amount"),
            func.sum(Order.quantity).label("total_quantity"),
        )
        .group_by(order_day)
        .order_by(order_day)
    )

    if start_date:
        stmt = stmt.where(Order.order_date >= start_date)
    if end_date:
        stmt = stmt.where(Order.order_date <= end_date)

    rows = db.execute(stmt).all()
    return [
        OrdersByDateItem(
            order_date=_parse_order_day(row.order_day),
            total_amount=Decimal(row.total_amount or 0),
            total_quantity=int(row.total_quantity or 0),
        )
        for row in rows
    ]


def _parse_order_day(raw_value: str | date) -> date:
    if isinstance(raw_value, date):
        return raw_value
    return date.fromisoformat(raw_value)


def get_orders_raw(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> list[OrderRawItem]:
    stmt = select(Order).order_by(Order.order_date.desc(), Order.payment_date.desc())
    if start_date:
        stmt = stmt.where(Order.order_date >= start_date.date())
    if end_date:
        stmt = stmt.where(Order.order_date <= end_date.date())

    rows = db.scalars(stmt).all()
    return [
        OrderRawItem(
            date=row.order_date,
            payment_date=row.payment_date,
            buyer_name=row.buyer_name,
            buyer_id=row.buyer_id,
            receiver_name=row.receiver_name,
            address=row.address,
            product_name=row.product_name,
            option_name=row.option_name,
            quantity=row.quantity,
            amount=row.amount,
        )
        for row in rows
    ]


def get_total_revenue(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> Decimal:
    stmt = select(
        func.sum(Order.amount).label("revenue"),
    )
    if start_date:
        stmt = stmt.where(Order.order_date >= start_date.date())
    if end_date:
        stmt = stmt.where(Order.order_date <= end_date.date())
    row = db.execute(stmt).one()
    return Decimal(row.revenue or 0)
