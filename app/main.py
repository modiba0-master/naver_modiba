import logging
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.database import Base, SessionLocal, engine, ensure_orders_schema
from app.routers.analytics import router as analytics_router
from app.routers.health import router as health_router
from app.services.daily_summary_service import generate_daily_summary
from app.services.sync import sync_orders
from app.sync_state import record_scheduled_job_error, record_scheduled_job_ok

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

        summary_result: dict = {}
        try:
            summary_result = generate_daily_summary()
        except Exception:
            logger.exception("generate_daily_summary failed after sync_orders")
        logger.info(
            "Scheduled sync done inserted_count=%s summary_upserted=%s summary_batches=%s",
            inserted_count,
            summary_result.get("upserted_rows"),
            summary_result.get("batches"),
        )
        record_scheduled_job_ok(
            inserted_count=inserted_count,
            summary_upserted=int(summary_result.get("upserted_rows") or 0),
        )
    except Exception as exc:
        record_scheduled_job_error(str(exc))
        logger.exception("Scheduled sync/summary failed with exception")
    finally:
        _sync_job_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Data preservation: never drop runtime tables on startup.
    try:
        Base.metadata.create_all(bind=engine)
        ensure_orders_schema(engine)
    except OperationalError as exc:
        # pymysql: (1045, "Access denied for user 'x'@'host' (using password: YES/NO)")
        errno = getattr(exc.orig, "args", (None,))[0] if exc.orig is not None else None
        if errno == 1045:
            logger.critical(
                "DB login failed (MySQL 1045 Access denied). "
                "DATABASE_URL user/password does not match the MariaDB service. "
                "On Railway: open the MariaDB plugin → use its Variables / Connect string for "
                "DATABASE_URL (or set DATABASE_PUBLIC_URL and DATABASE_URL_USE_PUBLIC=1 with the public URL from the same DB). "
                "If the database was reset or the user was recreated, copy a fresh connection string; old env values stay invalid."
            )
        raise

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
    return {
        "message": "naver modiba server running",
        "service": "FastAPI (uvicorn). Order sync + /analytics + /health.",
        "railway": "The 'web' process in Procfile is this API; it is not the Streamlit dashboard.",
    }


app.include_router(health_router)
app.include_router(analytics_router)
