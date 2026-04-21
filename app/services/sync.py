from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.services.naver import fetch_naver_orders

KST = ZoneInfo("Asia/Seoul")

VALID_ORDER_STATUSES = {
    "신규주문",
    "배송준비",
    "배송중",
    "배송완료",
    "구매확정",
}


def normalize_order_status(value: str) -> str:
    return (value or "").strip()


def is_valid_order_status(value: str) -> bool:
    return normalize_order_status(value) in VALID_ORDER_STATUSES


def _parse_payment_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_api_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def calculate_business_date(payment_date: datetime) -> date:
    """
    매출 귀속일(일자별 집계 키): 결제 시각을 한국시간(KST)으로 본 뒤
    당일 00:00~15:59 → 그날, 16:00 이후 → 익일.(요일·주말 별도 귀속 없음)
    """
    if payment_date.tzinfo is None:
        local = payment_date.replace(tzinfo=KST)
    else:
        local = payment_date.astimezone(KST)
    cutoff = time(hour=16, minute=0)
    if local.time() < cutoff:
        return local.date()
    return local.date() + timedelta(days=1)


def _merge_timeline_from_payload(order: Order, payload: dict[str, Any]) -> None:
    """동일 상품주문번호 재동기화 시 결제·발주·발송·주문일시·상태를 보강한다."""
    pd_raw = payload.get("paymentDate")
    if pd_raw:
        payment_date = _parse_payment_date(str(pd_raw))
        order.payment_date = payment_date
        order.business_date = calculate_business_date(payment_date)
    od = _parse_api_datetime(payload.get("orderDate"))
    if od:
        order.ordered_at = od
        order.order_date = od.date()
    po = _parse_api_datetime(payload.get("placeOrderDate"))
    if po:
        order.placed_order_at = po
    sd = _parse_api_datetime(payload.get("sendDate"))
    if sd:
        order.shipped_at = sd
    st = normalize_order_status(payload.get("orderStatus", ""))
    if is_valid_order_status(st):
        order.order_status = st
    co = (payload.get("contentOrderNo") or "").strip()
    if co:
        order.content_order_no = co


def sync_orders(db: Session) -> int:
    payloads = fetch_naver_orders()
    inserted_count = 0

    for payload in payloads:
        order_id = payload["orderId"]
        existing = db.scalar(select(Order).where(Order.order_id == order_id))
        if existing is not None:
            _merge_timeline_from_payload(existing, payload)
            continue

        order_status = normalize_order_status(payload.get("orderStatus", ""))
        if not is_valid_order_status(order_status):
            continue

        quantity = int(payload["quantity"])
        amount = int(payload["paymentAmount"])
        if quantity <= 0 or amount < 0:
            # Guardrail: skip suspicious payloads to avoid polluted analytics rows.
            continue

        payment_date = _parse_payment_date(payload["paymentDate"])
        business_date = calculate_business_date(payment_date)
        ordered_at = _parse_api_datetime(payload.get("orderDate"))
        placed_order_at = _parse_api_datetime(payload.get("placeOrderDate"))
        shipped_at = _parse_api_datetime(payload.get("sendDate"))
        order_calendar = ordered_at.date() if ordered_at else payment_date.date()
        content_no = (payload.get("contentOrderNo") or "").strip() or None

        order = Order(
            order_id=order_id,
            content_order_no=content_no,
            product_name=payload["productName"],
            option_name=payload["optionName"],
            quantity=quantity,
            amount=amount,
            buyer_name=payload["ordererName"],
            buyer_id=payload["ordererId"],
            receiver_name=payload["receiverName"],
            address=payload["shippingAddress"],
            order_status=order_status,
            payment_date=payment_date,
            order_date=order_calendar,
            business_date=business_date,
            ordered_at=ordered_at,
            placed_order_at=placed_order_at,
            shipped_at=shipped_at,
        )
        db.add(order)
        inserted_count += 1

    db.commit()
    return inserted_count
