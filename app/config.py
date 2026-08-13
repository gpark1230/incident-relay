from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://incident_relay:incident_relay@localhost:5433/incident_relay"
    redis_url: str = "redis://localhost:6380/0"

    notify_webhook_url: str = ""
    incident_desk_api_url: str = "http://localhost:8000"
    incident_desk_service_email: str = ""
    incident_desk_service_password: str = ""

    rate_limit_max_tokens: int = 5
    rate_limit_window_seconds: int = 60

    cache_ttl_seconds: int = 30


settings = Settings()
