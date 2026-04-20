from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

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
    ]
    for col, ddl in extra:
        if col not in names:
            alters.append(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
    if not alters:
        return
    with bind_engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
