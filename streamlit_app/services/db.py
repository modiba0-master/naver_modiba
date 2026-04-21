import sys
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv()

from app.config import settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        database_url = (settings.database_url or "").strip()
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

