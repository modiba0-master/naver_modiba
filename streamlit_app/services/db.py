import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _normalize_database_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("mysql://"):
        return value.replace("mysql://", "mysql+pymysql://", 1)
    return value


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        database_url = _normalize_database_url(os.getenv("DATABASE_URL", ""))
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set.")
        _engine = create_engine(database_url, pool_pre_ping=True)
    return _engine


def SessionLocal() -> Session:
    """FastAPI `app.database.SessionLocal`과 동일 패턴: `db = SessionLocal()` 후 `db.close()`."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autocommit=False, autoflush=False
        )
    return _SessionLocal()

