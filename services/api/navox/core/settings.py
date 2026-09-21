from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    app_environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://navox:navox@localhost:5432/navox"
    temporal_target: str = "localhost:7233"
    temporal_task_queue: str = "navox-foundation"

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
