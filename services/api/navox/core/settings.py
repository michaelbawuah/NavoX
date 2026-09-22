from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
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
    temporal_task_queue: str = "navox-agent"
    web_origin: str = "http://localhost:3000"
    session_cookie_name: str = "navox_session"
    session_ttl_hours: int = Field(default=168, ge=1, le=720)
    extension_session_ttl_hours: int = Field(default=72, ge=1, le=168)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/connections/google/callback"
    google_token_encryption_key: SecretStr | None = None
    google_gmail_push_subscription: str = ""
    google_pubsub_push_audience: str = ""
    google_pubsub_push_service_account: str = ""
    google_gmail_push_verification_token: SecretStr | None = None
    commitment_moderate_confidence_threshold: float = Field(default=0.65, ge=0.0, lt=1.0)
    commitment_high_confidence_threshold: float = Field(default=0.85, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_commitment_confidence_thresholds(self) -> "Settings":
        if (
            self.commitment_high_confidence_threshold
            <= self.commitment_moderate_confidence_threshold
        ):
            raise ValueError("High commitment confidence threshold must exceed moderate threshold")
        return self

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
