#!/usr/bin/env python3
"""
orders 테이블의 주문일시·결제·발주·발송 시각 일관성 점검.

- 동기화는 `app/services/sync.py`에서 API ISO 문자열을 파싱한 뒤, 결제일시와 동일하게
  **KST 벽시계 naive**로 저장하는 것이 정상(최신 코드 기준).
- 같은 결제 시각이 여러 행: **같은 주문번호(장바구니)에 상품줄이 여러 개**이면 정상.

사용:

  python scripts/check_order_timeline_health.py
  python scripts/check_order_timeline_health.py --database-url sqlite:///./app.db

환경변수 DATABASE_URL이 없으면 --database-url 필수.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Order


def _session(database_url: str | None) -> Session:
    url = (database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("DATABASE_URL 또는 --database-url 이 필요합니다.", file=sys.stderr)
        sys.exit(1)
    engine = create_engine(url, pool_pre_ping=True)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="주문 타임라인(주문일시·결제·발주·발송) 건강 점검",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="기본: 환경변수 DATABASE_URL",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="0이면 전체 행, 양수면 최근 id 기준 상위 N건만(대용량 DB용)",
    )
    args = parser.parse_args()

    db = _session(args.database_url)
    try:
        stmt = select(Order).order_by(Order.id.desc())
        if args.limit and args.limit > 0:
            stmt = stmt.limit(args.limit)
        rows = list(db.scalars(stmt).all())
    finally:
        db.close()

    n = len(rows)
    if n == 0:
        print("orders 행이 없습니다.")
        return 0

    # --- 이상 집계 (naive KST 벽시계끼리 비교) ---
    ordered_way_after_pay = 0  # 주문일시가 결제보다 이틀 이상 늦음
    pay_before_order_suspicious = 0  # 결제가 주문보다 1시간 이상 앞섬
    place_after_ship = 0  # 발주가 발송보다 늦음
    order_date_not_match = 0  # order_date != ordered_at 날짜
    missing_ordered = 0
    missing_payment = 0

    for o in rows:
        if o.payment_date is None:
            missing_payment += 1
            continue
        if o.ordered_at is None:
            missing_ordered += 1
        else:
            if o.order_date != o.ordered_at.date():
                order_date_not_match += 1
            if o.ordered_at > o.payment_date + timedelta(hours=48):
                ordered_way_after_pay += 1
            if o.payment_date + timedelta(hours=1) < o.ordered_at:
                pay_before_order_suspicious += 1
        if (
            o.placed_order_at is not None
            and o.shipped_at is not None
            and o.placed_order_at > o.shipped_at
        ):
            place_after_ship += 1

    # --- 동일 결제 시각(초 단위) 다건: 주문번호별로 묶어 설명 ---
    by_pay_sec: dict[str, list[str]] = defaultdict(list)
    for o in rows:
        if o.payment_date is None:
            continue
        key = o.payment_date.strftime("%Y-%m-%d %H:%M:%S")
        by_pay_sec[key].append(o.order_id)

    top_dup = sorted(
        ((k, len(v), len(set(v))) for k, v in by_pay_sec.items() if len(v) >= 2),
        key=lambda x: -x[1],
    )[:15]

    # 연속 id 구간에서 동일 payment_date 스트릭(동기화 버그 힌트용, 참고만)
    rows_by_id = sorted(rows, key=lambda r: r.id)
    max_streak_same_pay = 0
    streak = 0
    prev_pay = None
    for o in rows_by_id:
        p = o.payment_date
        if p is None:
            streak = 0
            prev_pay = None
            continue
        if prev_pay is not None and p == prev_pay:
            streak += 1
        else:
            streak = 1
        max_streak_same_pay = max(max_streak_same_pay, streak)
        prev_pay = p

    print(f"검사 행 수: {n}" + (f" (limit={args.limit})" if args.limit else ""))
    print()
    print("[타임라인 논리 이상 건수]")
    print(f"  결제일시 없음:              {missing_payment}")
    print(f"  주문일시(ordered_at) 없음:  {missing_ordered}")
    print(f"  order_date ≠ ordered_at 날짜: {order_date_not_match}")
    print(f"  주문일시 > 결제 + 48시간:   {ordered_way_after_pay}")
    print(f"  결제 +1h < 주문일시:        {pay_before_order_suspicious}  (대량이면 API/저장 확인)")
    print(f"  발주일시 > 발송일시:        {place_after_ship}")
    print()
    print("[동일 결제시각(초) 다건 TOP — 상위 몇 건]")
    if not top_dup:
        print("  (동일 초에 2건 이상인 그룹 없음)")
    else:
        for key, cnt, distinct_oid in top_dup:
            print(f"  {key}  → {cnt}행 (서로 다른 상품주문번호 {distinct_oid}개)")
    print()
    print(f"[참고] id 순서 기준 동일 payment_date 최대 연속 행 수: {max_streak_same_pay}")
    print("       (같은 주문 여러 줄이면 연속으로 같을 수 있어 단독으로는 이상 아님)")
    print()
    print("정상 기대: 주문일시 ≤ 결제에 가깝고, 발주 ≤ 발송. 동일 결제초 다건은 같은 주문번호면 흔함.")

    # 이상이 많으면 비정상 코드로 종료은 하지 않음(리포트만)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
