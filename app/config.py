from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./app.db"
    ntfy_topic: str = "naver-commerce-orders"
    order_poll_interval_seconds: int = 60
    enable_worker: bool = True
    naver_commerce_api_client_id: str | None = None
    naver_commerce_api_client_secret: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
