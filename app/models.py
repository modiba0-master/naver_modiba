from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    order_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    margin: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    customer_tag: Mapped[str] = mapped_column(String(20), default="Normal")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
