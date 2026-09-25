from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import SecretStr

from navox.ai.errors import AIProviderError as AIProviderError
from navox.ai.errors import AIProviderRejectedOutput
from navox.ai.gateway import StructuredOutputResponse

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


def _http_error(response: httpx.Response) -> AIProviderError:
    """Map provider codes to a fixed vocabulary; discard the response body."""
    status = response.status_code
    try:
        body = response.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    details = body.get("error") if isinstance(body, dict) else None
    details = details if isinstance(details, dict) else {}
    provider_code = details.get("code")
    provider_type = details.get("type")
    provider_code = provider_code if isinstance(provider_code, str) else None
    provider_type = provider_type if isinstance(provider_type, str) else None
    if status == 401:
        code = "authentication_failed"
    elif status == 403:
        code = "permission_denied"
    elif provider_code == "model_not_found":
        code = "model_unavailable"
    elif (
        provider_code
        in {
            "insufficient_quota",
            "credit_balance_exhausted",
            "organization_spend_limit_exceeded",
            "project_spend_limit_exceeded",
            "organization_usage_limit_exceeded",
        }
        or provider_type == "insufficient_quota"
    ):
        code = "quota_exhausted"
    elif status == 429:
        code = "rate_limited"
    elif provider_code in {"invalid_json_schema", "invalid_schema"}:
        code = "invalid_schema"
    elif provider_code in {"unsupported_parameter", "unsupported_value"}:
        code = "unsupported_parameter"
    elif status >= 500:
        code = "provider_unavailable"
    elif status in {400, 404, 422}:
        code = "invalid_request"
    else:
        code = "provider_error"
    return AIProviderError(
        f"OpenAI Responses request failed with HTTP {status}", code=code, http_status=status
    )


class OpenAIResponsesProvider:
    """Minimal OpenAI Responses adapter for strict schema-constrained generation."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not 0 < timeout_seconds <= 300:
            raise ValueError(
                "OpenAI read timeout must be positive, finite, and at most 300 seconds"
            )
        self.api_key = api_key
        self.model = model
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def generate_json(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        instructions: str,
        input_text: str,
    ) -> StructuredOutputResponse:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "store": False,
            "max_output_tokens": 8_000,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(
            self.timeout_seconds,
            connect=min(10.0, self.timeout_seconds),
            write=min(30.0, self.timeout_seconds),
            pool=min(5.0, self.timeout_seconds),
        )
        try:
            # HTTPX limits inactivity in each network phase. Also bound the
            # whole exchange so a trickling response cannot occupy a worker forever.
            async with asyncio.timeout(self.timeout_seconds + 45.0):
                if self.client is not None:
                    response = await self.client.post(
                        OPENAI_RESPONSES_URL,
                        headers=headers,
                        json=payload,
                        timeout=timeout,
                    )
                else:
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            OPENAI_RESPONSES_URL,
                            headers=headers,
                            json=payload,
                            timeout=timeout,
                        )
        except (httpx.TimeoutException, TimeoutError):
            raise AIProviderError("OpenAI Responses request timed out", code="timeout") from None
        except httpx.RequestError:
            # Do not put credentials, request content, or transport diagnostics into
            # workflow failure history/logs through an exception chain.
            raise AIProviderError(
                "OpenAI Responses transport failed", code="transport_error"
            ) from None

        if response.status_code >= 400:
            raise _http_error(response)

        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AIProviderError(
                "OpenAI Responses returned invalid JSON", code="invalid_response", http_status=200
            ) from None

        if not isinstance(body, dict) or body.get("status") != "completed":
            raise AIProviderError(
                "OpenAI Responses did not complete successfully",
                code="incomplete_response",
                http_status=200,
            )
        if body.get("error") is not None or body.get("incomplete_details") is not None:
            raise AIProviderError(
                "OpenAI Responses contained an incomplete or failed result",
                code="incomplete_response",
                http_status=200,
            )

        output_text = _extract_output_text(body)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError:
            raise AIProviderRejectedOutput("OpenAI structured output was not valid JSON") from None
        if not isinstance(parsed, dict):
            raise AIProviderRejectedOutput("OpenAI structured output must be a JSON object")

        response_model = body.get("model")
        return StructuredOutputResponse(
            data=parsed,
            provider=self.provider_name,
            model=str(response_model) if response_model else self.model,
        )


def _extract_output_text(body: dict[str, Any]) -> str:
    output = body.get("output")
    if not isinstance(output, list):
        raise AIProviderRejectedOutput("OpenAI Responses contained invalid output")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("status") not in (None, "completed"):
            raise AIProviderError(
                "OpenAI Responses contained an incomplete message",
                code="incomplete_response",
                http_status=200,
            )
        content = item.get("content")
        if not isinstance(content, list):
            raise AIProviderRejectedOutput("OpenAI Responses contained invalid message content")
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise AIProviderRejectedOutput("OpenAI refused the operational extraction request")
            if part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
    if len(texts) == 1:
        return texts[0]
    raise AIProviderRejectedOutput("OpenAI Responses contained no structured output text")
