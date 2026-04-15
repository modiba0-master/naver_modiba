from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./app.db"
    ntfy_topic: str = "naver-commerce-orders"
    order_poll_interval_seconds: int = 60
    enable_worker: bool = True
    naver_commerce_api_client_id: str | None = None
    naver_commerce_api_client_secret: str | None = None
    naver_client_id: str | None = Field(default=None, alias="NAVER_CLIENT_ID")
    naver_client_secret: str | None = Field(default=None, alias="NAVER_CLIENT_SECRET")
    naver_seller_id: str | None = Field(default=None, alias="NAVER_SELLER_ID")
    naver_commerce_api_base_url: str = "https://api.commerce.naver.com"
    naver_commerce_oauth_type: str = "SELF"
    naver_commerce_order_lookback_hours: int = 72
    sync_api_key: str | None = Field(default=None, alias="SYNC_API_KEY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
