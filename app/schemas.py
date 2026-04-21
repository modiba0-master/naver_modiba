from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal

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
    order_business_date: date | None = None
    payment_business_date: date | None = None
    shipping_business_date: date | None = None
    refund_amount: int = 0
    cancel_amount: int = 0
    net_revenue: int = 0

    model_config = ConfigDict(from_attributes=True)


class OrdersByDateItem(BaseModel):
    order_date: date
    aggregation_window_kst: str
    total_amount: Decimal
    total_quantity: int


class OrdersByDateResponse(BaseModel):
    items: List[OrdersByDateItem]


class OrderRawItem(BaseModel):
    """`payment_date`는 원본 결제 시각, `business_date`는 16시 규칙으로 저장된 결제 기준 영업일(레거시 호환).

    `date`는 요청한 `revenue_basis`에 대응하는 영업일. 순매출 집계는 `net_revenue`를 사용한다."""

    order_id: str
    content_order_no: str | None = None
    date: date
    revenue_basis: Literal["payment", "order", "shipping"] = "payment"
    business_date: date
    order_business_date: date | None = None
    payment_business_date: date | None = None
    shipping_business_date: date | None = None
    aggregation_window_kst: str
    order_calendar_date: date
    payment_date: datetime
    ordered_at: datetime | None = None
    placed_order_at: datetime | None = None
    shipped_at: datetime | None = None
    # 네이버 API 원문(가공 없음). 화면 표시용.
    order_datetime_raw: str = ""
    payment_datetime_raw: str = ""
    place_order_datetime_raw: str = ""
    buyer_name: str
    buyer_id: str
    receiver_name: str
    address: str
    product_name: str
    option_name: str
    quantity: int
    amount: int
    refund_amount: int = 0
    cancel_amount: int = 0
    net_revenue: int = 0
    revenue_status: str = "PAID"
    order_status: str


class OrdersRawResponse(BaseModel):
    items: List[OrderRawItem]


class RevenueResponse(BaseModel):
    total_revenue: Decimal = Field(default=0)


class HourRevenueRow(BaseModel):
    hour: int
    orders: int
    revenue: Decimal


class HourRevenueResponse(BaseModel):
    items: List[HourRevenueRow]


class HeatmapCell(BaseModel):
    """day_of_week: 0=월 … 6=일 (Python weekday와 동일)."""

    day_of_week: int
    hour: int
    revenue: Decimal


class HeatmapResponse(BaseModel):
    items: List[HeatmapCell]


class DbStatsResponse(BaseModel):
    """대시보드에서 DB 반영 여부 확인용(원장 건수·최신 결제 시각)."""

    orders_count: int
    latest_payment_date: datetime | None = None
