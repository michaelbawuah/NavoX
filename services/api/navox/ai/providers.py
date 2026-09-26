"""Four HTTP adapters under one contract. No permissions, state writes, or executors."""

import asyncio
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import httpx
from pydantic import SecretStr

from navox.ai.foundation.adapter import (
    ErrorCode,
    ProviderError,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
)
from navox.ai.foundation.contracts import Capability, FinishReason, JSONDocument, Provider, Usage
from navox.ai.foundation.registry import ModelDefinition


class AdapterFailure(RuntimeError):
    def __init__(self, code: ErrorCode, *, retry_after_ms: int | None = None) -> None:
        self.detail = ProviderError(code=code, retry_after_ms=retry_after_ms)
        super().__init__(f"AI provider failure: {code.value}")


def invalid() -> AdapterFailure:
    return AdapterFailure(ErrorCode.INVALID_RESPONSE)


class HTTPAdapter:
    provider: Provider
    endpoint: str
    models_endpoint: str
    supported_capabilities = frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT})

    def __init__(
        self,
        *,
        api_key: SecretStr,
        models: tuple[ModelDefinition, ...],
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        if not api_key.get_secret_value() or not 0 < timeout_seconds <= 300:
            raise ValueError("A provider credential and bounded timeout are required")
        if any(m.reference.provider != self.provider for m in models):
            raise ValueError("Model registration belongs to another provider")
        self._api_key, self._client, self._timeout = api_key, client, timeout_seconds
        self._models = {m.reference.model: m for m in models}

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

    def classify_error(self, error: Exception) -> ProviderError:
        if isinstance(error, AdapterFailure):
            return error.detail
        if isinstance(error, (TimeoutError, httpx.TimeoutException)):
            return ProviderError(code=ErrorCode.TIMEOUT)
        if isinstance(error, httpx.HTTPError):
            return ProviderError(code=ErrorCode.UNAVAILABLE)
        return ProviderError(code=ErrorCode.UNKNOWN)

    async def _request(
        self, endpoint: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(trust_env=False)
        try:
            async with asyncio.timeout(self._timeout):
                async with client.stream(
                    "POST" if payload is not None else "GET",
                    endpoint,
                    json=payload,
                    headers=self.headers(),
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        code = (
                            ErrorCode.AUTHENTICATION
                            if response.status_code in {401, 403}
                            else ErrorCode.RATE_LIMIT
                            if response.status_code == 429
                            else ErrorCode.UNAVAILABLE
                            if response.status_code >= 500
                            else ErrorCode.INVALID_REQUEST
                        )
                        retry = response.headers.get("retry-after", "")
                        retry_ms = (
                            min(86_400_000, int(retry) * 1000)
                            if retry.isdigit() and len(retry) < 8
                            else None
                        )
                        raise AdapterFailure(code, retry_after_ms=retry_ms)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise invalid()
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise invalid()
                    return body
        except (TimeoutError, httpx.TimeoutException):
            raise AdapterFailure(ErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise AdapterFailure(ErrorCode.UNAVAILABLE) from None
        except (ValueError, UnicodeError, RecursionError):
            raise invalid() from None
        finally:
            if self._client is None:
                await client.aclose()

    async def list_models(self) -> tuple[ModelDefinition, ...]:
        body = await self._request(self.models_endpoint)
        rows = body.get("models" if self.provider == Provider.GEMINI else "data")
        if not isinstance(rows, list):
            raise invalid()
        identifiers = {
            str(row.get("id") or row.get("name", "")).removeprefix("models/")
            for row in rows
            if isinstance(row, dict)
        }
        return tuple(model for key, model in self._models.items() if key in identifiers)

    def capabilities(self, model: str) -> frozenset[Capability]:
        definition = self._models.get(model)
        return definition.capabilities & self.supported_capabilities if definition else frozenset()

    def estimate_cost(self, model: str, usage: Usage) -> Decimal | None:
        definition = self._models.get(model)
        if definition is None or usage.input_tokens is None or usage.output_tokens is None:
            return None
        if definition.input_cost_per_million is None or definition.output_cost_per_million is None:
            return None
        return (
            definition.input_cost_per_million * usage.input_tokens
            + definition.output_cost_per_million * usage.output_tokens
        ) / Decimal(1_000_000)

    async def health_probe(self) -> ProviderHealth:
        try:
            await self.list_models()
        except AdapterFailure as error:
            return (
                ProviderHealth.DISABLED
                if error.detail.code == ErrorCode.AUTHENTICATION
                else ProviderHealth.UNAVAILABLE
            )
        return ProviderHealth.HEALTHY

    def payload(self, request: ProviderRequest) -> dict[str, Any]:
        raise NotImplementedError

    def decode(self, body: Mapping[str, Any]) -> ProviderResponse:
        raise NotImplementedError

    def normalize_response(self, response: Mapping[str, object]) -> ProviderResponse:
        try:
            return self.decode(response)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
            raise invalid() from None

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        request = ProviderRequest.model_validate(request)
        model = self._models.get(request.model)
        if model is None or request.max_output_tokens > model.max_output_tokens:
            raise AdapterFailure(ErrorCode.INVALID_REQUEST)
        # Configured model names can never create URLs or another network target.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", request.model):
            raise AdapterFailure(ErrorCode.INVALID_REQUEST)
        body = await self._request(self.endpoint.format(model=request.model), self.payload(request))
        response = self.normalize_response(body)
        if response.model != request.model:
            raise invalid()
        return response


class OpenAIAdapter(HTTPAdapter):
    provider = Provider.OPENAI
    endpoint = "https://api.openai.com/v1/responses"
    models_endpoint = "https://api.openai.com/v1/models"

    def payload(self, request: ProviderRequest) -> dict[str, Any]:
        name = re.sub(r"[^A-Za-z0-9_-]", "_", request.schema_ref.name)[:64]
        return {
            "model": request.model,
            "instructions": request.instructions,
            "input": request.context.text,
            "store": False,
            "max_output_tokens": request.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": name,
                    "schema": json.loads(request.output_schema.text),
                    "strict": True,
                }
            },
        }

    def decode(self, body: Mapping[str, Any]) -> ProviderResponse:
        if body.get("status") != "completed" or body.get("error") or body.get("incomplete_details"):
            raise invalid()
        texts = []
        for item in body["output"]:
            if item["type"] == "reasoning":
                continue
            if item["type"] != "message" or item.get("status", "completed") != "completed":
                raise invalid()
            for part in item["content"]:
                if part["type"] == "refusal":
                    raise AdapterFailure(ErrorCode.REFUSAL)
                if part["type"] != "output_text":
                    raise invalid()
                texts.append(part["text"])
        if len(texts) != 1:
            raise invalid()
        raw_usage = body.get("usage") or {}
        return ProviderResponse(
            model=body["model"],
            output=JSONDocument(text=texts[0]),
            finish_reason=FinishReason.STOP,
            usage=Usage(
                input_tokens=raw_usage.get("input_tokens"),
                output_tokens=raw_usage.get("output_tokens"),
            ),
        )


class GrokAdapter(OpenAIAdapter):
    provider = Provider.XAI
    endpoint = "https://api.x.ai/v1/responses"
    models_endpoint = "https://api.x.ai/v1/models"


class GeminiAdapter(HTTPAdapter):
    provider = Provider.GEMINI
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    models_endpoint = "https://generativelanguage.googleapis.com/v1beta/models"

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key.get_secret_value()}

    def payload(self, request: ProviderRequest) -> dict[str, Any]:
        return {
            "systemInstruction": {"parts": [{"text": request.instructions}]},
            "contents": [{"role": "user", "parts": [{"text": request.context.text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": json.loads(request.output_schema.text),
                "maxOutputTokens": request.max_output_tokens,
            },
        }

    def decode(self, body: Mapping[str, Any]) -> ProviderResponse:
        candidates = body["candidates"]
        if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise invalid()
        parts = candidates[0]["content"]["parts"]
        if any("functionCall" in p or not isinstance(p.get("text"), str) for p in parts):
            raise invalid()
        texts = [p["text"] for p in parts if not p.get("thought")]
        raw_usage = body.get("usageMetadata") or {}
        count = raw_usage.get("candidatesTokenCount")
        thoughts = raw_usage.get("thoughtsTokenCount", 0)
        if count is not None:
            if type(count) is not int or type(thoughts) is not int or thoughts < 0:
                raise invalid()
            count += thoughts
        return ProviderResponse(
            model=body["modelVersion"],
            output=JSONDocument(text="".join(texts)),
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=raw_usage.get("promptTokenCount"), output_tokens=count),
        )


class ClaudeAdapter(HTTPAdapter):
    provider = Provider.ANTHROPIC
    endpoint = "https://api.anthropic.com/v1/messages"
    models_endpoint = "https://api.anthropic.com/v1/models"

    def headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key.get_secret_value(), "anthropic-version": "2023-06-01"}

    def payload(self, request: ProviderRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "system": request.instructions,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": request.context.text}],
            "output_config": {
                "format": {"type": "json_schema", "schema": json.loads(request.output_schema.text)}
            },
        }

    def decode(self, body: Mapping[str, Any]) -> ProviderResponse:
        if body.get("stop_reason") == "refusal":
            raise AdapterFailure(ErrorCode.REFUSAL)
        if body.get("stop_reason") != "end_turn":
            raise invalid()
        parts = body["content"]
        if any(p["type"] not in {"text", "thinking", "redacted_thinking"} for p in parts):
            raise invalid()
        texts = [p["text"] for p in parts if p["type"] == "text"]
        if len(texts) != 1:
            raise invalid()
        raw_usage = body.get("usage") or {}
        count = raw_usage.get("input_tokens")
        if count is not None:
            counts = [
                count,
                raw_usage.get("cache_read_input_tokens", 0),
                raw_usage.get("cache_creation_input_tokens", 0),
            ]
            if any(type(c) is not int or c < 0 for c in counts):
                raise invalid()
            count = sum(counts)
        return ProviderResponse(
            model=body["model"],
            output=JSONDocument(text=texts[0]),
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=count, output_tokens=raw_usage.get("output_tokens")),
        )
