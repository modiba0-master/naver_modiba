"""orders.business_date 재계산.

- DB `payment_date`는 KST naive 결제 시각을 전제로,
  `calculate_business_date`(16:00 KST 영업일 컷)로 `business_date`만 다시 맞춘다.
- 동일 결제 시각이 여러 행에 보이는 것은 **손상이 아니라**, 같은 주문번호(장바구니)에 상품줄이
  여러 개일 때 결제 시각이 복제되는 정상 동작이다.

사용 (프로젝트 루트에서, DATABASE_URL·.env가 맞을 때):

  python scripts/recompute_business_dates.py

"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Order
from app.services.sync import calculate_business_date


def main() -> int:
    db = SessionLocal()
    try:
        rows = db.scalars(select(Order)).all()
        updated = 0
        for o in rows:
            if o.payment_date is None:
                continue
            new_bd = calculate_business_date(o.payment_date)
            if o.business_date != new_bd:
                o.business_date = new_bd
                updated += 1
        db.commit()
        print(f"recompute_business_dates: rows={len(rows)} updated={updated}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
