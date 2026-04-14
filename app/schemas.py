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

    model_config = ConfigDict(from_attributes=True)


class OrdersByDateItem(BaseModel):
    order_date: date
    total_amount: Decimal
    total_quantity: int


class OrdersByDateResponse(BaseModel):
    items: List[OrdersByDateItem]


class OrderRawItem(BaseModel):
    date: date
    business_date: date
    payment_date: datetime
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


class SyncResponse(BaseModel):
    inserted_count: int
