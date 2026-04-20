"""Backfill missing orders from SmartStore excel exports.

Usage (PowerShell):
  $env:DATABASE_URL="mysql+pymysql://..."
  python scripts/backfill_orders_from_excel.py `
    --order-manage "C:/Users/USER/Downloads/스마트스토어_선택주문발주발송관리_20260421_0231.xlsx" `
    --delivery-status "C:/Users/USER/Downloads/스마트스토어_선택주문배송현황_20260421_0231.xlsx" `
    --start "2026-04-17 16:00:00" `
    --end "2026-04-20 09:00:00"
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, ensure_orders_schema, engine
from app.models import Order
from app.services.sync import calculate_business_date, is_valid_order_status, normalize_order_status


@dataclass
class RowData:
    order_id: str
    content_order_no: str | None
    product_name: str
    option_name: str
    quantity: int
    amount: int
    buyer_name: str
    buyer_id: str
    receiver_name: str
    address: str
    order_status: str
    payment_date: datetime
    order_date: datetime
    placed_order_at: datetime | None
    shipped_at: datetime | None


def _to_dt(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _to_str(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _to_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_col(row: pd.Series, idx: int) -> Any:
    if idx < 0 or idx >= len(row):
        return None
    return row.iloc[idx]


def _extract_row_order_manage(row: pd.Series) -> RowData | None:
    # 선택주문발주발송관리 export column positions.
    order_id = _to_str(_safe_col(row, 0))  # 상품주문번호
    if not order_id:
        return None
    content_order_no = _to_str(_safe_col(row, 1)) or None  # 주문번호

    payment_date = _to_dt(_safe_col(row, 13)) or _to_dt(_safe_col(row, 48))
    if payment_date is None:
        return None
    order_date = _to_dt(_safe_col(row, 48)) or payment_date

    status = normalize_order_status(_to_str(_safe_col(row, 10)))
    if not is_valid_order_status(status):
        status = "신규주문"

    return RowData(
        order_id=order_id,
        content_order_no=content_order_no,
        product_name=_to_str(_safe_col(row, 15)),
        option_name=_to_str(_safe_col(row, 17)),
        quantity=max(1, _to_int(_safe_col(row, 19))),
        amount=max(0, _to_int(_safe_col(row, 24))),
        buyer_name=_to_str(_safe_col(row, 7)),
        buyer_id=_to_str(_safe_col(row, 8)),
        receiver_name=_to_str(_safe_col(row, 9)),
        address=_to_str(_safe_col(row, 39)),
        order_status=status,
        payment_date=payment_date,
        order_date=order_date,
        placed_order_at=_to_dt(_safe_col(row, 29)),
        shipped_at=_to_dt(_safe_col(row, 30)),
    )


def _extract_row_delivery_status(row: pd.Series) -> RowData | None:
    # 선택주문배송현황 export column positions.
    order_id = _to_str(_safe_col(row, 0))  # 상품주문번호
    if not order_id:
        return None
    content_order_no = _to_str(_safe_col(row, 1)) or None  # 주문번호

    # 배송현황 파일에서 주문일(결제일 대용) 컬럼
    payment_date = _to_dt(_safe_col(row, 11))
    if payment_date is None:
        return None
    order_date = payment_date

    status = normalize_order_status(_to_str(_safe_col(row, 10)))
    if not is_valid_order_status(status):
        status = "신규주문"

    return RowData(
        order_id=order_id,
        content_order_no=content_order_no,
        product_name=_to_str(_safe_col(row, 14)),
        option_name=_to_str(_safe_col(row, 15)),
        quantity=max(1, _to_int(_safe_col(row, 16))),
        amount=max(0, _to_int(_safe_col(row, 21))),
        buyer_name=_to_str(_safe_col(row, 7)),
        buyer_id=_to_str(_safe_col(row, 8)),
        receiver_name=_to_str(_safe_col(row, 9)),
        address=_to_str(_safe_col(row, 42)),
        order_status=status,
        payment_date=payment_date,
        order_date=order_date,
        placed_order_at=None,
        shipped_at=_to_dt(_safe_col(row, 5)),
    )


def _load_rows(path: str) -> list[RowData]:
    df = pd.read_excel(path)
    out: list[RowData] = []
    lower_path = path.lower()
    is_delivery_status = ("배송현황" in path) or ("delivery" in lower_path)
    for _, raw in df.iterrows():
        row = (
            _extract_row_delivery_status(raw)
            if is_delivery_status
            else _extract_row_order_manage(raw)
        )
        if row is not None:
            out.append(row)
    return out


def _merge_rows(rows: list[RowData]) -> list[RowData]:
    merged: dict[str, RowData] = {}
    for row in rows:
        prev = merged.get(row.order_id)
        if prev is None:
            merged[row.order_id] = row
            continue
        # Prefer the row with richer timeline/status info.
        if (row.shipped_at or row.placed_order_at) and not (prev.shipped_at or prev.placed_order_at):
            merged[row.order_id] = row
            continue
        if row.payment_date > prev.payment_date:
            merged[row.order_id] = row
    return list(merged.values())


def run_backfill(order_manage_path: str, delivery_status_path: str, start: datetime, end: datetime) -> dict[str, int]:
    ensure_orders_schema(engine)
    rows = _load_rows(order_manage_path) + _load_rows(delivery_status_path)
    merged = _merge_rows(rows)
    target = [r for r in merged if start <= r.payment_date <= end]

    inserted = 0
    updated = 0
    skipped = 0
    db = SessionLocal()
    try:
        for row in target:
            existing = db.scalar(select(Order).where(Order.order_id == row.order_id))
            if existing is None:
                db.add(
                    Order(
                        order_id=row.order_id,
                        content_order_no=row.content_order_no,
                        product_name=row.product_name,
                        option_name=row.option_name,
                        quantity=row.quantity,
                        amount=row.amount,
                        buyer_name=row.buyer_name,
                        buyer_id=row.buyer_id,
                        receiver_name=row.receiver_name,
                        address=row.address,
                        order_status=row.order_status,
                        payment_date=row.payment_date,
                        order_date=row.order_date.date(),
                        business_date=calculate_business_date(row.payment_date),
                        ordered_at=row.order_date,
                        placed_order_at=row.placed_order_at,
                        shipped_at=row.shipped_at,
                    )
                )
                inserted += 1
                continue

            changed = False
            if row.content_order_no and row.content_order_no != existing.content_order_no:
                existing.content_order_no = row.content_order_no
                changed = True
            if row.placed_order_at and existing.placed_order_at is None:
                existing.placed_order_at = row.placed_order_at
                changed = True
            if row.shipped_at and existing.shipped_at is None:
                existing.shipped_at = row.shipped_at
                changed = True
            if changed:
                updated += 1
            else:
                skipped += 1

        db.commit()
    finally:
        db.close()

    return {
        "source_rows": len(rows),
        "dedup_rows": len(merged),
        "target_rows": len(target),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill orders from SmartStore excel files.")
    parser.add_argument("--order-manage", required=True, help="선택주문발주발송관리 엑셀 경로")
    parser.add_argument("--delivery-status", required=True, help="선택주문배송현황 엑셀 경로")
    parser.add_argument("--start", required=True, help="시작 시각, e.g. 2026-04-17 16:00:00")
    parser.add_argument("--end", required=True, help="종료 시각, e.g. 2026-04-20 09:00:00")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(args.end, "%Y-%m-%d %H:%M:%S")

    result = run_backfill(args.order_manage, args.delivery_status, start, end)
    print(result)


if __name__ == "__main__":
    main()
