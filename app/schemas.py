from datetime import date, datetime
from decimal import Decimal
from typing import List

from pydantic import BaseModel, ConfigDict, Field


class OrderOut(BaseModel):
    order_id: str
    product_name: str
    option_name: str
    quantity: int
    amount: int
    buyer_name: str
    buyer_id: str
    receiver_name: str
    address: str
    order_status: str
    payment_date: datetime
    order_date: date
    business_date: date
    content_order_no: str | None = None
    ordered_at: datetime | None = None
    placed_order_at: datetime | None = None
    shipped_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OrdersByDateItem(BaseModel):
    order_date: date
    total_amount: Decimal
    total_quantity: int


class OrdersByDateResponse(BaseModel):
    items: List[OrdersByDateItem]


class OrderRawItem(BaseModel):
    """KPI 일자(`date`)는 결제일(달력) 기준. `business_date`는 결제일의 날짜(ISO 앞부분), 없으면 주문일(`date` 키) fallback.
    `order_id`: 상품주문번호. `content_order_no`: 주문번호(동일 결제에 여러 상품줄)."""

    order_id: str
    content_order_no: str | None = None
    date: date
    business_date: date
    order_calendar_date: date
    payment_date: datetime
    ordered_at: datetime | None = None
    placed_order_at: datetime | None = None
    shipped_at: datetime | None = None
    buyer_name: str
    buyer_id: str
    receiver_name: str
    address: str
    product_name: str
    option_name: str
    quantity: int
    amount: int
    order_status: str


class OrdersRawResponse(BaseModel):
    items: List[OrderRawItem]


class RevenueResponse(BaseModel):
    total_revenue: Decimal = Field(default=0)
