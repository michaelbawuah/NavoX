"""Provider-neutral speech transport. No credentials, policy, or action authority.

An adapter is a single-purpose HTTP boundary: it turns one already-validated
bounded clip plus a registry-selected model identifier into one bounded
transcript string, or into a fixed error classification. It never chooses a
model, holds a grant, persists audio, or exposes a response body.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Final, Protocol, runtime_checkable

import httpx
from pydantic import SecretStr

from navox.ai.foundation.adapter import ErrorCode, ProviderError
from navox.ai.foundation.contracts import Capability, Provider
from navox.core.settings import Settings

# The fixed HTTPS destination. Model identity can never create a network target.
OPENAI_TRANSCRIPTIONS_URL: Final[str] = "https://api.openai.com/v1/audio/transcriptions"
MAX_AUDIO_REQUEST_BYTES: Final[int] = 1_000_000
MAX_TRANSCRIPTION_RESPONSE_BYTES: Final[int] = 262_144
MAX_PROVIDER_TRANSCRIPT_CHARACTERS: Final[int] = 4_096
WAV_CONTENT_TYPE: Final[str] = "audio/wav"
MODEL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@runtime_checkable
class SpeechAdapter(Protocol):
    """One bounded audio request. Implementations keep no audio or transcript state."""

    @property
    def provider(self) -> Provider: ...

    def capabilities(self, model: str) -> frozenset[Capability]: ...

    async def transcribe(self, *, model: str, audio: bytes, timeout_seconds: float) -> str: ...

    def classify_error(self, error: Exception) -> ProviderError: ...


class SpeechAdapterFailure(RuntimeError):
    """A fixed transport classification; never an upstream body or credential."""

    def __init__(self, detail: ProviderError) -> None:
        self.detail = detail
        super().__init__(f"Speech provider failure: {detail.code.value}")


def _retry_after_ms(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after", "")
    if value.isdigit() and len(value) < 8:
        return min(86_400_000, int(value) * 1000)
    return None


def _is_wav_container(audio: bytes) -> bool:
    """A bare PCM stream mislabeled as WAV must never leave this boundary."""

    return len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


class OpenAISpeechAdapter:
    """Minimal OpenAI `/v1/audio/transcriptions` adapter.

    The adapter is constructed with a server-only key and never accepts a
    caller-chosen destination, redirect, or model outside the published
    identifier shape.
    """

    provider = Provider.OPENAI

    def __init__(self, *, api_key: SecretStr, client: httpx.AsyncClient | None = None) -> None:
        if not api_key.get_secret_value():
            raise ValueError("An OpenAI credential is required for speech")
        self._api_key, self._client = api_key, client

    def capabilities(self, model: str) -> frozenset[Capability]:
        if not MODEL_IDENTIFIER.fullmatch(model):
            return frozenset()
        return frozenset({Capability.TRANSCRIPTION})

    def classify_error(self, error: Exception) -> ProviderError:
        if isinstance(error, SpeechAdapterFailure):
            return error.detail
        if isinstance(error, (TimeoutError, httpx.TimeoutException)):
            return ProviderError(code=ErrorCode.TIMEOUT)
        if isinstance(error, httpx.HTTPError):
            return ProviderError(code=ErrorCode.UNAVAILABLE)
        return ProviderError(code=ErrorCode.UNKNOWN)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

    async def transcribe(self, *, model: str, audio: bytes, timeout_seconds: float) -> str:
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Speech timeout must be positive, finite, and at most 300 seconds")
        if not MODEL_IDENTIFIER.fullmatch(model) or not _is_wav_container(audio):
            raise SpeechAdapterFailure(ProviderError(code=ErrorCode.INVALID_REQUEST))
        if len(audio) > MAX_AUDIO_REQUEST_BYTES:
            raise SpeechAdapterFailure(ProviderError(code=ErrorCode.INVALID_REQUEST))

        timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(10.0, timeout_seconds),
            write=timeout_seconds,
            read=timeout_seconds,
            pool=min(5.0, timeout_seconds),
        )
        client = self._client or httpx.AsyncClient(trust_env=False)
        try:
            # Redirects stay off: a model or provider response can never move the
            # destination or forward the credential to another host.
            async with asyncio.timeout(timeout_seconds):
                async with client.stream(
                    "POST",
                    OPENAI_TRANSCRIPTIONS_URL,
                    headers=self._headers(),
                    data={"model": model},
                    files={"file": ("audio.wav", audio, WAV_CONTENT_TYPE)},
                    timeout=timeout,
                    follow_redirects=False,
                ) as response:
                    return await self._decode(response, model)
        except (TimeoutError, httpx.TimeoutException):
            raise SpeechAdapterFailure(ProviderError(code=ErrorCode.TIMEOUT)) from None
        except httpx.HTTPError:
            raise SpeechAdapterFailure(ProviderError(code=ErrorCode.UNAVAILABLE)) from None
        finally:
            if self._client is None:
                await client.aclose()

    async def _decode(self, response: httpx.Response, model: str) -> str:
        if not 200 <= response.status_code < 300:
            if response.status_code in {401, 403}:
                code = ErrorCode.AUTHENTICATION
            elif response.status_code == 429:
                code = ErrorCode.RATE_LIMIT
            elif response.status_code >= 500 or 300 <= response.status_code < 400:
                code = ErrorCode.UNAVAILABLE
            else:
                code = ErrorCode.INVALID_REQUEST
            raise SpeechAdapterFailure(
                ProviderError(code=code, retry_after_ms=_retry_after_ms(response))
            )
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > MAX_TRANSCRIPTION_RESPONSE_BYTES:
                raise SpeechAdapterFailure(ProviderError(code=ErrorCode.INVALID_RESPONSE))
        try:
            body = json.loads(bytes(raw))
        except (ValueError, UnicodeDecodeError):
            raise SpeechAdapterFailure(ProviderError(code=ErrorCode.INVALID_RESPONSE)) from None
        if not isinstance(body, dict) or body.get("error") is not None:
            raise SpeechAdapterFailure(ProviderError(code=ErrorCode.INVALID_RESPONSE))
        text = body.get("text")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > MAX_PROVIDER_TRANSCRIPT_CHARACTERS
        ):
            raise SpeechAdapterFailure(ProviderError(code=ErrorCode.INVALID_RESPONSE))
        returned_model = body.get("model")
        if returned_model is not None:
            # Omission is allowed (the documented transcription response omits
            # it); a contradicting identity is not.
            if (
                not isinstance(returned_model, str)
                or not returned_model.strip()
                or len(returned_model) > 180
                or returned_model != model
            ):
                raise SpeechAdapterFailure(ProviderError(code=ErrorCode.INVALID_RESPONSE))
        return text


def configured_speech_adapters(settings: Settings) -> dict[Provider, SpeechAdapter]:
    """Server-only adapters. An absent server key installs no provider."""

    key = settings.openai_api_key
    if key is None or not key.get_secret_value():
        return {}
    return {Provider.OPENAI: OpenAISpeechAdapter(api_key=key)}
