import logging
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order
from app.services.naver import fetch_naver_orders
from app.services.naver_orders_sync import calculate_business_date, to_kst_naive
from app.services.revenue_compute import compute_net_revenue

logger = logging.getLogger(__name__)
_LOOKBACK_MAX_HOURS = 24 * 30

VALID_ORDER_STATUSES = {
    "신규주문",
    "배송준비",
    "배송중",
    "배송완료",
    "구매확정",
}
CLAIM_STATUS_KEYWORDS = (
    "취소",
    "반품",
    "교환",
    "CANCEL",
    "RETURN",
    "EXCHANGE",
    "CANCELED",
    "CANCELLED",
)


def normalize_order_status(value: str) -> str:
    return (value or "").strip()


def is_valid_order_status(value: str) -> bool:
    return normalize_order_status(value) in VALID_ORDER_STATUSES


def is_claim_event_status(value: str) -> bool:
    s = normalize_order_status(value).upper()
    if not s:
        return False
    return any(k in s for k in CLAIM_STATUS_KEYWORDS)


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


def _resolve_lookback_hours() -> tuple[int, str]:
    """
    평시에는 고정 구간만 조회하고, 누락복구는 필요할 때만 별도 설정으로 실행한다.
    """
    base = max(1, int(settings.naver_commerce_order_lookback_hours))
    manual_backfill = max(0, int(settings.naver_backfill_lookback_hours))
    if manual_backfill > 0:
        return min(manual_backfill, _LOOKBACK_MAX_HOURS), "manual_backfill"
    return min(base, _LOOKBACK_MAX_HOURS), "regular"


def _fetch_orders_with_lookback(lookback_hours: int) -> list[dict]:
    """테스트 monkeypatch 호환: kwargs 미지원 목 함수면 기본 호출로 폴백."""
    try:
        return fetch_naver_orders(lookback_hours=lookback_hours)
    except TypeError:
        return fetch_naver_orders()


def _coerce_quantity(payload: dict[str, Any]) -> int:
    """API별 필드 차이를 흡수해 수량을 정수로 추출한다."""
    for key in ("quantity", "productOrderQuantity", "orderQuantity", "productCount"):
        if key in payload and payload.get(key) not in (None, ""):
            try:
                return int(payload.get(key))
            except (TypeError, ValueError):
                continue
    return 0


def _fallback_payment_datetime(payload: dict[str, Any]) -> datetime | None:
    """
    paymentDate 누락 시 누락구간 백필을 위해 대체 시각을 선택한다.
    우선순위: orderDate -> placeOrderDate -> sendDate
    """
    for key in ("orderDate", "placeOrderDate", "sendDate"):
        dt = _api_datetime_for_db(_parse_api_datetime(payload.get(key)))
        if dt is not None:
            return dt
    return None


def _pstr(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "")


def _pint(payload: dict[str, Any], key: str) -> int:
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _fill_missing_extended_columns(order: Order, payload: dict[str, Any]) -> None:
    """기존 주문에서 비어있는 확장 컬럼만 보강(원본 핵심 값은 유지)."""
    str_fields = {
        "shipped_date_raw": "sendDate",
        "order_detail_status": "orderDetailStatus",
        "pay_location_type": "payLocationType",
        "product_no": "productNo",
        "product_type": "productType",
        "option_code": "optionCode",
        "dispatch_due_date_raw": "dispatchDueDateRaw",
        "delivery_fee_type": "deliveryFeeType",
        "delivery_bundle_group_no": "deliveryBundleGroupNo",
        "delivery_fee_pay_type": "deliveryFeePayType",
        "receiver_contact1": "receiverContact1",
        "integrated_shipping_address": "integratedShippingAddress",
        "buyer_contact": "buyerContact",
        "shipping_message": "shippingMessage",
        "payment_method": "paymentMethod",
    }
    int_fields = {
        "option_price": "optionPrice",
        "product_price": "productPrice",
        "final_product_discount_amount": "finalProductDiscountAmount",
        "seller_discount_amount": "sellerDiscountAmount",
        "final_order_amount": "finalOrderAmount",
        "delivery_fee_amount": "deliveryFeeAmount",
        "jeju_island_extra_fee": "jejuIslandExtraFee",
        "delivery_fee_discount_amount": "deliveryFeeDiscountAmount",
        "naverpay_order_commission": "naverpayOrderCommission",
        "sales_integration_commission": "salesIntegrationCommission",
        "expected_settlement_amount": "expectedSettlementAmount",
    }
    for model_field, payload_key in str_fields.items():
        current = str(getattr(order, model_field, "") or "")
        if current:
            continue
        incoming = _pstr(payload, payload_key)
        if incoming:
            setattr(order, model_field, incoming)
    for model_field, payload_key in int_fields.items():
        current = int(getattr(order, model_field, 0) or 0)
        if current != 0:
            continue
        incoming = _pint(payload, payload_key)
        if incoming != 0:
            setattr(order, model_field, incoming)


def _merge_timeline_from_payload(order: Order, payload: dict[str, Any]) -> None:
    """동일 상품주문번호 재동기화 시 결제·발주·발송·주문일시·상태·라인(금액·수량·상품)을 보강한다."""
    if "orderDateRaw" in payload:
        order.order_datetime_raw = str(payload.get("orderDateRaw") or "")
    if "paymentDateRaw" in payload:
        order.payment_datetime_raw = str(payload.get("paymentDateRaw") or "")
    if "placeOrderDateRaw" in payload:
        order.place_order_datetime_raw = str(payload.get("placeOrderDateRaw") or "")

    if "productName" in payload and payload.get("productName") is not None:
        order.product_name = str(payload.get("productName") or "")
    if "optionName" in payload and payload.get("optionName") is not None:
        order.option_name = str(payload.get("optionName") or "")
    if "quantity" in payload and payload.get("quantity") is not None:
        try:
            order.quantity = max(0, int(payload.get("quantity") or 0))
        except (TypeError, ValueError):
            pass
    if "paymentAmount" in payload and payload.get("paymentAmount") is not None:
        try:
            order.amount = int(payload.get("paymentAmount") or 0)
        except (TypeError, ValueError):
            pass
    if "ordererName" in payload and (payload.get("ordererName") or "").strip():
        order.buyer_name = str(payload.get("ordererName") or "").strip()
    if "ordererId" in payload and payload.get("ordererId") is not None:
        order.buyer_id = str(payload.get("ordererId") or "").strip()
    if "receiverName" in payload and (payload.get("receiverName") or "").strip():
        order.receiver_name = str(payload.get("receiverName") or "").strip()
    if "shippingAddress" in payload and payload.get("shippingAddress") is not None:
        order.address = str(payload.get("shippingAddress") or "")

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


def _merge_claim_only(order: Order, payload: dict[str, Any]) -> None:
    """기존 상품주문번호는 원본 고정, 교환/반품/취소 이벤트일 때만 최소 필드 갱신."""
    st = normalize_order_status(payload.get("orderStatus", ""))
    if st:
        order.order_status = st

    order.refund_amount = int(payload.get("refundAmount") or 0)
    order.cancel_amount = int(payload.get("cancelAmount") or 0)
    order.net_revenue = compute_net_revenue(order.amount, order.refund_amount, order.cancel_amount)
    order.order_detail_status = _pstr(payload, "orderDetailStatus")


def sync_orders(db: Session) -> int:
    """네이버에서 상품주문 단위 목록을 가져와 DB에 반영한다.

    - `order_id`는 네이버 **상품주문번호**(productOrderId). DB에 없으면 신규 Insert, 있으면 타임라인만 merge.
    - 반환값은 이번 호출에서 **새로 Insert된 행 수**(기존 행 갱신은 포함하지 않음).
    """
    lookback_hours, lookback_mode = _resolve_lookback_hours()
    payloads = _fetch_orders_with_lookback(lookback_hours)
    inserted_count = 0
    skipped_existing_static = 0
    merged_claim_existing = 0
    invalid_status = Counter()
    skipped_bad_qty = 0
    fixed_zero_qty = 0
    skipped_missing_payment = 0
    fallback_payment_used = 0

    for payload in payloads:
        order_id = payload["orderId"]
        existing = db.scalar(select(Order).where(Order.order_id == order_id))
        if existing is not None:
            _fill_missing_extended_columns(existing, payload)
            st = normalize_order_status(payload.get("orderStatus", ""))
            has_claim_amount = int(payload.get("refundAmount") or 0) > 0 or int(
                payload.get("cancelAmount") or 0
            ) > 0
            if is_claim_event_status(st) or has_claim_amount:
                _merge_claim_only(existing, payload)
                merged_claim_existing += 1
            else:
                skipped_existing_static += 1
            continue

        order_status = normalize_order_status(payload.get("orderStatus", ""))
        if not is_valid_order_status(order_status):
            invalid_status[order_status or "(empty)"] += 1
            continue

        quantity = _coerce_quantity(payload)
        amount = int(payload["paymentAmount"])
        if amount < 0:
            # Guardrail: skip suspicious payloads to avoid polluted analytics rows.
            skipped_bad_qty += 1
            continue
        if quantity <= 0:
            # Naver 응답 변형/누락으로 quantity가 0으로 들어오면 신규 주문이 통째로 누락될 수 있다.
            # 집계 연속성을 위해 최소 1로 보정하고, 로그 카운트로 관찰한다.
            quantity = 1
            fixed_zero_qty += 1

        raw_pd = payload.get("paymentDate")
        payment_stored: datetime | None = None
        if raw_pd is not None and str(raw_pd).strip():
            try:
                payment_stored = parse_payment_datetime_string(str(raw_pd))
            except ValueError:
                payment_stored = None
        if payment_stored is None:
            payment_stored = _fallback_payment_datetime(payload)
            if payment_stored is not None:
                fallback_payment_used += 1
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
            shipped_date_raw=str(payload.get("sendDate") or ""),
            order_detail_status=_pstr(payload, "orderDetailStatus"),
            pay_location_type=_pstr(payload, "payLocationType"),
            product_no=_pstr(payload, "productNo"),
            product_type=_pstr(payload, "productType"),
            option_code=_pstr(payload, "optionCode"),
            option_price=_pint(payload, "optionPrice"),
            product_price=_pint(payload, "productPrice"),
            final_product_discount_amount=_pint(payload, "finalProductDiscountAmount"),
            seller_discount_amount=_pint(payload, "sellerDiscountAmount"),
            final_order_amount=_pint(payload, "finalOrderAmount"),
            dispatch_due_date_raw=_pstr(payload, "dispatchDueDateRaw"),
            delivery_fee_type=_pstr(payload, "deliveryFeeType"),
            delivery_bundle_group_no=_pstr(payload, "deliveryBundleGroupNo"),
            delivery_fee_pay_type=_pstr(payload, "deliveryFeePayType"),
            delivery_fee_amount=_pint(payload, "deliveryFeeAmount"),
            jeju_island_extra_fee=_pint(payload, "jejuIslandExtraFee"),
            delivery_fee_discount_amount=_pint(payload, "deliveryFeeDiscountAmount"),
            receiver_contact1=_pstr(payload, "receiverContact1"),
            integrated_shipping_address=_pstr(payload, "integratedShippingAddress"),
            buyer_contact=_pstr(payload, "buyerContact"),
            shipping_message=_pstr(payload, "shippingMessage"),
            payment_method=_pstr(payload, "paymentMethod"),
            naverpay_order_commission=_pint(payload, "naverpayOrderCommission"),
            sales_integration_commission=_pint(payload, "salesIntegrationCommission"),
            expected_settlement_amount=_pint(payload, "expectedSettlementAmount"),
        )
        db.add(order)
        inserted_count += 1

    db.commit()
    logger.info(
        "sync_orders: lookback_mode=%s lookback_hours=%s fetched=%s inserted=%s merged_claim_existing=%s skipped_existing_static=%s "
        "skipped_invalid_status=%s skipped_bad_qty=%s fixed_zero_qty=%s fallback_payment_used=%s skipped_missing_payment=%s",
        lookback_mode,
        lookback_hours,
        len(payloads),
        inserted_count,
        merged_claim_existing,
        skipped_existing_static,
        sum(invalid_status.values()),
        skipped_bad_qty,
        fixed_zero_qty,
        fallback_payment_used,
        skipped_missing_payment,
    )
    if invalid_status:
        logger.warning(
            "sync_orders: 신규 중 허용되지 않은 orderStatus(저장 안 함): %s — "
            "네이버 API 코드가 PAYED 등으로 매핑되는지 app/services/naver.py _STATUS_MAP 확인",
            dict(invalid_status.most_common(20)),
        )
    return inserted_count
