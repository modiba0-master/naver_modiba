import httpx
from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    """
    동기화 스케줄러는 `enable_worker` AND `RUN_SYNC_SCHEDULER_IN_API` 일 때만
    API 프로세스 안에서 1분 주기로 동작한다. 둘 중 하나라도 꺼지면 이 경로로는 네이버 폴링이 없다.
    """
    scheduler_would_run = bool(
        settings.enable_worker and settings.run_sync_scheduler_in_api
    )
    return {
        "status": "ok",
        "order_sync": {
            "scheduler_enabled_in_api_process": scheduler_would_run,
            "order_poll_interval_seconds": settings.order_poll_interval_seconds,
            "enable_worker": settings.enable_worker,
            "run_sync_scheduler_in_api": settings.run_sync_scheduler_in_api,
            "naver_order_sync_mode": settings.naver_order_sync_mode,
            "naver_commerce_order_lookback_hours": settings.naver_commerce_order_lookback_hours,
        },
    }


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
