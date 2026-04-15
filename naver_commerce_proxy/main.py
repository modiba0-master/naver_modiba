import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.naver import (
    NaverApiError,
    get_last_changed_statuses_json,
    post_product_orders_query_json,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Naver Commerce API Proxy")


class ProductOrdersQueryBody(BaseModel):
    productOrderIds: list[str] = Field(..., min_length=1)


def _naver_error_json_response(e: NaverApiError) -> JSONResponse:
    logger.error(
        "Naver proxy error: %s status=%s trace_id=%s body=%s",
        e,
        e.status_code,
        e.trace_id,
        e.response_body[:500] if e.response_body else "",
    )
    payload: dict[str, object] = {
        "error": str(e),
        "status_code": e.status_code,
        "response_body": e.response_body,
    }
    if e.trace_id:
        payload["traceId"] = e.trace_id
    return JSONResponse(status_code=e.status_code, content=payload)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/naver/orders")
def naver_orders(hours: int = 24):
    try:
        return get_last_changed_statuses_json(lookback_hours=hours)
    except NaverApiError as e:
        return _naver_error_json_response(e)


@app.post("/naver/product-orders/query")
def naver_product_orders_query(body: ProductOrdersQueryBody):
    try:
        return post_product_orders_query_json(body.productOrderIds)
    except NaverApiError as e:
        return _naver_error_json_response(e)
