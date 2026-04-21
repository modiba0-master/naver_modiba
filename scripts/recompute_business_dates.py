"""orders의 영업일·순매출 재계산.

- `payment_date`(KST naive)로 `business_date` / `payment_business_date` / `order_business_date` / `shipping_business_date`를 맞춘다.
- `net_revenue` = amount - refund - cancel (상한 적용).

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
from app.services.revenue_compute import compute_net_revenue
from app.services.sync import calculate_business_date


def main() -> int:
    db = SessionLocal()
    try:
        rows = db.scalars(select(Order)).all()
        updated = 0
        for o in rows:
            if o.payment_date is None:
                continue
            pay_bd = calculate_business_date(o.payment_date)
            order_bd = calculate_business_date(o.ordered_at) if o.ordered_at else pay_bd
            ship_bd = calculate_business_date(o.shipped_at) if o.shipped_at else None
            nr = compute_net_revenue(o.amount, o.refund_amount, o.cancel_amount)
            if (
                o.business_date != pay_bd
                or o.payment_business_date != pay_bd
                or o.order_business_date != order_bd
                or o.shipping_business_date != ship_bd
                or o.net_revenue != nr
            ):
                o.business_date = pay_bd
                o.payment_business_date = pay_bd
                o.order_business_date = order_bd
                o.shipping_business_date = ship_bd
                o.net_revenue = nr
                updated += 1
        db.commit()
        print(f"recompute_business_dates: rows={len(rows)} updated={updated}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
