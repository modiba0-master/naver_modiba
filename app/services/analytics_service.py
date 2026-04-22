from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.aggregation_display import format_kst_sales_window
from app.models import Order
from app.schemas import (
    HeatmapCell,
    HourRevenueRow,
    OrderLedgerItem,
    OrderRawItem,
    OrdersByDateItem,
)
from app.services.revenue_compute import derive_revenue_status
from app.services.sync import is_claim_event_status, is_valid_order_status, normalize_order_status

RevenueBasis = Literal["payment", "order", "shipping"]


def get_db_order_stats(db: Session) -> dict[str, Any]:
    """원장 건수·최신 결제일시·최신 영업일(집계) — `date`/KPI vs 실제 결제 캘린더 구분에 사용."""
    cnt = int(db.scalar(select(func.count()).select_from(Order)) or 0)
    last_pd = db.scalar(select(func.max(Order.payment_date)))
    # KPI·일자별 매출이 쓰는 `business_date`(16시 규칙) 최댓값
    last_bd = db.scalar(select(func.max(Order.business_date)))
    return {
        "orders_count": cnt,
        "latest_payment_date": last_pd,
        "latest_business_date": last_bd,
    }


def _effective_business_date(row: Order, basis: RevenueBasis) -> date | None:
    if basis == "order":
        return row.order_business_date or row.payment_business_date or row.business_date
    if basis == "shipping":
        return row.shipping_business_date
    return row.payment_business_date or row.business_date


def _row_in_basis(row: Order, basis: RevenueBasis) -> bool:
    if basis == "shipping":
        return row.shipping_business_date is not None
    return True


def _orders_raw_sql_date_column(basis: RevenueBasis):
    """orders-raw 기간 필터: 결제 기준은 ``business_date`` 컬럼, 그 외는 ``_effective_business_date``와 동일한 coalesce."""
    if basis == "order":
        return func.coalesce(
            Order.order_business_date,
            Order.payment_business_date,
            Order.business_date,
        )
    if basis == "shipping":
        return Order.shipping_business_date
    return Order.business_date


def get_orders_by_date(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
    revenue_basis: RevenueBasis = "payment",
) -> list[OrdersByDateItem]:
    raw_items = get_orders_raw(
        db,
        start_date=start_date,
        end_date=end_date,
        revenue_basis=revenue_basis,
    )
    grouped: dict[date, dict[str, Decimal | int]] = {}

    for item in raw_items:
        day = item.date
        if day not in grouped:
            grouped[day] = {
                "total_amount": Decimal(0),
                "total_quantity": 0,
            }
        grouped[day]["total_amount"] = Decimal(grouped[day]["total_amount"]) + Decimal(
            item.net_revenue
        )
        grouped[day]["total_quantity"] = int(grouped[day]["total_quantity"]) + int(
            item.quantity
        )

    results = []
    for day in sorted(grouped.keys()):
        results.append(
            OrdersByDateItem(
                order_date=day,
                aggregation_window_kst=format_kst_sales_window(day),
                total_amount=Decimal(grouped[day]["total_amount"]),
                total_quantity=int(grouped[day]["total_quantity"]),
            )
        )
    return results


def get_orders_raw(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
    revenue_basis: RevenueBasis = "payment",
) -> list[OrderRawItem]:
    stmt = select(Order).order_by(Order.payment_date.desc())
    col = _orders_raw_sql_date_column(revenue_basis)
    if start_date is not None and end_date is not None:
        stmt = stmt.where(
            col.between(start_date.date(), end_date.date()),
        )
    elif start_date is not None:
        stmt = stmt.where(col >= start_date.date())
    elif end_date is not None:
        stmt = stmt.where(col <= end_date.date())

    rows = db.scalars(stmt).all()
    seen_orders: set[str] = set()
    filtered_items: list[dict[str, Any]] = []
    for row in rows:
        normalized_status = normalize_order_status(row.order_status)
        if not is_valid_order_status(normalized_status):
            continue

        if not _row_in_basis(row, revenue_basis):
            continue

        bd = _effective_business_date(row, revenue_basis)
        if bd is None:
            continue

        order_id = row.order_id
        if order_id in seen_orders:
            continue
        seen_orders.add(order_id)

        pay_bd = row.payment_business_date or row.business_date
        rs = derive_revenue_status(row.net_revenue, row.amount)

        item: dict = {}
        item["order_id"] = row.order_id
        item["content_order_no"] = row.content_order_no
        item["date"] = bd
        item["revenue_basis"] = revenue_basis
        item["business_date"] = pay_bd
        item["order_business_date"] = row.order_business_date
        item["payment_business_date"] = row.payment_business_date
        item["shipping_business_date"] = row.shipping_business_date
        item["aggregation_window_kst"] = format_kst_sales_window(bd)
        item["order_calendar_date"] = row.order_date
        item["payment_date"] = row.payment_date
        item["ordered_at"] = row.ordered_at
        item["placed_order_at"] = row.placed_order_at
        item["shipped_at"] = row.shipped_at
        item["order_datetime_raw"] = getattr(row, "order_datetime_raw", "") or ""
        item["payment_datetime_raw"] = getattr(row, "payment_datetime_raw", "") or ""
        item["place_order_datetime_raw"] = getattr(row, "place_order_datetime_raw", "") or ""
        item["buyer_name"] = row.buyer_name
        item["buyer_id"] = row.buyer_id
        item["receiver_name"] = row.receiver_name
        item["address"] = row.address
        item["product_name"] = row.product_name
        item["option_name"] = row.option_name
        item["quantity"] = row.quantity
        item["amount"] = row.amount
        item["refund_amount"] = row.refund_amount
        item["cancel_amount"] = row.cancel_amount
        item["net_revenue"] = row.net_revenue
        item["revenue_status"] = rs
        item["order_status"] = normalized_status

        filtered_items.append(item)

    items = filtered_items
    return [OrderRawItem.model_validate(x) for x in items]


def get_claim_orders_raw(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[OrderRawItem]:
    """교환/반품/취소 전용 조회. 기본 매출 목록과 분리해서 본다."""
    stmt = select(Order).order_by(Order.payment_date.desc())
    col = _orders_raw_sql_date_column("payment")
    if start_date is not None and end_date is not None:
        stmt = stmt.where(col.between(start_date.date(), end_date.date()))
    elif start_date is not None:
        stmt = stmt.where(col >= start_date.date())
    elif end_date is not None:
        stmt = stmt.where(col <= end_date.date())

    rows = db.scalars(stmt).all()
    out: list[dict[str, Any]] = []
    for row in rows:
        st = normalize_order_status(row.order_status)
        if not (is_claim_event_status(st) or row.refund_amount > 0 or row.cancel_amount > 0):
            continue
        pay_bd = row.payment_business_date or row.business_date
        item: dict[str, Any] = {
            "order_id": row.order_id,
            "content_order_no": row.content_order_no,
            "date": row.payment_business_date or row.business_date,
            "revenue_basis": "payment",
            "business_date": pay_bd,
            "order_business_date": row.order_business_date,
            "payment_business_date": row.payment_business_date,
            "shipping_business_date": row.shipping_business_date,
            "aggregation_window_kst": format_kst_sales_window(pay_bd),
            "order_calendar_date": row.order_date,
            "payment_date": row.payment_date,
            "ordered_at": row.ordered_at,
            "placed_order_at": row.placed_order_at,
            "shipped_at": row.shipped_at,
            "order_datetime_raw": getattr(row, "order_datetime_raw", "") or "",
            "payment_datetime_raw": getattr(row, "payment_datetime_raw", "") or "",
            "place_order_datetime_raw": getattr(row, "place_order_datetime_raw", "") or "",
            "buyer_name": row.buyer_name,
            "buyer_id": row.buyer_id,
            "receiver_name": row.receiver_name,
            "address": row.address,
            "product_name": row.product_name,
            "option_name": row.option_name,
            "quantity": row.quantity,
            "amount": row.amount,
            "refund_amount": row.refund_amount,
            "cancel_amount": row.cancel_amount,
            "net_revenue": row.net_revenue,
            "revenue_status": derive_revenue_status(row.net_revenue, row.amount),
            "order_status": st,
        }
        out.append(item)
    return [OrderRawItem.model_validate(x) for x in out]


def get_orders_ledger(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[OrderLedgerItem]:
    """운영 확인/다운로드용 주문 원장 상세. 저장된 확장 컬럼을 그대로 노출."""
    stmt = select(Order).order_by(Order.payment_date.desc(), Order.id.desc())
    if start_date is not None:
        stmt = stmt.where(Order.payment_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Order.payment_date <= end_date)
    rows = db.scalars(stmt).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "order_id": row.order_id,
                "content_order_no": row.content_order_no,
                "payment_date": row.payment_date,
                "order_date": row.order_date,
                "business_date": row.business_date,
                "order_status": row.order_status,
                "order_detail_status": row.order_detail_status,
                "pay_location_type": row.pay_location_type,
                "buyer_name": row.buyer_name,
                "buyer_id": row.buyer_id,
                "buyer_contact": row.buyer_contact,
                "receiver_name": row.receiver_name,
                "receiver_contact1": row.receiver_contact1,
                "address": row.address,
                "integrated_shipping_address": row.integrated_shipping_address,
                "shipping_message": row.shipping_message,
                "product_no": row.product_no,
                "product_name": row.product_name,
                "product_type": row.product_type,
                "option_name": row.option_name,
                "option_code": row.option_code,
                "quantity": row.quantity,
                "option_price": row.option_price,
                "product_price": row.product_price,
                "final_product_discount_amount": row.final_product_discount_amount,
                "seller_discount_amount": row.seller_discount_amount,
                "final_order_amount": row.final_order_amount,
                "amount": row.amount,
                "delivery_fee_type": row.delivery_fee_type,
                "delivery_bundle_group_no": row.delivery_bundle_group_no,
                "delivery_fee_pay_type": row.delivery_fee_pay_type,
                "delivery_fee_amount": row.delivery_fee_amount,
                "jeju_island_extra_fee": row.jeju_island_extra_fee,
                "delivery_fee_discount_amount": row.delivery_fee_discount_amount,
                "dispatch_due_date_raw": row.dispatch_due_date_raw,
                "shipped_date_raw": row.shipped_date_raw,
                "payment_method": row.payment_method,
                "naverpay_order_commission": row.naverpay_order_commission,
                "sales_integration_commission": row.sales_integration_commission,
                "expected_settlement_amount": row.expected_settlement_amount,
                "refund_amount": row.refund_amount,
                "cancel_amount": row.cancel_amount,
                "net_revenue": row.net_revenue,
                "ordered_at": row.ordered_at,
                "placed_order_at": row.placed_order_at,
                "shipped_at": row.shipped_at,
                "order_datetime_raw": row.order_datetime_raw,
                "payment_datetime_raw": row.payment_datetime_raw,
                "place_order_datetime_raw": row.place_order_datetime_raw,
            }
        )
    return [OrderLedgerItem.model_validate(x) for x in items]


def get_total_revenue(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
    revenue_basis: RevenueBasis = "payment",
) -> Decimal:
    raw_items = get_orders_raw(
        db,
        start_date=start_date,
        end_date=end_date,
        revenue_basis=revenue_basis,
    )
    return Decimal(sum(item.net_revenue for item in raw_items))


def _orders_for_payment_window(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[Order]:
    """시간대·히트맵: 기간 필터는 `payment_business_date`, 시각은 `payment_date`만 사용."""
    stmt = select(Order).order_by(Order.payment_date.desc())
    rows = db.scalars(stmt).all()
    out: list[Order] = []
    seen: set[str] = set()
    for row in rows:
        if not is_valid_order_status(normalize_order_status(row.order_status)):
            continue
        bd = row.payment_business_date or row.business_date
        if start_date and bd < start_date.date():
            continue
        if end_date and bd > end_date.date():
            continue
        if row.order_id in seen:
            continue
        seen.add(row.order_id)
        out.append(row)
    return out


def get_revenue_by_hour(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[HourRevenueRow]:
    rows = _orders_for_payment_window(db, start_date, end_date)
    buckets: dict[int, dict[str, Decimal]] = {}
    for row in rows:
        h = row.payment_date.hour
        if h not in buckets:
            buckets[h] = {"revenue": Decimal(0), "orders": 0}
        buckets[h]["revenue"] += Decimal(row.net_revenue)
        buckets[h]["orders"] += 1
    return [
        HourRevenueRow(hour=h, orders=buckets[h]["orders"], revenue=buckets[h]["revenue"])
        for h in sorted(buckets.keys())
    ]


def get_revenue_heatmap(
    db: Session,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[HeatmapCell]:
    """요일(`payment_business_date`) × 시(`payment_date` 시각) × 순매출."""
    rows = _orders_for_payment_window(db, start_date, end_date)
    cells: dict[tuple[int, int], Decimal] = {}
    for row in rows:
        bd = row.payment_business_date or row.business_date
        dow = bd.weekday()
        hr = row.payment_date.hour
        key = (dow, hr)
        cells[key] = cells.get(key, Decimal(0)) + Decimal(row.net_revenue)
    return [
        HeatmapCell(day_of_week=dow, hour=hr, revenue=rev)
        for (dow, hr), rev in sorted(cells.items())
    ]
