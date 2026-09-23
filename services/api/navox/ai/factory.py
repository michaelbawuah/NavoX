from __future__ import annotations

from navox.ai.gateway import AIGateway
from navox.ai.openai_provider import OpenAIResponsesProvider
from navox.core.settings import Settings


class AIProviderNotConfigured(RuntimeError):
    pass


def build_ai_gateway(settings: Settings) -> AIGateway:
    """Build the configured provider behind the provider-neutral NavoX gateway."""

    provider = settings.ai_provider.casefold().strip()
    if provider == "openai":
        if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
            raise AIProviderNotConfigured("OPENAI_API_KEY is required when AI_PROVIDER=openai")
        return AIGateway(
            OpenAIResponsesProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                timeout_seconds=settings.openai_read_timeout_seconds,
            )
        )
    raise AIProviderNotConfigured(f"Unsupported AI provider: {settings.ai_provider}")
