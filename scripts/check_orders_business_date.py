#!/usr/bin/env python3
"""
배포 DB(DATABASE_URL)에서 특정 매출 귀속일(business_date) 주문이 있는지 집계한다.

사용 예 (PowerShell, Railway에서 복사한 URL 사용):

  $env:DATABASE_URL="mysql+pymysql://..."
  python scripts/check_orders_business_date.py --date 2026-04-21

SQLite 로컬:

  python scripts/check_orders_business_date.py --date 2026-04-21 --database-url sqlite:///./app.db

문제 해결:
  - `UnicodeError: label empty or too long` → 대개 URL 파싱 오류. 비밀번호에 `@`가 있으면 `%40`으로 바꾸고,
    PowerShell에서 URL을 한 줄·따옴표로만 설정했는지 확인하세요.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 레포 루트에서 `app` import 가능하도록
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url


def _prepare_database_url(raw: str) -> str:
    """공백·따옴표 제거 후 URL 파싱; MySQL 계열은 비어 있는 호스트를 조기에 걸러낸다."""
    s = raw.strip().strip('"').strip("'")
    if not s:
        raise ValueError("DATABASE_URL이 비어 있습니다.")
    try:
        u = make_url(s)
    except Exception as exc:
        raise ValueError(f"DATABASE_URL을 해석할 수 없습니다: {exc}") from exc

    driver = (u.drivername or "").lower()
    if "mysql" in driver:
        if not (u.host and str(u.host).strip()):
            raise ValueError(
                "MySQL/MariaDB URL에 호스트가 비어 있습니다. "
                "비밀번호에 `@`가 들어가면 URL에서는 `%40`으로 인코딩해야 합니다. "
                "예: `mysql+pymysql://user:p%40ssword@hostname:3306/db`"
            )
    return s


def main() -> None:
    parser = argparse.ArgumentParser(
        description="orders 테이블에서 business_date 기준 건수·금액 합계 확인",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="매출 귀속일 (YYYY-MM-DD), 예: 2026-04-21",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="기본값: 환경변수 DATABASE_URL",
    )
    args = parser.parse_args()
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL이 없습니다. --database-url 또는 환경변수를 설정하세요.", file=sys.stderr)
        sys.exit(1)

    try:
        url = _prepare_database_url(url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    try:
        engine = create_engine(url, pool_pre_ping=True)
    except Exception as exc:
        print(f"엔진 생성 실패: {exc}", file=sys.stderr)
        sys.exit(1)
    sql = text(
        """
        SELECT
            COUNT(*) AS row_count,
            COUNT(DISTINCT order_id) AS distinct_product_orders,
            COALESCE(SUM(amount), 0) AS sum_amount
        FROM orders
        WHERE business_date = :bd
        """
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(sql, {"bd": args.date}).mappings().one()
    except UnicodeError as exc:
        print(
            "DB 연결 실패: 호스트 이름(IDNA/DNS) 오류입니다.\n"
            "  - DATABASE_URL이 잘렸거나, 비밀번호의 `@` 미인코딩으로 호스트가 비정상 파싱된 경우가 많습니다.\n"
            "  - PowerShell: `$env:DATABASE_URL='한 줄짜리 URL'` 처럼 따옴표로 감싸고 줄바꿈이 없는지 확인하세요.\n"
            f"  상세: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    except OSError as exc:
        print(
            "DB 연결 실패(네트워크/호스트). URL의 호스트·포트·방화벽을 확인하세요.\n"
            f"  상세: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"business_date = {args.date}")
    print(f"  rows (lines):              {row['row_count']}")
    print(f"  distinct order_id:       {row['distinct_product_orders']}")
    print(f"  sum(amount):             {row['sum_amount']}")


if __name__ == "__main__":
    main()
