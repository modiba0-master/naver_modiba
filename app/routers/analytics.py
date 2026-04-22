from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    DbStatsResponse,
    HeatmapResponse,
    HourRevenueResponse,
    OrdersByDateResponse,
    OrdersRawResponse,
    RevenueResponse,
)
from app.services.analytics_service import (
    get_claim_orders_raw,
    get_db_order_stats,
    get_orders_by_date,
    get_orders_raw,
    get_revenue_by_hour,
    get_revenue_heatmap,
    get_total_revenue,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

RevenueBasis = Literal["payment", "order", "shipping"]


@router.get("/db-stats", response_model=DbStatsResponse)
def db_stats(db: Session = Depends(get_db)):
    """DB `orders` 건수·최신 결제일시·최신 영업일(집계) — 동기화 여부 vs 날짜 필터 혼동 구분에 사용."""
    s = get_db_order_stats(db)
    return DbStatsResponse(
        orders_count=int(s["orders_count"]),
        latest_payment_date=s["latest_payment_date"],
        latest_business_date=s["latest_business_date"],
    )


@router.get("/orders-by-date", response_model=OrdersByDateResponse)
def orders_by_date(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    revenue_basis: RevenueBasis = "payment",
    db: Session = Depends(get_db),
):
    items = get_orders_by_date(
        db, start_date=start_date, end_date=end_date, revenue_basis=revenue_basis
    )
    return OrdersByDateResponse(items=items)


@router.get("/orders-raw", response_model=OrdersRawResponse)
def orders_raw(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    revenue_basis: RevenueBasis = "payment",
    db: Session = Depends(get_db),
):
    """기간이 있으면 DB에서 ``business_date``(결제 기준) 등 귀속일 컬럼으로 필터 — ``payment_date`` 구간이 아님."""
    items = get_orders_raw(
        db, start_date=start_date, end_date=end_date, revenue_basis=revenue_basis
    )
    items = [item.model_dump(mode="json") for item in items]
    return JSONResponse(
        content={"items": items},
        media_type="application/json; charset=utf-8",
    )


@router.get("/orders-claims", response_model=OrdersRawResponse)
def orders_claims(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: Session = Depends(get_db),
):
    """교환/반품/취소 전용 목록."""
    items = get_claim_orders_raw(db, start_date=start_date, end_date=end_date)
    items = [item.model_dump(mode="json") for item in items]
    return JSONResponse(
        content={"items": items},
        media_type="application/json; charset=utf-8",
    )


@router.get("/margin", response_model=RevenueResponse)
def margin_analysis(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    revenue_basis: RevenueBasis = "payment",
    db: Session = Depends(get_db),
):
    total_revenue = get_total_revenue(
        db, start_date=start_date, end_date=end_date, revenue_basis=revenue_basis
    )
    return RevenueResponse(total_revenue=total_revenue)


@router.get("/revenue-by-hour", response_model=HourRevenueResponse)
def revenue_by_hour(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: Session = Depends(get_db),
):
    """결제 시각(`payment_date`) 시만 사용. 기간 필터는 `payment_business_date`."""
    items = get_revenue_by_hour(db, start_date=start_date, end_date=end_date)
    return HourRevenueResponse(items=items)


@router.get("/revenue-heatmap", response_model=HeatmapResponse)
def revenue_heatmap(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: Session = Depends(get_db),
):
    """요일(`payment_business_date`) × 시(`payment_date`)."""
    items = get_revenue_heatmap(db, start_date=start_date, end_date=end_date)
    return HeatmapResponse(items=items)
