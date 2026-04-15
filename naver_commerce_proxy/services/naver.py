import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.commerce.naver.com"
REQUEST_TIMEOUT_SEC = 5.0
_RETRY_BACKOFF_SEC = (1.0, 2.0, 4.0)
_MAX_ATTEMPTS = 4  # 1 try + 3 retries


def _status_should_retry(status_code: int) -> bool:
    return status_code >= 500 or status_code == 429


def _request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SEC)
    last: requests.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            if method.upper() == "GET":
                last = session.get(url, **kwargs)
            else:
                last = session.post(url, **kwargs)
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "%s %s attempt %s/%s transport error: %s",
                method,
                url,
                attempt + 1,
                _MAX_ATTEMPTS,
                exc,
            )
            if attempt + 1 >= _MAX_ATTEMPTS:
                raise NaverApiError(
                    f"HTTP request failed after retries: {exc}",
                    status_code=502,
                    response_body=str(exc),
                    trace_id="",
                ) from exc
            time.sleep(_RETRY_BACKOFF_SEC[attempt])
            continue

        if last.ok or not _status_should_retry(last.status_code):
            return last
        logger.warning(
            "%s %s attempt %s/%s HTTP %s",
            method,
            url,
            attempt + 1,
            _MAX_ATTEMPTS,
            last.status_code,
        )
        if attempt + 1 >= _MAX_ATTEMPTS:
            return last
        time.sleep(_RETRY_BACKOFF_SEC[attempt])

    assert last is not None
    return last


KST = timezone(timedelta(hours=9))
OAUTH_TYPE = os.getenv("NAVER_OAUTH_TYPE", "SELF")


class NaverApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response_body: str,
        trace_id: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.trace_id = trace_id


def _client_credentials() -> tuple[str, str]:
    client_id = (os.getenv("NAVER_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("NAVER_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise NaverApiError(
            "NAVER_CLIENT_ID and NAVER_CLIENT_SECRET must be set",
            status_code=500,
            response_body="",
            trace_id="",
        )
    return client_id, client_secret


def _generate_client_secret_sign(
    client_id: str, client_secret: str, timestamp_ms: int
) -> str:
    raw = f"{client_id}_{timestamp_ms}".encode("utf-8")
    hashed = bcrypt.hashpw(raw, client_secret.encode("utf-8"))
    return base64.b64encode(hashed).decode("utf-8")


def _trace_from_response(resp: requests.Response) -> str:
    tid = resp.headers.get("GNCP-GW-Trace-ID") or ""
    if tid:
        return tid
    try:
        j = resp.json()
        if isinstance(j, dict) and j.get("traceId"):
            return str(j["traceId"])
    except (ValueError, json.JSONDecodeError):
        pass
    return ""


def get_access_token(session: requests.Session) -> str:
    client_id, client_secret = _client_credentials()
    url = f"{BASE_URL}/external/v1/oauth2/token"
    last: requests.Response | None = None

    for _ in range(2):
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        sign = _generate_client_secret_sign(client_id, client_secret, timestamp_ms)
        resp = _request_with_retry(
            session,
            "POST",
            url,
            data={
                "client_id": client_id,
                "timestamp": str(timestamp_ms),
                "client_secret_sign": sign,
                "grant_type": "client_credentials",
                "type": OAUTH_TYPE,
            },
        )
        last = resp
        trace_id = _trace_from_response(resp)
        body_preview = (resp.text or "")[:500]
        logger.error(
            "Naver token response status=%s trace_id=%s body=%s",
            resp.status_code,
            trace_id or "(none)",
            body_preview,
        ) if not resp.ok else logger.info(
            "Naver token response status=%s trace_id=%s",
            resp.status_code,
            trace_id or "(none)",
        )

        if resp.ok:
            try:
                data = resp.json()
            except json.JSONDecodeError:
                raise NaverApiError(
                    "Invalid JSON from token endpoint",
                    status_code=502,
                    response_body=body_preview,
                    trace_id=trace_id,
                )
            token = data.get("access_token")
            if not token:
                raise NaverApiError(
                    "access_token missing in token response",
                    status_code=502,
                    response_body=body_preview,
                    trace_id=trace_id,
                )
            return str(token)

    assert last is not None
    trace_id = _trace_from_response(last)
    body_preview = (last.text or "")[:500]
    logger.error(
        "Naver token failed status=%s trace_id=%s body=%s",
        last.status_code,
        trace_id,
        body_preview,
    )
    raise NaverApiError(
        "Naver OAuth token request failed",
        status_code=502 if last.status_code >= 500 else last.status_code,
        response_body=body_preview,
        trace_id=trace_id,
    )


def _format_kst_param(dt: datetime) -> str:
    s = dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")
    return s[:-2] + ":" + s[-2:]


def get_last_changed_statuses_json(lookback_hours: int = 24) -> Any:
    hours = min(max(lookback_hours, 1), 24)
    now_kst = datetime.now(KST)
    from_dt = now_kst - timedelta(hours=hours)

    with requests.Session() as session:
        token = get_access_token(session)
        url = (
            f"{BASE_URL}/external/v1/pay-order/seller/"
            "product-orders/last-changed-statuses"
        )
        resp = _request_with_retry(
            session,
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "lastChangedType": "PAYED",
                "lastChangedFrom": _format_kst_param(from_dt),
                "lastChangedTo": _format_kst_param(now_kst),
                "limitCount": 300,
            },
        )

        trace_id = _trace_from_response(resp)
        body_preview = (resp.text or "")[:500]
        logger.info(
            "last-changed-statuses status=%s trace_id=%s body_snippet=%s",
            resp.status_code,
            trace_id or "(none)",
            body_preview[:200],
        )

        if not resp.ok:
            logger.error(
                "last-changed-statuses error status=%s trace_id=%s body=%s",
                resp.status_code,
                trace_id,
                body_preview,
            )
            raise NaverApiError(
                "last-changed-statuses request failed",
                status_code=502 if resp.status_code >= 500 else resp.status_code,
                response_body=body_preview,
                trace_id=trace_id,
            )

        try:
            return resp.json()
        except json.JSONDecodeError:
            raise NaverApiError(
                "Invalid JSON from last-changed-statuses",
                status_code=502,
                response_body=body_preview,
                trace_id=trace_id,
            )


def post_product_orders_query_json(product_order_ids: list[str]) -> Any:
    if not product_order_ids:
        return {"data": {"contents": []}}

    with requests.Session() as session:
        token = get_access_token(session)
        url = f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query"
        resp = _request_with_retry(
            session,
            "POST",
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"productOrderIds": product_order_ids},
        )

        trace_id = _trace_from_response(resp)
        body_preview = (resp.text or "")[:500]
        logger.info(
            "product-orders/query status=%s trace_id=%s ids=%s",
            resp.status_code,
            trace_id or "(none)",
            len(product_order_ids),
        )

        if not resp.ok:
            logger.error(
                "product-orders/query error status=%s trace_id=%s body=%s",
                resp.status_code,
                trace_id,
                body_preview,
            )
            raise NaverApiError(
                "product-orders/query request failed",
                status_code=502 if resp.status_code >= 500 else resp.status_code,
                response_body=body_preview,
                trace_id=trace_id,
            )

        try:
            return resp.json()
        except json.JSONDecodeError:
            raise NaverApiError(
                "Invalid JSON from product-orders/query",
                status_code=502,
                response_body=body_preview,
                trace_id=trace_id,
            )
