"""``payment_date`` 원본 시각(KST naive)만 받아 16:00 영업일 → ``business_date`` 산출(저장은 sync/naver_orders_sync)."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def calculate_business_date(dt: datetime) -> date:
    """
    영업일 기준 매출일 계산
    기준: 당일 16:00 ~ 다음날 15:59
    (KST 기준, 추가 timezone 변환 없음 — 호출 전에 KST naive 벽시계로 맞출 것)
    """
    if dt.hour >= 16:
        return (dt + timedelta(days=1)).date()
    return dt.date()
