from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import SecretStr

from navox.ai.gateway import StructuredOutputResponse

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class AIProviderError(RuntimeError):
    """Raised when a configured AI provider cannot produce an accepted response."""


class OpenAIResponsesProvider:
    """Minimal OpenAI Responses adapter for strict schema-constrained generation."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
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

        if self.client is not None:
            response = await self.client.post(
                OPENAI_RESPONSES_URL,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    OPENAI_RESPONSES_URL,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )

        if response.status_code >= 400:
            raise AIProviderError(
                f"OpenAI Responses request failed with HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise AIProviderError("OpenAI Responses returned invalid JSON") from exc

        output_text = _extract_output_text(body)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise AIProviderError("OpenAI structured output was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise AIProviderError("OpenAI structured output must be a JSON object")

        response_model = body.get("model")
        return StructuredOutputResponse(
            data=parsed,
            provider=self.provider_name,
            model=str(response_model) if response_model else self.model,
        )


def _extract_output_text(body: dict[str, Any]) -> str:
    for item in body.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise AIProviderError("OpenAI refused the operational extraction request")
            if part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    return text
    raise AIProviderError("OpenAI Responses contained no structured output text")
