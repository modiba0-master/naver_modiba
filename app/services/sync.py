import logging
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.services.naver import fetch_naver_orders
from app.services.naver_orders_sync import calculate_business_date, to_kst_naive
from app.services.revenue_compute import compute_net_revenue

logger = logging.getLogger(__name__)

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


def parse_payment_datetime_string(value: str) -> datetime | None:
    """네이버 ``paymentDate`` → ``payment_date``(KST naive, 16시 영업일 로직 없음).

    - ``Z``/``z`` 없음: ``fromisoformat`` → naive면 그대로(KST로 간주), aware면 ``to_kst_naive``.
    - ``Z``/``z`` 있음: 접미사 제거 후 naive 파싱 → **+9시간**(UTC→KST 벽시계).

    빈 문자열이면 ``None``.
    """
    s = str(value).strip()
    if not s:
        return None

    if len(s) > 1 and s[-1] in ("Z", "z"):
        core = s[:-1]
        dt = datetime.fromisoformat(core)
        return dt + timedelta(hours=9)

    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        return to_kst_naive(dt)
    return dt


def _same_wallclock_payment(a: datetime, b: datetime) -> bool:
    """재동기화 시 API 문자열만 다시 파싱할 때, 동일 시각이면 DB 값을 유지한다(불필요한 덮어쓰기 방지)."""
    return a.replace(microsecond=0) == b.replace(microsecond=0)


def _apply_revenue_and_business_dates(
    order: Order,
    *,
    payment_stored: datetime | None,
    ordered_at: datetime | None,
    shipped_at: datetime | None,
) -> None:
    """``payment_date``는 원본 시각 유지 후, 그 값으로 ``business_date``(16시 규칙)·기타 영업일·순매출 반영."""
    if payment_stored is None:
        order.net_revenue = compute_net_revenue(
            order.amount, order.refund_amount, order.cancel_amount
        )
        return
    order.payment_date = payment_stored
    payment_bd = calculate_business_date(order.payment_date)
    order.business_date = payment_bd
    order.payment_business_date = payment_bd
    order.order_business_date = (
        calculate_business_date(ordered_at) if ordered_at else payment_bd
    )
    order.shipping_business_date = (
        calculate_business_date(shipped_at) if shipped_at else None
    )
    order.net_revenue = compute_net_revenue(
        order.amount, order.refund_amount, order.cancel_amount
    )


def _api_datetime_for_db(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return to_kst_naive(value)


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


def _merge_timeline_from_payload(order: Order, payload: dict[str, Any]) -> None:
    """동일 상품주문번호 재동기화 시 결제·발주·발송·주문일시·상태를 보강한다."""
    if "orderDateRaw" in payload:
        order.order_datetime_raw = str(payload.get("orderDateRaw") or "")
    if "paymentDateRaw" in payload:
        order.payment_datetime_raw = str(payload.get("paymentDateRaw") or "")
    if "placeOrderDateRaw" in payload:
        order.place_order_datetime_raw = str(payload.get("placeOrderDateRaw") or "")

    ra = int(payload.get("refundAmount") or 0)
    ca = int(payload.get("cancelAmount") or 0)
    order.refund_amount = ra
    order.cancel_amount = ca

    pd_raw = payload.get("paymentDate")
    if pd_raw:
        try:
            parsed = parse_payment_datetime_string(str(pd_raw))
        except ValueError:
            parsed = None
        if parsed is None:
            payment_stored = order.payment_date
        elif order.payment_date is not None and _same_wallclock_payment(
            parsed, order.payment_date
        ):
            payment_stored = order.payment_date
        else:
            payment_stored = parsed
    else:
        payment_stored = order.payment_date

    od = _parse_api_datetime(payload.get("orderDate"))
    if od:
        order.ordered_at = _api_datetime_for_db(od)
        order.order_date = order.ordered_at.date()
    po = _parse_api_datetime(payload.get("placeOrderDate"))
    if po:
        order.placed_order_at = _api_datetime_for_db(po)
    sd = _parse_api_datetime(payload.get("sendDate"))
    if sd:
        order.shipped_at = _api_datetime_for_db(sd)

    _apply_revenue_and_business_dates(
        order,
        payment_stored=payment_stored,
        ordered_at=order.ordered_at,
        shipped_at=order.shipped_at,
    )

    st = normalize_order_status(payload.get("orderStatus", ""))
    if is_valid_order_status(st):
        order.order_status = st
    co = (payload.get("contentOrderNo") or "").strip()
    if co:
        order.content_order_no = co


def sync_orders(db: Session) -> int:
    """네이버에서 상품주문 단위 목록을 가져와 DB에 반영한다.

    - `order_id`는 네이버 **상품주문번호**(productOrderId). DB에 없으면 신규 Insert, 있으면 타임라인만 merge.
    - 반환값은 이번 호출에서 **새로 Insert된 행 수**(기존 행 갱신은 포함하지 않음).
    """
    payloads = fetch_naver_orders()
    inserted_count = 0
    merged_existing = 0
    invalid_status = Counter()
    skipped_bad_qty = 0
    skipped_missing_payment = 0

    for payload in payloads:
        order_id = payload["orderId"]
        existing = db.scalar(select(Order).where(Order.order_id == order_id))
        if existing is not None:
            _merge_timeline_from_payload(existing, payload)
            merged_existing += 1
            continue

        order_status = normalize_order_status(payload.get("orderStatus", ""))
        if not is_valid_order_status(order_status):
            invalid_status[order_status or "(empty)"] += 1
            continue

        quantity = int(payload["quantity"])
        amount = int(payload["paymentAmount"])
        if quantity <= 0 or amount < 0:
            # Guardrail: skip suspicious payloads to avoid polluted analytics rows.
            skipped_bad_qty += 1
            continue

        raw_pd = payload.get("paymentDate")
        if raw_pd is None or not str(raw_pd).strip():
            skipped_missing_payment += 1
            continue
        try:
            payment_stored = parse_payment_datetime_string(str(raw_pd))
        except ValueError:
            skipped_missing_payment += 1
            continue
        if payment_stored is None:
            skipped_missing_payment += 1
            continue

        refund_amount = int(payload.get("refundAmount") or 0)
        cancel_amount = int(payload.get("cancelAmount") or 0)
        net_revenue = compute_net_revenue(amount, refund_amount, cancel_amount)

        ordered_at = _api_datetime_for_db(_parse_api_datetime(payload.get("orderDate")))
        placed_order_at = _api_datetime_for_db(
            _parse_api_datetime(payload.get("placeOrderDate"))
        )
        shipped_at = _api_datetime_for_db(_parse_api_datetime(payload.get("sendDate")))
        order_calendar = ordered_at.date() if ordered_at else payment_stored.date()
        content_no = (payload.get("contentOrderNo") or "").strip() or None

        row: dict[str, Any] = {"payment_date": payment_stored}
        row["business_date"] = calculate_business_date(row["payment_date"])
        payment_date: datetime = row["payment_date"]
        payment_bd: date = row["business_date"]
        order_bd = calculate_business_date(ordered_at) if ordered_at else payment_bd
        ship_bd = calculate_business_date(shipped_at) if shipped_at else None

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
            business_date=payment_bd,
            order_business_date=order_bd,
            payment_business_date=payment_bd,
            shipping_business_date=ship_bd,
            refund_amount=refund_amount,
            cancel_amount=cancel_amount,
            net_revenue=net_revenue,
            ordered_at=ordered_at,
            placed_order_at=placed_order_at,
            shipped_at=shipped_at,
            order_datetime_raw=str(payload.get("orderDateRaw") or ""),
            payment_datetime_raw=str(payload.get("paymentDateRaw") or ""),
            place_order_datetime_raw=str(payload.get("placeOrderDateRaw") or ""),
        )
        db.add(order)
        inserted_count += 1

    db.commit()
    logger.info(
        "sync_orders: fetched=%s inserted=%s merged_existing=%s skipped_invalid_status=%s "
        "skipped_bad_qty=%s skipped_missing_payment=%s",
        len(payloads),
        inserted_count,
        merged_existing,
        sum(invalid_status.values()),
        skipped_bad_qty,
        skipped_missing_payment,
    )
    if invalid_status:
        logger.warning(
            "sync_orders: 신규 중 허용되지 않은 orderStatus(저장 안 함): %s — "
            "네이버 API 코드가 PAYED 등으로 매핑되는지 app/services/naver.py _STATUS_MAP 확인",
            dict(invalid_status.most_common(20)),
        )
    return inserted_count
