"""orders의 매출 집계일·순매출 재계산 (배치 단위).

- `payment_date`(한국시간 벽시계)에 16:00 영업일 규칙 → `business_date` / `*_business_date`.
- `net_revenue` = amount - refund - cancel.

실행 전 DB 백업 권장.

    python scripts/recompute_business_dates.py
    python scripts/recompute_business_dates.py --batch-size 5000
    python scripts/recompute_business_dates.py --verify-only

검증: 스크립트 종료 시 `verify`로 불일치 건수를 출력. MySQL용 예시 SQL은
`scripts/sql/verify_business_date_mariadb.sql` 참고.
"""

from __future__ import annotations

import argparse
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

DEFAULT_BATCH = 10_000


def _apply_row(o: Order) -> bool:
    """변경이 있으면 True."""
    if o.payment_date is None:
        return False
    pay_bd = calculate_business_date(o.payment_date)
    order_bd = calculate_business_date(o.ordered_at) if o.ordered_at else pay_bd
    ship_bd = calculate_business_date(o.shipped_at) if o.shipped_at else None
    nr = compute_net_revenue(o.amount, o.refund_amount, o.cancel_amount)
    changed = (
        o.business_date != pay_bd
        or o.payment_business_date != pay_bd
        or o.order_business_date != order_bd
        or o.shipping_business_date != ship_bd
        or o.net_revenue != nr
    )
    if changed:
        o.business_date = pay_bd
        o.payment_business_date = pay_bd
        o.order_business_date = order_bd
        o.shipping_business_date = ship_bd
        o.net_revenue = nr
    return changed


def run_recompute(batch_size: int) -> tuple[int, int]:
    """처리 행 수, 업데이트 행 수."""
    db = SessionLocal()
    processed = 0
    updated = 0
    try:
        last_id = 0
        while True:
            batch = db.scalars(
                select(Order)
                .where(Order.id > last_id)
                .order_by(Order.id)
                .limit(batch_size)
            ).all()
            if not batch:
                break
            batch_updated = 0
            for o in batch:
                processed += 1
                if _apply_row(o):
                    batch_updated += 1
            db.commit()
            updated += batch_updated
            last_id = batch[-1].id
        return processed, updated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_verify() -> int:
    """business_date가 calculate_business_date(payment_date)와 다른 행 수."""
    db = SessionLocal()
    try:
        mismatches = 0
        last_id = 0
        while True:
            batch = db.scalars(
                select(Order)
                .where(Order.id > last_id)
                .order_by(Order.id)
                .limit(DEFAULT_BATCH)
            ).all()
            if not batch:
                break
            for o in batch:
                if o.payment_date is None:
                    continue
                exp = calculate_business_date(o.payment_date)
                if o.business_date != exp:
                    mismatches += 1
            last_id = batch[-1].id
        return mismatches
    finally:
        db.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Recompute orders business_date (batched)")
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH,
        help=f"rows per batch (default {DEFAULT_BATCH})",
    )
    p.add_argument(
        "--verify-only",
        action="store_true",
        help="only count rows where business_date != expected from payment_date",
    )
    args = p.parse_args()

    if args.verify_only:
        n = run_verify()
        print(f"verify_business_date: mismatches={n}")
        return 0 if n == 0 else 1

    processed, updated = run_recompute(args.batch_size)
    print(f"recompute_business_dates: processed={processed} updated={updated}")
    n = run_verify()
    print(f"verify_business_date: mismatches={n}")
    return 0 if n == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
