from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.sql import func

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url, pool_pre_ping=True, connect_args=connect_args
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _backfill_orders_revenue_columns() -> None:
    """신규 컬럼 추가 후 영업일·순매출을 채운다 (기존 행 호환)."""
    from app.models import Order
    from app.services.revenue_compute import compute_net_revenue
    from app.services.sync import calculate_business_date

    db = SessionLocal()
    try:
        rows = db.scalars(select(Order)).all()
        if not rows:
            return
        changed = False
        for o in rows:
            pay_bd = o.payment_business_date or o.business_date
            if o.payment_business_date != pay_bd:
                o.payment_business_date = pay_bd
                changed = True
            if o.ordered_at:
                obd = calculate_business_date(o.ordered_at)
            else:
                obd = pay_bd
            if o.order_business_date != obd:
                o.order_business_date = obd
                changed = True
            if o.shipped_at:
                sbd = calculate_business_date(o.shipped_at)
                if o.shipping_business_date != sbd:
                    o.shipping_business_date = sbd
                    changed = True
            elif o.shipping_business_date is not None:
                o.shipping_business_date = None
                changed = True
            nr = compute_net_revenue(o.amount, o.refund_amount, o.cancel_amount)
            if o.net_revenue != nr:
                o.net_revenue = nr
                changed = True
        if changed:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ensure_orders_schema(bind_engine) -> None:
    """기존 DB에 orders 확장 컬럼이 없으면 ALTER로 추가한다."""
    try:
        insp = inspect(bind_engine)
    except Exception:
        return
    if not insp.has_table("orders"):
        return
    names = {c["name"] for c in insp.get_columns("orders")}
    alters: list[str] = []
    extra: list[tuple[str, str]] = [
        ("ordered_at", "DATETIME NULL"),
        ("placed_order_at", "DATETIME NULL"),
        ("shipped_at", "DATETIME NULL"),
        ("content_order_no", "VARCHAR(64) NULL"),
        ("order_business_date", "DATE NULL"),
        ("payment_business_date", "DATE NULL"),
        ("shipping_business_date", "DATE NULL"),
        ("refund_amount", "INTEGER NOT NULL DEFAULT 0"),
        ("cancel_amount", "INTEGER NOT NULL DEFAULT 0"),
        ("net_revenue", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, ddl in extra:
        if col not in names:
            alters.append(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
    if alters:
        with bind_engine.begin() as conn:
            for stmt in alters:
                conn.execute(text(stmt))

    from app.models import Order

    db = SessionLocal()
    try:
        n_null = db.scalar(
            select(func.count()).select_from(Order).where(Order.payment_business_date.is_(None))
        )
    finally:
        db.close()

    if alters or (n_null and n_null > 0):
        _backfill_orders_revenue_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
