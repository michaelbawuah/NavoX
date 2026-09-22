import pytest
from pydantic import SecretStr

from navox.ai.factory import AIProviderNotConfigured, build_ai_gateway
from navox.ai.openai_provider import OpenAIResponsesProvider
from navox.core.settings import Settings


def test_ai_gateway_is_disabled_by_default() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(AIProviderNotConfigured, match="Unsupported AI provider: disabled"):
        build_ai_gateway(settings)


def test_openai_provider_requires_secret_when_enabled() -> None:
    settings = Settings(_env_file=None, ai_provider="openai", openai_api_key=None)

    with pytest.raises(AIProviderNotConfigured, match="OPENAI_API_KEY"):
        build_ai_gateway(settings)


def test_openai_provider_is_built_only_behind_gateway() -> None:
    settings = Settings(
        _env_file=None,
        ai_provider="openai",
        openai_api_key=SecretStr("test-key"),
        openai_model="gpt-5.6-luna",
    )

    gateway = build_ai_gateway(settings)

    assert isinstance(gateway.provider, OpenAIResponsesProvider)
    assert gateway.provider.model == "gpt-5.6-luna"
    assert gateway.provider.api_key.get_secret_value() == "test-key"


def test_settings_reject_unknown_ai_provider() -> None:
    with pytest.raises(ValueError, match="AI_PROVIDER must be disabled or openai"):
        Settings(_env_file=None, ai_provider="unknown")
