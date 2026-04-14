import logging

import httpx

logger = logging.getLogger(__name__)


def notify_new_order(topic: str, order_id: str) -> None:
    if not topic:
        return

    message = f"New order collected: {order_id}"
    try:
        response = httpx.post(f"https://ntfy.sh/{topic}", content=message, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("ntfy notification failed for %s: %s", order_id, exc)
