"""Operator-owned Vox inference configuration; never accepted from HTTP or a model.

The JSON contains route names and credential environment-variable *names*, never keys.
An absent path preserves the existing qualified gateway. Configuration reloads per call.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from navox.core.settings import Settings

MAX_CONFIG_BYTES = 32_768
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,179}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")


class InferenceConfigurationError(ValueError):
    """A safe operator diagnostic without paths, values, or validation inputs."""


class ProviderRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider: str = "openai"
    model: str = Field(min_length=1, max_length=180)
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    voice: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("provider", "model", "voice")
    @classmethod
    def route_name(cls, value: str | None) -> str | None:
        if value is not None and not _NAME.fullmatch(value):
            raise ValueError("Invalid route name")
        return value

    @field_validator("api_key_env")
    @classmethod
    def credential_name(cls, value: str) -> str:
        if not _ENV.fullmatch(value):
            raise ValueError("Invalid credential reference")
        return value


def endpoint_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("Inference endpoint must use an approved secure origin")
    _ = parsed.port
    return parsed.scheme + "://" + parsed.netloc


class VoxInferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal["vox-inference.v1"] = "vox-inference.v1"
    mode: Literal["realtime", "cascade"] = "realtime"
    fallback_enabled: bool = True
    allowed_origins: tuple[str, ...] = Field(
        default=("https://api.openai.com",), min_length=1, max_length=8
    )
    realtime: ProviderRoute = ProviderRoute(model="gpt-realtime-2.1", voice="marin")
    stt: ProviderRoute = ProviderRoute(model="gpt-transcribe")
    llm: ProviderRoute = ProviderRoute(model="gpt-5.6-luna")
    tts: ProviderRoute = ProviderRoute(model="gpt-4o-mini-tts-2025-12-15", voice="marin")
    tts_stream_format: Literal["audio", "sse"] = "audio"
    end_of_turn_silence_ms: int = Field(default=1200, strict=True, ge=400, le=2000)
    turn_timeout_seconds: float = Field(default=45, ge=5, le=90, allow_inf_nan=False)
    rollover_seconds: float = Field(default=3300, ge=60, le=3300, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1000, strict=True, ge=64, le=4096)
    max_response_audio_seconds: int = Field(default=30, strict=True, ge=1, le=60)
    max_context_characters: int = Field(default=400_000, strict=True, ge=1000, le=512_000)

    @model_validator(mode="after")
    def approved_endpoints(self) -> VoxInferenceConfig:
        origins = set()
        for origin in self.allowed_origins:
            if endpoint_origin(origin) != origin or urlsplit(origin).path:
                raise ValueError("Allowlist entries must be exact origins")
            origins.add(origin)
        for route in (self.realtime, self.stt, self.llm, self.tts):
            if endpoint_origin(route.base_url) not in origins:
                raise ValueError("Inference endpoint is not operator-allowlisted")
            if route.base_url.endswith("/") or not urlsplit(route.base_url).path.startswith("/"):
                raise ValueError("Inference base URL must have a fixed API path")
        if self.realtime.voice is None or self.tts.voice is None:
            raise ValueError("Spoken routes require an explicit voice")
        return self


def load_inference_config(settings: Settings) -> VoxInferenceConfig | None:
    """Read one bounded regular operator file; fail closed on unsafe or invalid input."""
    raw_path = getattr(settings, "student_vox_inference_config_path", "")
    if not raw_path:
        return None
    try:
        path = Path(raw_path)
        if not path.is_absolute():
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                raise ValueError
            # The running service user or root owns this deployment configuration.
            if info.st_uid not in {0, os.geteuid()} or info.st_size > MAX_CONFIG_BYTES:
                raise ValueError
            payload = handle.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise ValueError
        return VoxInferenceConfig.model_validate_json(payload)
    except (OSError, ValueError):
        raise InferenceConfigurationError("Vox inference configuration is unavailable") from None


def route_credential(route: ProviderRoute, settings: Settings) -> SecretStr:
    """Resolve only the selected server route's reference; never include it in diagnostics."""
    key = (
        settings.openai_api_key
        if route.api_key_env == "OPENAI_API_KEY"
        else SecretStr(os.environ.get(route.api_key_env, ""))
    )
    if key is None or not key.get_secret_value().strip():
        raise InferenceConfigurationError("Vox inference credential is unavailable")
    return key
