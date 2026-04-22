"""
Health & 운영 점검.

구조(한 DB):
  네이버 Commerce API → `sync_orders` → `orders` + (스케줄) `generate_daily_summary` → `daily_summary`
  FastAPI `SessionLocal` / `engine` → `settings.database_url` (MariaDB 등)

동기화:
  `enable_worker` AND `run_sync_scheduler_in_api` 일 때만 APScheduler가
  `order_poll_interval_seconds` 마다 `_scheduled_sync_orders_and_summary` 실행.
"""

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.engine.url import make_url

from app.config import settings
from app.database import SessionLocal, engine
from app.models import Order
from app.sync_state import get_scheduled_job_state

router = APIRouter(tags=["health"])


def _database_probe() -> tuple[bool, dict]:
    """(연결 성공 여부, 표시용 필드)."""
    out: dict = {
        "connected": False,
        "dialect": None,
        "host": None,
        "error": None,
        "orders_count": None,
        "latest_payment_date": None,
    }
    try:
        u = make_url(settings.database_url)
        out["dialect"] = u.drivername
        out["host"] = u.host
    except Exception:
        pass

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        out["connected"] = True
    except Exception as exc:
        out["error"] = str(exc)[:500]
        return False, out

    try:
        db = SessionLocal()
        try:
            out["orders_count"] = int(
                db.scalar(select(func.count()).select_from(Order)) or 0
            )
            last_pd = db.scalar(select(func.max(Order.payment_date)))
            if last_pd is not None:
                out["latest_payment_date"] = last_pd.isoformat(sep=" ")
        finally:
            db.close()
    except Exception as exc:
        out["connected"] = False
        out["error"] = str(exc)[:500]
        return False, out

    return True, out


@router.get("/health")
def health_check():
    """
    - `status`: DB 연결 실패 시 `degraded`, 그때 HTTP **503**.
    - `database`: `SELECT 1` + `orders` 건수·최신 `payment_date` (쌓임 여부 확인).
    - `scheduled_sync`: 마지막 성공 시각·삽입 건수·요약 upsert(프로세스 재시작 시 None).
    """
    ok, db_info = _database_probe()
    scheduler_would_run = bool(
        settings.enable_worker and settings.run_sync_scheduler_in_api
    )
    body: dict = {
        "status": "ok" if ok else "degraded",
        "architecture": {
            "flow": "Naver API → sync_orders → orders; generate_daily_summary → daily_summary",
            "database": "SQLAlchemy engine → DATABASE_URL (pool_pre_ping)",
            "scheduler": "APScheduler in API if enable_worker & run_sync_scheduler_in_api",
            "railway_web_process": "Procfile 'web' runs this FastAPI app (not Streamlit).",
        },
        "database": db_info,
        "order_sync": {
            "scheduler_enabled_in_api_process": scheduler_would_run,
            "order_poll_interval_seconds": settings.order_poll_interval_seconds,
            "enable_worker": settings.enable_worker,
            "run_sync_scheduler_in_api": settings.run_sync_scheduler_in_api,
            "naver_order_sync_mode": settings.naver_order_sync_mode,
            "naver_commerce_order_lookback_hours": settings.naver_commerce_order_lookback_hours,
        },
        "scheduled_job": get_scheduled_job_state(),
    }
    if not ok:
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/debug/outbound-ip")
def outbound_ip():
    """실제 배포 컨테이너의 아웃바운드 IP 확인용 (임시)."""
    results = {}
    for name, url in [
        ("ipify", "https://api.ipify.org"),
        ("icanhazip", "https://icanhazip.com"),
    ]:
        try:
            r = httpx.get(url, timeout=10)
            results[name] = r.text.strip()
        except Exception as e:
            results[name] = f"ERROR: {e}"
    return results
