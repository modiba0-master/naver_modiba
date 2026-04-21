import os
import sys

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import make_url

from app.db_url_utils import encode_mysql_password_in_url, print_database_url_diagnostics

load_dotenv()


class Settings(BaseSettings):
    database_url: str = "sqlite:///./app.db"
    ntfy_topic: str = "naver-commerce-orders"
    order_poll_interval_seconds: int = 60
    enable_worker: bool = True
    # 배포 시 별도 설정 없이 API 프로세스에서 1분 주기 sync(기본). 워커만 쓸 때 false.
    run_sync_scheduler_in_api: bool = Field(
        default=True, alias="RUN_SYNC_SCHEDULER_IN_API"
    )
    naver_commerce_api_client_id: str | None = None
    naver_commerce_api_client_secret: str | None = None
    naver_client_id: str | None = Field(default=None, alias="NAVER_CLIENT_ID")
    naver_client_secret: str | None = Field(default=None, alias="NAVER_CLIENT_SECRET")
    naver_seller_id: str | None = Field(default=None, alias="NAVER_SELLER_ID")
    naver_commerce_api_base_url: str = "https://api.commerce.naver.com"
    naver_commerce_oauth_type: str = "SELF"
    naver_commerce_order_lookback_hours: int = 72
    # payment_datetime: 결제일시 구간 조회(누락 적음). last_changed: 변경일시 API(기존).
    naver_order_sync_mode: str = Field(
        default="payment_datetime", alias="NAVER_ORDER_SYNC_MODE"
    )
    sync_api_key: str | None = Field(default=None, alias="SYNC_API_KEY")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if os.getenv("DATABASE_URL_USE_PUBLIC", "").lower() in ("1", "true", "yes"):
            pub = os.getenv("DATABASE_PUBLIC_URL", "").strip().strip('"').strip("'")
            if pub:
                value = pub
        url = str(value or "").strip().strip('"').strip("'")
        if url.startswith("mariadb://"):
            url = url.replace("mariadb://", "mysql+pymysql://", 1)
        if url.startswith("mysql://") and "pymysql" not in url:
            url = url.replace("mysql://", "mysql+pymysql://", 1)
        url = encode_mysql_password_in_url(url)
        return url

    @field_validator("database_url", mode="after")
    @classmethod
    def validate_database_url_parseable(cls, value: str) -> str:
        if not value or value.startswith("sqlite"):
            return value
        if "${{" in value or "{{" in value:
            raise ValueError(
                "DATABASE_URL에 ${{...}} 참조가 그대로 들어가 있습니다. "
                "Railway에서 치환된 완성 문자열을 넣거나, 로컬은 .env에 직접 값을 넣으세요."
            )
        try:
            make_url(value)
        except Exception as exc:
            raise ValueError(
                "DATABASE_URL을 SQLAlchemy가 파싱할 수 없습니다. "
                "비밀번호에 @ : / # 등이 있으면 % 인코딩하고, 따옴표·줄바꿈이 섞이지 않았는지 확인하세요. "
                f"원인: {exc}"
            ) from exc
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
if "pytest" not in sys.modules:
    if os.getenv("DATABASE_URL_USE_PUBLIC", "").lower() in ("1", "true", "yes"):
        print("[config] DATABASE_URL_USE_PUBLIC=1 → 연결 문자열은 DATABASE_PUBLIC_URL 기준")
    print_database_url_diagnostics(settings.database_url)
