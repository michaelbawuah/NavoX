from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = next(
    (
        candidate / ".env"
        for candidate in (SERVICE_ROOT, *SERVICE_ROOT.parents)
        if (candidate / ".env").is_file()
    ),
    SERVICE_ROOT / ".env",
)


class Settings(BaseSettings):
    app_environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://navox:navox@localhost:5432/navox"
    temporal_target: str = "localhost:7233"
    temporal_task_queue: str = "navox-foundation"
    web_origin: str = "http://localhost:3000"
    session_cookie_name: str = "navox_session"
    session_ttl_hours: int = Field(default=168, ge=1, le=720)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
