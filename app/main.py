import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import inspect

from app.config import settings
from app.database import Base, engine
from app.models import Order
from app.routers.analytics import router as analytics_router
from app.routers.health import router as health_router
from app.worker import run_order_polling

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)


def ensure_orders_table_schema() -> None:
    inspector = inspect(engine)
    if "orders" in inspector.get_table_names():
        logger.warning("Recreating orders table to enforce latest schema.")
        Order.__table__.drop(bind=engine, checkfirst=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_orders_table_schema()
    Base.metadata.create_all(bind=engine)
    worker_task = None
    if settings.enable_worker:
        worker_task = asyncio.create_task(run_order_polling())
        logger.info("Background polling worker started")
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            logger.info("Background polling worker stopped")


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
