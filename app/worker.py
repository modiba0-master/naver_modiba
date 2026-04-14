import asyncio
import logging

from app.config import settings
from app.database import SessionLocal
from app.services.sync import sync_orders

logger = logging.getLogger(__name__)


async def run_order_polling() -> None:
    while True:
        db = SessionLocal()
        try:
            inserted = sync_orders(db)
            logger.info("Order polling completed, inserted=%s", inserted)
        except Exception:
            logger.exception("Order polling failed")
        finally:
            db.close()
        await asyncio.sleep(settings.order_poll_interval_seconds)
