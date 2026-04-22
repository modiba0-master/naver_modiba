"""API 프로세스 내 주기 동기화(`sync_orders` + summary) 마지막 성공/실패 시각 — `/health` 노출용."""

from __future__ import annotations

from datetime import datetime, timezone

_last_successful_job_at: datetime | None = None
_last_sync_inserted: int | None = None
_last_summary_upserted: int | None = None
_last_job_error: str | None = None


def record_scheduled_job_ok(
    *, inserted_count: int, summary_upserted: int | None
) -> None:
    global _last_successful_job_at, _last_sync_inserted, _last_summary_upserted, _last_job_error
    _last_successful_job_at = datetime.now(timezone.utc)
    _last_sync_inserted = inserted_count
    _last_summary_upserted = summary_upserted
    _last_job_error = None


def record_scheduled_job_error(message: str) -> None:
    global _last_job_error
    _last_job_error = (message or "")[:2000]


def get_scheduled_job_state() -> dict[str, object]:
    return {
        "last_success_utc": _last_successful_job_at.isoformat()
        if _last_successful_job_at
        else None,
        "last_inserted_count": _last_sync_inserted,
        "last_summary_upserted_rows": _last_summary_upserted,
        "last_error": _last_job_error,
    }
