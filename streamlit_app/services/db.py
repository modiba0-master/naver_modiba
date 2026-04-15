import os

from sqlalchemy import Engine, create_engine


def _normalize_database_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("mysql://"):
        return value.replace("mysql://", "mysql+pymysql://", 1)
    return value


def get_engine() -> Engine:
    database_url = _normalize_database_url(os.getenv("DATABASE_URL", ""))
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set.")
    return create_engine(database_url, pool_pre_ping=True)

