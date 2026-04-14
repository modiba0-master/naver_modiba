from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    OrdersByDateResponse,
    OrdersRawResponse,
    RevenueResponse,
    SyncResponse,
)
from app.services.analytics_service import get_orders_by_date, get_orders_raw, get_total_revenue
from app.services.sync import sync_orders

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/sync-orders", response_model=SyncResponse)
def sync_orders_endpoint(db: Session = Depends(get_db)):
    inserted_count = sync_orders(db)
    return SyncResponse(inserted_count=inserted_count)


@router.get("/orders-by-date", response_model=OrdersByDateResponse)
def orders_by_date(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: Session = Depends(get_db),
):
    items = get_orders_by_date(db, start_date=start_date, end_date=end_date)
    return OrdersByDateResponse(items=items)


@router.get("/orders-raw", response_model=OrdersRawResponse)
def orders_raw(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: Session = Depends(get_db),
):
    items = get_orders_raw(db, start_date=start_date, end_date=end_date)
    return OrdersRawResponse(items=items)


@router.get("/margin", response_model=RevenueResponse)
def margin_analysis(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: Session = Depends(get_db),
):
    total_revenue = get_total_revenue(db, start_date=start_date, end_date=end_date)
    return RevenueResponse(total_revenue=total_revenue)
