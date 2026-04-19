import httpx
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    return {"status": "ok"}


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
