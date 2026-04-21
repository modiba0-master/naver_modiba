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
    aggregation_window_kst: str
    total_amount: Decimal
    total_quantity: int


class OrdersByDateResponse(BaseModel):
    items: List[OrdersByDateItem]


class OrderRawItem(BaseModel):
    """`date`와 `business_date`는 동일: 결제일시를 KST로 본 16:00 컷오프 매출 귀속일.
    `aggregation_window_kst`: 해당 귀속일에 대응하는 집계 구간 [전일 16:00, 당일 16:00) KST 문구.
    `order_calendar_date`: 주문일(달력). `order_id`: 상품주문번호. `content_order_no`: 주문번호(동일 결제에 여러 상품줄)."""

    order_id: str
    content_order_no: str | None = None
    date: date
    business_date: date
    aggregation_window_kst: str
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
