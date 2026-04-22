import sys

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from services.db_url import get_streamlit_database_url

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        database_url = (get_streamlit_database_url() or "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set.")
        if "pytest" not in sys.modules:
            from sqlalchemy.engine.url import make_url

            try:
                p = make_url(database_url)
                print(f"[streamlit] DB host={p.host!r} (services.db_url, no app package)")
            except Exception:
                pass
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
