import base64
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import httpx

from app.config import settings

# 커머스API productOrderStatus (문서 기준 코드). 미등록 코드는 그대로 두면 sync에서 화이트리스트로 걸림.
_STATUS_MAP = {
    "PAYMENT_WAITING": "결제대기",  # 미결제 — DB 신규 INSERT 대상 아님(의도)
    "PAYED": "신규주문",
    "DELIVERY_READY": "배송준비",  # 구버전/일부 응답 호환
    "DELIVERING": "배송중",
    "DELIVERED": "배송완료",
    "PURCHASE_DECIDED": "구매확정",
}
KST = timezone(timedelta(hours=9))


def _get_value(payload: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        node: Any = payload
        missing = False
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                missing = True
                break
            node = node[key]
        if not missing and node not in (None, ""):
            return node
    return None


def _to_iso_datetime(value: Any) -> str:
    """API 시각을 문자열로 통과. 없으면 빈 문자열 — 결제일시 누락 시 `datetime.now()`로 채우지 않음(잘못된 집계 방지)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, datetime):
        return value.isoformat()
    return ""


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip()
    return _STATUS_MAP.get(status, status or "신규주문")


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in (
            "contents",
            "items",
            "productOrders",
            "orders",
            "lastChangeStatuses",
        ):
            maybe_items = data.get(key)
            if isinstance(maybe_items, list):
                return [item for item in maybe_items if isinstance(item, dict)]
    return []


def _extract_changed_order_nos(payload: dict[str, Any]) -> list[str]:
    items = _extract_items(payload)
    order_nos: list[str] = []
    for item in items:
        order_no = _get_value(
            item,
            "productOrderNo",
            "productOrderId",
            "orderId",
            "product_order_no",
        )
        if order_no:
            order_nos.append(str(order_no))
    return order_nos


def _to_internal_order(item: dict[str, Any]) -> dict[str, Any]:
    option_name = _get_value(
        item,
        "productOrder.productOption",
        "productOrder.optionCode",
        "productOption.optionName",
        "optionName",
        "option.optionName",
    ) or ""
    quantity = _get_value(item, "quantity", "productOrderQuantity", "orderQuantity") or 0
    amount = _get_value(
        item,
        "productOrder.totalPaymentAmount",
        "paymentAmount",
        "totalPaymentAmount",
        "amount",
    ) or 0
    refund_raw = _get_value(
        item,
        "productOrder.refundAmount",
        "productOrder.totalRefundAmount",
        "refundAmount",
        "totalRefundAmount",
        "refundPaymentAmount",
    )
    cancel_raw = _get_value(
        item,
        "productOrder.cancelAmount",
        "cancelAmount",
        "cancelPaymentAmount",
    )
    try:
        refund_amt = int(refund_raw or 0)
    except (TypeError, ValueError):
        refund_amt = 0
    try:
        cancel_amt = int(cancel_raw or 0)
    except (TypeError, ValueError):
        cancel_amt = 0
    return {
        # 상품주문번호(줄 단위 1:1). order.orderId 등 상위 주문번호와 혼동하지 않도록 productOrder 우선.
        "orderId": str(
            _get_value(
                item,
                "productOrder.productOrderId",
                "productOrderId",
                "id",
            )
            or ""
        ),
        # 주문번호(결제/장바구니 단위 1:n 상품줄)
        "contentOrderNo": str(
            _get_value(
                item,
                "order.orderId",
                "order.contentOrderNo",
                "contentOrderNo",
            )
            or ""
        ).strip(),
        "productName": str(
            _get_value(item, "productOrder.productName", "productName", "product.productName")
            or ""
        ),
        "optionName": str(option_name),
        "quantity": int(_get_value(item, "productOrder.quantity", "quantity", "orderQuantity") or quantity),
        "paymentAmount": int(amount),
        "refundAmount": refund_amt,
        "cancelAmount": cancel_amt,
        "orderStatus": _normalize_status(
            _get_value(
                item,
                "productOrder.productOrderStatus",
                "productOrderStatus",
                "orderStatus",
                "claimStatus",
            )
        ),
        "ordererName": str(
            _get_value(item, "order.ordererName", "ordererName", "orderer.name", "buyerName")
            or ""
        ),
        "ordererId": str(
            _get_value(item, "order.ordererId", "ordererId", "orderer.id", "buyerId") or ""
        ),
        "receiverName": str(
            _get_value(
                item,
                "productOrder.shippingAddress.name",
                "shippingAddress.name",
                "receiverName",
                "receiver.name",
            )
            or ""
        ),
        "shippingAddress": str(
            _get_value(
                item,
                "productOrder.shippingAddress.baseAddress",
                "shippingAddress.baseAddress",
                "shippingAddress.address1",
                "shippingAddress",
                "address",
            )
            or ""
        ),
        # 결제는 주문(장바구니) 단위 1회 → 같은 주문번호의 상품주문(줄)마다 API가 동일한 paymentDate를 반복(가공 아님).
        # lastChangedDate는 결제 시각과 다를 수 있어 결제일시 대용으로 쓰지 않음.
        "paymentDate": _to_iso_datetime(
            _get_value(
                item,
                "order.paymentDate",
                "paymentDate",
                "paymentDateTime",
            )
        ),
        # 주문/발주/발송 시각(상세 API 필드; 없으면 None → sync에서 생략)
        "orderDate": _get_value(
            item,
            "order.orderDate",
            "productOrder.orderDate",
            "orderDate",
        ),
        "placeOrderDate": _get_value(
            item,
            "productOrder.placeOrderDate",
            "placeOrderDate",
        ),
        "sendDate": _get_value(
            item,
            "productOrder.delivery.sendDate",
            "delivery.sendDate",
            "productOrder.sendDate",
            "sendDate",
        ),
    }


def _generate_client_secret_sign(
    client_id: str,
    client_secret: str,
    timestamp_ms: int,
) -> str:
    raw = f"{client_id}_{timestamp_ms}".encode("utf-8")
    hashed = bcrypt.hashpw(raw, client_secret.encode("utf-8"))
    return base64.b64encode(hashed).decode("utf-8")


def _resolve_client_credentials() -> tuple[str, str]:
    client_id = settings.naver_commerce_api_client_id or settings.naver_client_id
    client_secret = (
        settings.naver_commerce_api_client_secret or settings.naver_client_secret
    )
    if not client_id or not client_secret:
        raise RuntimeError(
            "네이버 커머스 API 인증값이 없습니다. "
            "NAVER_COMMERCE_API_CLIENT_ID/SECRET 또는 NAVER_CLIENT_ID/SECRET을 설정하세요."
        )
    return client_id, client_secret


def _log_naver_403(response: httpx.Response) -> None:
    if response.status_code == 403:
        print("[NAVER 403 ERROR]")
        print(response.text)


def _print_naver_trace_from_json(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        data = response.json()
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    trace_id = data.get("traceId") or data.get("trace_id")
    if trace_id:
        print(f"[NAVER TRACE] {trace_id}")


def _raise_http_error(prefix: str, response: httpx.Response) -> None:
    _log_naver_403(response)
    _print_naver_trace_from_json(response)
    body = response.text[:500]
    trace_id = response.headers.get("GNCP-GW-Trace-ID", "")
    response_time_ms = response.headers.get("GNCP-GW-HttpClient-ResponseTime", "")
    raise RuntimeError(
        f"{prefix}: status={response.status_code}, trace_id={trace_id}, "
        f"response_time_ms={response_time_ms}, body={body}"
    )


_outbound_ip_logged = False


def _log_outbound_ip_once() -> None:
    global _outbound_ip_logged
    if _outbound_ip_logged:
        return
    try:
        ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
        logger = __import__("logging").getLogger(__name__)
        logger.info("Naver API outbound IP: %s", ip)
    except Exception:
        pass
    _outbound_ip_logged = True


def _get_access_token(client: httpx.Client) -> str:
    _log_outbound_ip_once()
    client_id, client_secret = _resolve_client_credentials()

    last_response: httpx.Response | None = None
    for _ in range(2):
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        sign = _generate_client_secret_sign(client_id, client_secret, timestamp_ms)
        response = client.post(
            "/external/v1/oauth2/token",
            data={
                "client_id": client_id,
                "timestamp": str(timestamp_ms),
                "client_secret_sign": sign,
                "grant_type": "client_credentials",
                "type": settings.naver_commerce_oauth_type,
            },
        )
        if response.is_success:
            token = response.json().get("access_token")
            if not token:
                _raise_http_error("네이버 커머스 인증 토큰 응답에 access_token이 없습니다", response)
            return str(token)
        last_response = response

    if last_response is not None:
        _raise_http_error("네이버 커머스 인증 토큰 발급 실패", last_response)
    raise RuntimeError("네이버 커머스 인증 토큰 발급 실패")


_NAVER_API_MAX_WINDOW_HOURS = 24


class PaymentRangeQueryError(Exception):
    """조건형 GET /product-orders(결제일시 구간) 미지원·파라미터 오류 시 last_changed 로 폴백한다."""


# last_changed 모드 전용. PAYED 변경 시각 기준이라 결제일과 어긋나 누락될 수 있음.
_SYNC_STATUS_TYPES = ["PAYED"]

# Rate Limit(429) 발생 시 대기 후 재시도 횟수 및 간격
_RATE_LIMIT_RETRY = 3
_RATE_LIMIT_SLEEP = 2.0  # seconds


def _fmt_kst(dt: datetime) -> str:
    s = dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")
    return s[:-2] + ":" + s[-2:]


def _fetch_order_nos_in_window(
    client: httpx.Client, access_token: str, window_from: datetime, window_to: datetime
) -> list[str]:
    """네이버 API 단일 24h 윈도우에서 PAYED 주문번호를 수집한다.
    - 400: 미지원 타입 → skip
    - 429: Rate Limit → 재시도
    """
    import logging as _logging
    import time as _time
    _log = _logging.getLogger(__name__)

    all_nos: list[str] = []
    for status_type in _SYNC_STATUS_TYPES:
        for attempt in range(_RATE_LIMIT_RETRY):
            resp = client.get(
                "/external/v1/pay-order/seller/product-orders/last-changed-statuses",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "lastChangedType": status_type,
                    "lastChangedFrom": _fmt_kst(window_from),
                    "lastChangedTo": _fmt_kst(window_to),
                    "limitCount": 300,
                },
            )
            _log_naver_403(resp)
            _print_naver_trace_from_json(resp)

            if resp.status_code == 400:
                _log.warning("lastChangedType=%s 미지원(400); skip.", status_type)
                break
            if resp.status_code == 429:
                wait = _RATE_LIMIT_SLEEP * (attempt + 1)
                _log.warning(
                    "lastChangedType=%s Rate Limit(429); %.1fs 후 재시도 (%d/%d)",
                    status_type, wait, attempt + 1, _RATE_LIMIT_RETRY,
                )
                _time.sleep(wait)
                continue

            resp.raise_for_status()
            all_nos.extend(_extract_changed_order_nos(resp.json()))
            break  # 성공

    return all_nos


def _build_lookback_windows(now_kst: datetime, total_from: datetime) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    window_end = now_kst
    while window_end > total_from:
        window_start = max(window_end - timedelta(hours=_NAVER_API_MAX_WINDOW_HOURS), total_from)
        windows.append((window_start, window_end))
        window_end = window_start
    windows.reverse()
    return windows


def _fetch_raw_orders_payment_window(
    client: httpx.Client,
    access_token: str,
    window_from: datetime,
    window_to: datetime,
    *,
    first_window_first_page: bool,
) -> list[dict[str, Any]]:
    """GET product-orders: rangeType=PAYED_DATETIME, 단일 윈도우 최대 24h. 페이지네이션."""
    import logging as _logging
    import time as _time

    _log = _logging.getLogger(__name__)
    out: list[dict[str, Any]] = []
    page = 1
    max_pages = 500  # 비정상 페이지네이션 시 무한 루프 방지
    while page <= max_pages:
        last_resp: httpx.Response | None = None
        for attempt in range(_RATE_LIMIT_RETRY):
            resp = client.get(
                "/external/v1/pay-order/seller/product-orders",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "rangeType": "PAYED_DATETIME",
                    "from": _fmt_kst(window_from),
                    "to": _fmt_kst(window_to),
                    "page": page,
                    "size": 300,
                },
            )
            last_resp = resp
            _log_naver_403(resp)
            _print_naver_trace_from_json(resp)

            if resp.status_code == 400:
                if first_window_first_page and page == 1:
                    raise PaymentRangeQueryError(resp.text[:500])
                _log.warning(
                    "product-orders PAYED_DATETIME 윈도우 page=%s 400; 이 윈도우는 건너뜀.",
                    page,
                )
                return out
            if resp.status_code == 429:
                wait = _RATE_LIMIT_SLEEP * (attempt + 1)
                _log.warning(
                    "product-orders Rate Limit(429); %.1fs 후 재시도 (%d/%d)",
                    wait,
                    attempt + 1,
                    _RATE_LIMIT_RETRY,
                )
                _time.sleep(wait)
                continue

            resp.raise_for_status()
            body = resp.json()
            chunk = _extract_items(body)
            out.extend(chunk)

            pag: dict[str, Any] = {}
            data = body.get("data")
            if isinstance(data, dict) and isinstance(data.get("pagination"), dict):
                pag = data["pagination"]
            has_next = pag.get("hasNext")

            if has_next is True:
                page += 1
                break
            if has_next is False:
                return out
            if len(chunk) < 300:
                return out
            page += 1
            break
        else:
            if last_resp is None:
                raise RuntimeError("product-orders 결제일시 조회: 응답 없음")
            _raise_http_error("product-orders 결제일시 조회 재시도 소진", last_resp)

    _log.warning("product-orders PAYED_DATETIME page 상한(%s) 도달", max_pages)
    return out


def _fetch_naver_orders_via_last_changed() -> list[dict]:
    """변경일시(last-changed) → 상품주문번호 수집 → query 상세."""
    base_url = settings.naver_commerce_api_base_url.rstrip("/")
    lookback_hours = max(settings.naver_commerce_order_lookback_hours, 1)
    now_kst = datetime.now(KST)
    total_from = now_kst - timedelta(hours=lookback_hours)
    windows = _build_lookback_windows(now_kst, total_from)

    all_order_nos: list[str] = []
    with httpx.Client(base_url=base_url, timeout=30) as client:
        access_token = _get_access_token(client)
        for w_from, w_to in windows:
            nos = _fetch_order_nos_in_window(client, access_token, w_from, w_to)
            all_order_nos.extend(nos)

        if not all_order_nos:
            return []

        all_order_nos = list(dict.fromkeys(all_order_nos))

        detail_response = client.post(
            "/external/v1/pay-order/seller/product-orders/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"productOrderIds": all_order_nos},
        )
        _log_naver_403(detail_response)
        _print_naver_trace_from_json(detail_response)
        detail_response.raise_for_status()
        raw_items = _extract_items(detail_response.json())

        normalized = [_to_internal_order(item) for item in raw_items]
        return [item for item in normalized if item["orderId"]]


def _fetch_naver_orders_via_payment_datetime() -> list[dict]:
    """결제일시(PAYED_DATETIME) 구간으로 상세를 직접 받는다. last_changed 누락(결제·변경 시각 불일치) 완화.

    - API당 최대 24h 구간 → lookback을 24h 윈도로 쪼갬.
    - `productOrderStatuses` 미지정: 배송완료 등 상태도 구간 안에 있으면 포함(공식 가이드).
    """
    import logging as _logging

    _log = _logging.getLogger(__name__)
    base_url = settings.naver_commerce_api_base_url.rstrip("/")
    lookback_hours = max(settings.naver_commerce_order_lookback_hours, 1)
    now_kst = datetime.now(KST)
    total_from = now_kst - timedelta(hours=lookback_hours)
    windows = _build_lookback_windows(now_kst, total_from)

    raw_items: list[dict[str, Any]] = []
    with httpx.Client(base_url=base_url, timeout=60) as client:
        access_token = _get_access_token(client)
        for idx, (w_from, w_to) in enumerate(windows):
            chunk = _fetch_raw_orders_payment_window(
                client,
                access_token,
                w_from,
                w_to,
                first_window_first_page=(idx == 0),
            )
            raw_items.extend(chunk)
            _log.info(
                "product-orders PAYED_DATETIME window %s~%s rows=%s (누적 %s)",
                w_from,
                w_to,
                len(chunk),
                len(raw_items),
            )

    seen: dict[str, dict[str, Any]] = {}
    for it in raw_items:
        oid = str(
            _get_value(
                it,
                "productOrder.productOrderId",
                "productOrderId",
                "productOrderNo",
            )
            or ""
        )
        if oid:
            seen[oid] = it
    deduped = list(seen.values())

    normalized = [_to_internal_order(item) for item in deduped]
    return [item for item in normalized if item["orderId"]]


def fetch_naver_orders() -> list[dict]:
    """네이버 커머스에서 상품주문 단위 목록을 가져온다.

    기본(`NAVER_ORDER_SYNC_MODE=payment_datetime`): **결제일시** 구간 조회 API로
    lookback 구간을 24시간 단위로 채운 뒤, 날짜 기준 누락을 줄인다.

    `last_changed`: 기존처럼 변경일시 API + query(결제는 됐는데 PAYED 변경이 창 밖이면 누락 가능).
    """
    import logging as _logging

    _log = _logging.getLogger(__name__)
    mode = (settings.naver_order_sync_mode or "payment_datetime").strip().lower()
    if mode == "last_changed":
        return _fetch_naver_orders_via_last_changed()
    try:
        return _fetch_naver_orders_via_payment_datetime()
    except PaymentRangeQueryError as exc:
        _log.warning(
            "결제일시 구간 조회 미사용/오류 → last_changed 로 폴백합니다: %s",
            exc,
        )
        return _fetch_naver_orders_via_last_changed()
