from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 스마트스토어 '주문번호'(결제 단위). 동일 값에 여러 '상품주문번호'(order_id) 행이 붙을 수 있음.
    content_order_no: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, default=None
    )
    product_name: Mapped[str] = mapped_column(String(255))
    option_name: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    buyer_name: Mapped[str] = mapped_column(String(120))
    buyer_id: Mapped[str] = mapped_column(String(64), index=True)
    receiver_name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(255))
    order_status: Mapped[str] = mapped_column(String(50), default="신규주문")
    # 결제일시 원본(네이버 API 파싱값; 영업일 16시 규칙 미적용). 타임존 있으면 KST naive로만 통일.
    payment_date: Mapped[datetime] = mapped_column(DateTime)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    # payment_date 시각 기준 16:00 영업일 규칙으로 계산·저장. 집계 키(레거시 호환: payment_business_date와 동일).
    business_date: Mapped[date] = mapped_column(Date, index=True)
    order_business_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    payment_business_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    shipping_business_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    refund_amount: Mapped[int] = mapped_column(Integer, default=0)
    cancel_amount: Mapped[int] = mapped_column(Integer, default=0)
    net_revenue: Mapped[int] = mapped_column(Integer, default=0)
    ordered_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    placed_order_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    # 네이버 API 응답 문자열 그대로(표시·검증용). 집계·영업일 계산은 기존 DateTime 컬럼만 사용.
    order_datetime_raw: Mapped[str] = mapped_column(String(255), default="")
    payment_datetime_raw: Mapped[str] = mapped_column(String(255), default="")
    place_order_datetime_raw: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DailySummary(Base):
    __tablename__ = "daily_summary"
    __table_args__ = (
        UniqueConstraint("date", "product_id", "option_id", name="uniq_daily"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    product_id: Mapped[str] = mapped_column(String(100), default="", index=True)
    option_id: Mapped[str] = mapped_column(String(100), default="")
    orders: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[int] = mapped_column(Integer, default=0)
    cancel_count: Mapped[int] = mapped_column(Integer, default=0)
    refund_amount: Mapped[int] = mapped_column(Integer, default=0)
    profit: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
