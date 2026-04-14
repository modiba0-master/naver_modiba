from datetime import datetime
from decimal import Decimal

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order
from app.schemas import MarginResponse, OrdersByDateItem


def get_orders_by_date(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> list[OrdersByDateItem]:
    order_day = func.date(Order.order_date)
    stmt = (
        select(
            order_day.label("order_day"),
            func.count(Order.id).label("order_count"),
            func.sum(Order.amount).label("total_amount"),
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
            order_count=row.order_count,
            total_amount=Decimal(row.total_amount or 0),
        )
        for row in rows
    ]


def _parse_order_day(raw_value: str | date) -> date:
    if isinstance(raw_value, date):
        return raw_value
    return date.fromisoformat(raw_value)


def get_margin_summary(
    db: Session, start_date: datetime | None, end_date: datetime | None
) -> MarginResponse:
    stmt = select(
        func.sum(Order.amount).label("revenue"),
        func.sum(Order.cost).label("cost"),
        func.sum(Order.shipping_fee).label("shipping"),
        func.sum(Order.margin).label("margin"),
    )
    if start_date:
        stmt = stmt.where(Order.order_date >= start_date)
    if end_date:
        stmt = stmt.where(Order.order_date <= end_date)

    row = db.execute(stmt).one()
    revenue = Decimal(row.revenue or 0)
    cost = Decimal(row.cost or 0)
    shipping = Decimal(row.shipping or 0)
    margin = Decimal(row.margin or 0)
    margin_rate = Decimal("0")
    if revenue > 0:
        margin_rate = (margin / revenue * Decimal("100")).quantize(Decimal("0.01"))

    return MarginResponse(
        total_revenue=revenue,
        total_cost=cost,
        total_shipping=shipping,
        total_margin=margin,
        margin_rate=margin_rate,
    )
