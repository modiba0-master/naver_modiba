import base64
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import httpx

from app.config import settings

_STATUS_MAP = {
    "PAYED": "신규주문",
    "DELIVERY_READY": "배송준비",
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
    if isinstance(value, str) and value:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return datetime.now(timezone.utc).isoformat()


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
    return {
        "orderId": str(
            _get_value(item, "productOrder.productOrderId", "productOrderId", "orderId", "id")
            or ""
        ),
        "productName": str(
            _get_value(item, "productOrder.productName", "productName", "product.productName")
            or ""
        ),
        "optionName": str(option_name),
        "quantity": int(_get_value(item, "productOrder.quantity", "quantity", "orderQuantity") or quantity),
        "paymentAmount": int(amount),
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
        "paymentDate": _to_iso_datetime(
            _get_value(
                item,
                "order.paymentDate",
                "paymentDate",
                "paymentDateTime",
                "lastChangedDate",
            )
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

# 실제 테스트로 확인된 지원 타입:
#   PAYED            - 결제완료 시각 기준 (신규 주문 수집용)
#   PURCHASE_DECIDED - 구매확정 시각 기준 (발주 지연으로 PAYED에서 누락된 주문 보완)
# DELIVERY_READY / DELIVERING / DELIVERED / CANCEL_REQUEST 는 400 반환 → 미지원
_SYNC_STATUS_TYPES = ["PAYED", "PURCHASE_DECIDED"]

# Rate Limit(429) 발생 시 대기 후 재시도 횟수 및 간격
_RATE_LIMIT_RETRY = 3
_RATE_LIMIT_SLEEP = 2.0  # seconds


def _fmt_kst(dt: datetime) -> str:
    s = dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")
    return s[:-2] + ":" + s[-2:]


def _fetch_order_nos_in_window(
    client: httpx.Client, access_token: str, window_from: datetime, window_to: datetime
) -> list[str]:
    """네이버 API 단일 24h 윈도우에서 PAYED+PURCHASE_DECIDED 주문번호를 수집한다.
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


def fetch_naver_orders() -> list[dict]:
    """Railway 고정 Outbound IP 환경에서 네이버 API를 직접 호출한다.

    네이버 last-changed-statuses API는 단일 요청의 최대 시간 범위가 24시간이므로
    lookback_hours가 24를 초과하면 24시간 단위로 나눠 여러 번 호출한다.
    """
    base_url = settings.naver_commerce_api_base_url.rstrip("/")
    lookback_hours = max(settings.naver_commerce_order_lookback_hours, 1)
    now_kst = datetime.now(KST)
    total_from = now_kst - timedelta(hours=lookback_hours)

    # 24시간 단위 윈도우 목록 생성
    windows: list[tuple[datetime, datetime]] = []
    window_end = now_kst
    while window_end > total_from:
        window_start = max(window_end - timedelta(hours=_NAVER_API_MAX_WINDOW_HOURS), total_from)
        windows.append((window_start, window_end))
        window_end = window_start
    windows.reverse()

    all_order_nos: list[str] = []
    with httpx.Client(base_url=base_url, timeout=30) as client:
        access_token = _get_access_token(client)
        for w_from, w_to in windows:
            nos = _fetch_order_nos_in_window(client, access_token, w_from, w_to)
            all_order_nos.extend(nos)

        if not all_order_nos:
            return []

        # 중복 제거
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
