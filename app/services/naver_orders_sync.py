"""네이버 주문 → `orders` 저장 시 영업일 파생.

- ``payment_date``: 원본 결제 시각(``sync.parse_payment_datetime_string`` 등).
- ``business_date`` / ``payment_business_date``: 그 시각에 16:00 영업일 규칙 적용(저장됨).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.order_transformer import calculate_business_date as _sales_business_date

KST = ZoneInfo("Asia/Seoul")


def to_kst_naive(dt: datetime) -> datetime:
    """원본 시각을 KST 벽시계 naive로 통일(영업일 16시 컷은 ``calculate_business_date``에서만)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(KST).replace(tzinfo=None)


def calculate_business_date(payment_date: datetime) -> date:
    """원본 ``payment_date``(또는 동일 의미 이벤트 시각) → 16:00 기준 매출 귀속일(저장용).

    ``row["business_date"] = calculate_business_date(row["payment_date"])`` 와 동일.
    """
    return _sales_business_date(to_kst_naive(payment_date))
