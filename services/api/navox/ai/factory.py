from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.gateway import AIGateway
from navox.ai.openai_provider import OpenAIResponsesProvider
from navox.connectors.contracts import CanonicalResource
from navox.core.settings import Settings
from navox.intelligence.extraction import OperationalExtractionGateway


class AIProviderNotConfigured(RuntimeError):
    pass


def build_ai_gateway(
    settings: Settings,
    *,
    database: AsyncSession | None = None,
    workspace_id: UUID | None = None,
    user_id: UUID | None = None,
    connection_id: UUID | None = None,
    fetched_source: CanonicalResource | None = None,
) -> OperationalExtractionGateway:
    """Build the configured provider behind the provider-neutral NavoX gateway."""

    provider = settings.ai_provider.casefold().strip()
    if provider == "automatic":
        if database is None or workspace_id is None or user_id is None or connection_id is None:
            raise AIProviderNotConfigured("Automatic routing requires authorized feature scope")
        from navox.ai.features import RegisteredExtractionGateway

        return RegisteredExtractionGateway(
            settings,
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            connection_id=connection_id,
            fetched_source=fetched_source,
        )
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
