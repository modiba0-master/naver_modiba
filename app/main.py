import logging
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers.analytics import router as analytics_router
from app.routers.health import router as health_router
from app.services.daily_summary_service import generate_daily_summary
from app.services.sync import sync_orders

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)
_sync_job_lock = threading.Lock()


def _scheduled_sync_orders_and_summary() -> None:
    if not _sync_job_lock.acquire(blocking=False):
        logger.warning("Scheduled sync skipped: previous job still running")
        return

    try:
        db = SessionLocal()
        try:
            inserted_count = sync_orders(db)
        finally:
            db.close()

        summary_result = generate_daily_summary()
        logger.info(
            "Scheduled sync done inserted_count=%s summary_upserted=%s summary_batches=%s",
            inserted_count,
            summary_result.get("upserted_rows"),
            summary_result.get("batches"),
        )
    except Exception:
        logger.exception("Scheduled sync/summary failed with exception")
    finally:
        _sync_job_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Data preservation: never drop runtime tables on startup.
    Base.metadata.create_all(bind=engine)

    scheduler = None
    if settings.enable_worker and settings.run_sync_scheduler_in_api:
        scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
            }
        )
        scheduler.add_job(
            _scheduled_sync_orders_and_summary,
            "interval",
            seconds=settings.order_poll_interval_seconds,
            id="sync-orders-and-summary",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(
            "APScheduler started: sync->summary every %s seconds",
            settings.order_poll_interval_seconds,
        )
    else:
        logger.info(
            "APScheduler disabled in API process (enable_worker=%s, run_sync_scheduler_in_api=%s)",
            settings.enable_worker,
            settings.run_sync_scheduler_in_api,
        )
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped")


app = FastAPI(
    title="Naver Commerce Order Analytics System",
    description="Real-time order collection, analytics, and customer tagging API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {"message": "naver modiba server running"}


app.include_router(health_router)
app.include_router(analytics_router)
