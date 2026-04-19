from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from sqlalchemy import MetaData, Table, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import DailySummary

logger = logging.getLogger(__name__)

_CANCELLED_STATUSES = {"CANCELLED", "취소", "주문취소", "CANCEL"}


@contextmanager
def _session_scope() -> Iterator[Session]:
    """
    Clean session boundary for summary generation.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _load_tables() -> tuple[Table, Table]:
    metadata = MetaData()
    orders = Table("orders", metadata, autoload_with=engine)
    try:
        daily_summary = Table("daily_summary", metadata, autoload_with=engine)
    except NoSuchTableError:
        logger.warning("daily_summary table not found. creating table and retrying")
        DailySummary.__table__.create(bind=engine, checkfirst=True)
        metadata = MetaData()
        orders = Table("orders", metadata, autoload_with=engine)
        daily_summary = Table("daily_summary", metadata, autoload_with=engine)
    return orders, daily_summary


def _status_is_cancelled(value: Any) -> bool:
    raw = str(value or "").strip()
    upper = raw.upper()
    return upper in _CANCELLED_STATUSES or raw in _CANCELLED_STATUSES


def _pick_existing_column(orders: Table, *candidates: str) -> str:
    for name in candidates:
        if name in orders.c:
            return name
    raise RuntimeError(f"Required column not found in orders table: {candidates}")


def _build_aggregates(
    rows: list[dict[str, Any]],
    *,
    product_key: str,
    option_key: str,
    status_key: str,
    ordered_at_key: str,
    amount_key: str,
) -> dict[tuple[date, str, str], dict[str, int]]:
    aggregates: dict[tuple[date, str, str], dict[str, int]] = {}

    for row in rows:
        ordered_at = row.get(ordered_at_key)
        if ordered_at is None:
            # Skip malformed rows instead of failing whole batch.
            continue

        day = ordered_at.date()
        product_id = str(row.get(product_key) or "")
        option_id = str(row.get(option_key) or "")
        line_amount = int(row.get(amount_key) or 0)
        cancelled = _status_is_cancelled(row.get(status_key))

        key = (day, product_id, option_id)
        if key not in aggregates:
            aggregates[key] = {
                "orders": 0,
                "revenue": 0,
                "cancel_count": 0,
                "refund_amount": 0,
                "profit": 0,
            }

        bucket = aggregates[key]
        bucket["orders"] += 1
        bucket["revenue"] += line_amount
        if cancelled:
            bucket["cancel_count"] += 1
            bucket["refund_amount"] += line_amount

    for bucket in aggregates.values():
        bucket["profit"] = bucket["revenue"] - bucket["refund_amount"]

    return aggregates


def _upsert_daily_summary(
    session: Session,
    daily_summary: Table,
    aggregates: dict[tuple[date, str, str], dict[str, int]],
    *,
    upsert_chunk_size: int,
) -> int:
    if not aggregates:
        return 0

    rows: list[dict[str, Any]] = [
        {
            "date": key[0],
            "product_id": key[1],
            "option_id": key[2],
            "orders": value["orders"],
            "revenue": value["revenue"],
            "cancel_count": value["cancel_count"],
            "refund_amount": value["refund_amount"],
            "profit": value["profit"],
        }
        for key, value in aggregates.items()
    ]

    affected = 0
    for i in range(0, len(rows), upsert_chunk_size):
        chunk = rows[i : i + upsert_chunk_size]
        stmt = sqlite_insert(daily_summary).values(chunk)
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["date", "product_id", "option_id"],
            set_={
                "orders": stmt.excluded.orders,
                "revenue": stmt.excluded.revenue,
                "cancel_count": stmt.excluded.cancel_count,
                "refund_amount": stmt.excluded.refund_amount,
                "profit": stmt.excluded.profit,
            },
        )
        result = session.execute(upsert_stmt)
        affected += int(result.rowcount or 0)
    return affected


def generate_daily_summary(batch_size: int = 5000, upsert_chunk_size: int = 2000) -> dict[str, int]:
    """
    Read orders from MariaDB and upsert grouped daily summary.

    Group key:
      - date(ordered_at)
      - product_id
      - option_id

    Metrics:
      - orders
      - revenue
      - cancel_count
      - refund_amount
      - profit
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    if upsert_chunk_size <= 0:
        raise ValueError("upsert_chunk_size must be greater than 0")

    orders, daily_summary = _load_tables()
    total_scanned = 0
    total_upserted = 0
    batch_no = 0
    last_seen_id = 0

    logger.info(
        "daily-summary start batch_size=%s upsert_chunk_size=%s",
        batch_size,
        upsert_chunk_size,
    )

    with _session_scope() as session:
        product_key = _pick_existing_column(orders, "product_id", "product_name")
        option_key = _pick_existing_column(orders, "option_id", "option_name")
        status_key = _pick_existing_column(orders, "status", "order_status")
        ordered_at_key = _pick_existing_column(orders, "ordered_at", "payment_date")
        amount_key = _pick_existing_column(orders, "amount", "price")

        while True:
            batch_no += 1
            stmt = (
                select(
                    orders.c.id,
                    orders.c[product_key],
                    orders.c[option_key],
                    orders.c[status_key],
                    orders.c[ordered_at_key],
                    orders.c[amount_key],
                )
                .where(orders.c.id > last_seen_id)
                .order_by(orders.c.id.asc())
                .limit(batch_size)
            )
            batch_rows = session.execute(stmt).mappings().all()
            if not batch_rows:
                break

            # Convert RowMapping to plain dict for stable processing.
            payload = [dict(r) for r in batch_rows]
            scanned = len(payload)
            total_scanned += scanned
            last_seen_id = int(payload[-1]["id"])

            aggregates = _build_aggregates(
                payload,
                product_key=product_key,
                option_key=option_key,
                status_key=status_key,
                ordered_at_key=ordered_at_key,
                amount_key=amount_key,
            )
            upserted = _upsert_daily_summary(
                session,
                daily_summary,
                aggregates,
                upsert_chunk_size=upsert_chunk_size,
            )
            total_upserted += upserted

            logger.info(
                "daily-summary batch=%s scanned=%s groups=%s upserted=%s last_id=%s",
                batch_no,
                scanned,
                len(aggregates),
                upserted,
                last_seen_id,
            )

    logger.info(
        "daily-summary done scanned=%s upserted=%s batches=%s",
        total_scanned,
        total_upserted,
        batch_no - 1,
    )
    return {
        "scanned_orders": total_scanned,
        "upserted_rows": total_upserted,
        "batches": max(batch_no - 1, 0),
    }

