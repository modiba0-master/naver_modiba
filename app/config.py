from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import make_url


class Settings(BaseSettings):
    database_url: str = "sqlite:///./app.db"
    ntfy_topic: str = "naver-commerce-orders"
    order_poll_interval_seconds: int = 60
    enable_worker: bool = True
    run_sync_scheduler_in_api: bool = Field(
        default=False, alias="RUN_SYNC_SCHEDULER_IN_API"
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
        url = str(value or "").strip().strip('"').strip("'")
        if url.startswith("mariadb://"):
            url = url.replace("mariadb://", "mysql+pymysql://", 1)
        if url.startswith("mysql://") and "pymysql" not in url:
            url = url.replace("mysql://", "mysql+pymysql://", 1)
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
