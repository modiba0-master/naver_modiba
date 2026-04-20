from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        url = str(value or "").strip()
        if url.startswith("mariadb://"):
            return url.replace("mariadb://", "mysql+pymysql://", 1)
        if url.startswith("mysql://") and "pymysql" not in url:
            return url.replace("mysql://", "mysql+pymysql://", 1)
        return url

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
