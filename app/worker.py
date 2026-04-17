import asyncio
import logging

from app.config import settings
from app.database import SessionLocal
from app.services.daily_summary_service import generate_daily_summary
from app.services.sync import sync_orders

logger = logging.getLogger(__name__)


async def run_order_polling() -> None:
    while True:
        db = SessionLocal()
        try:
            inserted = sync_orders(db)
            summary = generate_daily_summary()
            logger.info(
                "Order polling completed, inserted=%s summary_upserted=%s",
                inserted,
                summary.get("upserted_rows"),
            )
        except Exception:
            logger.exception("Order polling failed")
        finally:
            db.close()
        await asyncio.sleep(settings.order_poll_interval_seconds)
