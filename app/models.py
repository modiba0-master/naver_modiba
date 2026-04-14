from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    product_name: Mapped[str] = mapped_column(String(255))
    option_name: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    buyer_name: Mapped[str] = mapped_column(String(120))
    buyer_id: Mapped[str] = mapped_column(String(64), index=True)
    receiver_name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(255))
    order_status: Mapped[str] = mapped_column(String(50), default="신규주문")
    payment_date: Mapped[datetime] = mapped_column(DateTime)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
