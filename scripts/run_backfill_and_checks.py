from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_orders_from_excel import run_backfill


def normalize_database_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        raise ValueError("DATABASE_URL is empty")
    if url.startswith("mariadb://"):
        url = url.replace("mariadb://", "mysql+pymysql://", 1)
    elif url.startswith("mysql://") and "pymysql" not in url:
        url = url.replace("mysql://", "mysql+pymysql://", 1)
    return url


def print_query_result(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n[{title}]")
    if not rows:
        print("(no rows)")
        return
    for row in rows:
        print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe DB connect + backfill + check SQL")
    parser.add_argument("--order-manage", required=True)
    parser.add_argument("--delivery-status", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD HH:MM:SS")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(args.end, "%Y-%m-%d %H:%M:%S")

    raw_url = os.getenv("DATABASE_URL", "")
    db_url = normalize_database_url(raw_url)
    os.environ["DATABASE_URL"] = db_url

    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            ping = conn.execute(text("SELECT 1")).scalar()
            print(f"DB 연결 성공: {ping}")
    except Exception as exc:  # pragma: no cover
        print(f"DB 연결 실패: {exc}")
        raise

    result = run_backfill(
        order_manage_path=args.order_manage,
        delivery_status_path=args.delivery_status,
        start=start,
        end=end,
    )
    print("\n[Backfill Result]")
    print(result)

    checks: list[tuple[str, str]] = [
        (
            "A. range count",
            """
            SELECT COUNT(*) AS cnt
            FROM orders
            WHERE payment_date >= :start_dt
              AND payment_date <= :end_dt
            """,
        ),
        (
            "B. daily summary",
            """
            SELECT DATE(payment_date) AS pay_day,
                   COUNT(*) AS rows_cnt,
                   COUNT(DISTINCT content_order_no) AS order_cnt,
                   COALESCE(SUM(amount), 0) AS revenue
            FROM orders
            WHERE payment_date >= :start_dt
              AND payment_date <= :end_dt
            GROUP BY DATE(payment_date)
            ORDER BY pay_day
            """,
        ),
        (
            "C. status distribution",
            """
            SELECT order_status, COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS revenue
            FROM orders
            WHERE payment_date >= :start_dt
              AND payment_date <= :end_dt
            GROUP BY order_status
            ORDER BY cnt DESC
            """,
        ),
        (
            "D. sample rows",
            """
            SELECT order_id, content_order_no, payment_date, order_status,
                   placed_order_at, shipped_at, amount
            FROM orders
            WHERE payment_date >= :start_dt
              AND payment_date <= :end_dt
            ORDER BY payment_date, order_id
            LIMIT 50
            """,
        ),
        (
            "E. duplicate product-order id",
            """
            SELECT order_id, COUNT(*) AS dup_cnt
            FROM orders
            GROUP BY order_id
            HAVING COUNT(*) > 1
            ORDER BY dup_cnt DESC, order_id
            LIMIT 20
            """,
        ),
    ]

    params = {
        "start_dt": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_dt": end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with engine.connect() as conn:
        for title, sql in checks:
            rows = [dict(r) for r in conn.execute(text(sql), params).mappings().all()]
            print_query_result(title, rows)


if __name__ == "__main__":
    main()
