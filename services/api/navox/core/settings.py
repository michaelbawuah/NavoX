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
    canvas_base_url: str = ""
    canvas_oauth_client_id: str = ""
    canvas_oauth_client_secret: SecretStr | None = None
    canvas_oauth_redirect_uri: str = "http://localhost:8000/api/v1/connectors/canvas-lms/callback"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/connections/google/callback"
    google_token_encryption_key: SecretStr | None = None
    connector_secret_encryption_key: SecretStr | None = None
    google_gmail_push_subscription: str = ""
    google_gmail_watch_topic: str = ""
    google_calendar_push_url: str = ""
    google_pubsub_push_audience: str = ""
    google_pubsub_push_service_account: str = ""
    google_gmail_push_verification_token: SecretStr | None = None
    ai_provider: str = "disabled"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_read_timeout_seconds: float = Field(default=120.0, ge=1.0, le=300.0, allow_inf_nan=False)
    commitment_moderate_confidence_threshold: float = Field(default=0.65, ge=0.0, lt=1.0)
    commitment_high_confidence_threshold: float = Field(default=0.85, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_commitment_confidence_thresholds(self) -> "Settings":
        if self.ai_provider.casefold().strip() not in {"disabled", "openai"}:
            raise ValueError("AI_PROVIDER must be disabled or openai")

        if (
            self.commitment_high_confidence_threshold
            <= self.commitment_moderate_confidence_threshold
        ):
            raise ValueError("High commitment confidence threshold must exceed moderate threshold")

        if self.app_environment.casefold() in {"production", "prod"}:
            if not self.web_origin.casefold().startswith("https://"):
                raise ValueError("Production web origin must use HTTPS")
            if self.google_oauth_client_id:
                if not self.google_oauth_redirect_uri.casefold().startswith("https://"):
                    raise ValueError("Production Google OAuth redirect must use HTTPS")
                if self.google_oauth_client_secret is None:
                    raise ValueError("Production Google OAuth requires a client secret")
                if self.google_token_encryption_key is None:
                    raise ValueError("Production Google OAuth requires token encryption")
        return self

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
