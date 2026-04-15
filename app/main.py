import logging
from contextlib import asynccontextmanager

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.config import settings
from app.database import Base, engine
from app.routers.analytics import router as analytics_router
from app.routers.health import router as health_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)
_sync_job_lock = False


async def _scheduled_sync_orders(app: FastAPI) -> None:
    global _sync_job_lock

    if _sync_job_lock:
        logger.warning("Scheduled /analytics/sync-orders skipped: previous job still running")
        return

    _sync_job_lock = True
    try:
        transport = httpx.ASGITransport(app=app)
        headers = {}
        if settings.sync_api_key:
            headers["x-sync-key"] = settings.sync_api_key
        async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
            response = await client.post("/analytics/sync-orders", headers=headers)

        if response.is_success:
            payload = response.json()
            logger.info(
                "Scheduled /analytics/sync-orders success inserted_count=%s",
                payload.get("inserted_count"),
            )
        else:
            logger.error(
                "Scheduled /analytics/sync-orders failed status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
    except Exception:
        logger.exception("Scheduled /analytics/sync-orders failed with exception")
    finally:
        _sync_job_lock = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Data preservation: never drop runtime tables on startup.
    Base.metadata.create_all(bind=engine)

    scheduler = None
    if settings.enable_worker:
        scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
            }
        )
        scheduler.add_job(
            _scheduled_sync_orders,
            "interval",
            minutes=10,
            args=[app],
            id="sync-orders-every-10-minutes",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("APScheduler started: /analytics/sync-orders every 10 minutes")
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
