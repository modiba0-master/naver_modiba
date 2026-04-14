from datetime import date, datetime
from decimal import Decimal
from typing import List

from pydantic import BaseModel, ConfigDict, Field


class OrderOut(BaseModel):
    order_id: str
    customer_id: str
    customer_name: str
    order_date: datetime
    amount: Decimal
    cost: Decimal
    shipping_fee: Decimal
    margin: Decimal
    customer_tag: str

    model_config = ConfigDict(from_attributes=True)


class OrdersByDateItem(BaseModel):
    order_date: date
    order_count: int
    total_amount: Decimal


class OrdersByDateResponse(BaseModel):
    items: List[OrdersByDateItem]


class MarginResponse(BaseModel):
    total_revenue: Decimal = Field(default=0)
    total_cost: Decimal = Field(default=0)
    total_shipping: Decimal = Field(default=0)
    total_margin: Decimal = Field(default=0)
    margin_rate: Decimal = Field(default=0)


class SyncResponse(BaseModel):
    inserted_count: int
