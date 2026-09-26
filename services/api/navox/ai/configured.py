"""Server-only construction from an operator policy and the published catalog."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.factory import AIProviderNotConfigured
from navox.ai.foundation.adapter import AIProviderAdapter
from navox.ai.foundation.contracts import Provider
from navox.ai.foundation.persistence import RegistryStore
from navox.ai.foundation.registry import RegistrySnapshot
from navox.ai.providers import ClaudeAdapter, GeminiAdapter, GrokAdapter, OpenAIAdapter
from navox.ai.routing import PolicyRules
from navox.ai.runtime import GatewayRuntime
from navox.ai.store import GatewayStore
from navox.core.settings import Settings
from navox.db.session import get_session_factory


async def build_runtime(
    settings: Settings, factory: async_sessionmaker[AsyncSession] | None = None
) -> GatewayRuntime:
    if settings.ai_provider != "automatic":
        raise AIProviderNotConfigured("The registered gateway is not enabled")
    factory = factory or get_session_factory()
    try:
        policy = PolicyRules.model_validate(settings.ai_provider_policy)
        async with factory() as database:
            registry = await RegistryStore(database).load()
    except ValueError:
        raise AIProviderNotConfigured("AI routing configuration is unavailable") from None
    if registry is None:
        raise AIProviderNotConfigured("AI catalog is not configured")
    return GatewayRuntime(GatewayStore(factory, policy), configured_adapters(settings, registry))


def configured_adapters(
    settings: Settings, registry: RegistrySnapshot
) -> dict[Provider, AIProviderAdapter]:
    adapters: dict[Provider, AIProviderAdapter] = {}
    for provider, key, adapter in (
        (Provider.OPENAI, settings.openai_api_key, OpenAIAdapter),
        (Provider.GEMINI, settings.gemini_api_key, GeminiAdapter),
        (Provider.ANTHROPIC, settings.anthropic_api_key, ClaudeAdapter),
        (Provider.XAI, settings.xai_api_key, GrokAdapter),
    ):
        if key is not None and key.get_secret_value():
            adapters[provider] = adapter(
                api_key=key,
                models=tuple(m for m in registry.models if m.reference.provider == provider),
                timeout_seconds=settings.openai_read_timeout_seconds,
            )
    return adapters
