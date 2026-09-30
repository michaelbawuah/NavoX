"""Native embedding HTTP fixtures; no live models, registrations or provider calls."""

import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from navox.ai.catalog import catalog_template
from navox.ai.embedding_contracts import (
    EMBEDDING,
    EmbeddingInput,
    EmbeddingOutput,
    embedding_artifacts,
)
from navox.ai.foundation.adapter import ProviderRequest
from navox.ai.foundation.contracts import Capability, JSONDocument, Profile, Provider, TaskType
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.providers import (
    AdapterFailure,
    ClaudeAdapter,
    GeminiAdapter,
    GrokAdapter,
    OpenAIAdapter,
)

MODEL = "fixture-embedding-v1"


def definition(provider):
    return ModelDefinition(
        reference=ModelRef(provider=provider, model=MODEL),
        capabilities=frozenset({Capability.EMBEDDINGS}),
        enabled=True,
        context_window=32768,
        max_output_tokens=1,
        input_cost_per_million=Decimal("0.1"),
        output_cost_per_million=Decimal("0"),
    )


def request(**kwargs):
    prompt, schema = embedding_artifacts()
    return ProviderRequest(
        task_id=uuid4(),
        operation="embedding",
        model=MODEL,
        instructions=prompt.instructions,
        context=JSONDocument(
            text=EmbeddingInput(
                text="Budget for the workshop", purpose="RETRIEVAL_QUERY", dimensions=3
            ).model_dump_json()
        ),
        output_schema=schema.document,
        schema_ref=EMBEDDING,
        prompt_ref=EMBEDDING,
        max_output_tokens=1,
        **kwargs,
    )


def body(provider, vector=(0.1, 0.2, -0.3), usage=True):
    if provider == Provider.OPENAI:
        result = {"model": MODEL, "data": [{"index": 0, "embedding": list(vector)}]}
        if usage:
            result["usage"] = {"prompt_tokens": 7, "total_tokens": 7}
    else:
        result = {"embedding": {"values": list(vector)}}
        if usage:
            result["usageMetadata"] = {"promptTokenCount": 7}
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [OpenAIAdapter, GeminiAdapter])
async def test_embedding_uses_native_transport_with_only_selected_text(cls):
    observed = []

    def handle(req):
        observed.append(req)
        return httpx.Response(200, json=body(cls.provider))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = cls(
            api_key=SecretStr("fixture"), models=(definition(cls.provider),), client=client
        )
        response = await adapter.execute(request())
    payload = json.loads(observed[0].content)
    assert "instructions" not in payload and "output_schema" not in payload
    if cls == OpenAIAdapter:
        assert observed[0].url.path == "/v1/embeddings"
        assert payload == {
            "input": "Budget for the workshop",
            "model": MODEL,
            "encoding_format": "float",
            "dimensions": 3,
        }
    else:
        assert observed[0].url.path.endswith(":embedContent")
        assert payload["content"]["parts"][0]["text"] == "Budget for the workshop"
        assert payload["embedContentConfig"] == {
            "taskType": "RETRIEVAL_QUERY",
            "outputDimensionality": 3,
        }
    assert EmbeddingOutput.model_validate_json(response.output.text).vector == (0.1, 0.2, -0.3)
    assert response.usage.input_tokens == 7 and response.usage.output_tokens == 0
    assert response.model == MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [OpenAIAdapter, GeminiAdapter])
@pytest.mark.parametrize("vector", [[], [0, 0, 0], [True, 0.2, 0.3], ["0.1", 0.2, 0.3], [0.1, 0.2]])
async def test_invalid_or_wrong_dimension_vectors_are_rejected(cls, vector):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=body(cls.provider, vector))
        )
    ) as client:
        adapter = cls(
            api_key=SecretStr("fixture"), models=(definition(cls.provider),), client=client
        )
        with pytest.raises(AdapterFailure) as error:
            await adapter.execute(request())
        assert error.value.detail.code == "invalid_response"


@pytest.mark.parametrize(
    "vector", [[float("nan")], [float("inf")], [1e308, 1e308, 1e308, 1e308], [True]]
)
def test_embedding_contract_refuses_nonfinite_or_invalid_vectors(vector):
    with pytest.raises(ValueError):
        EmbeddingOutput(vector=vector)


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [GrokAdapter, ClaudeAdapter])
async def test_provider_without_embedding_transport_cannot_claim_capability(cls):
    adapter = cls(api_key=SecretStr("fixture"), models=(definition(cls.provider),))
    assert Capability.EMBEDDINGS not in adapter.capabilities(MODEL)
    with pytest.raises(AdapterFailure):
        await adapter.execute(request())


@pytest.mark.asyncio
async def test_model_substitution_and_missing_usage_stay_distinct():
    for substitute in (False, True):
        response = body(Provider.OPENAI, usage=False)
        if substitute:
            response["model"] = "another-model"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _, selected=response: httpx.Response(200, json=selected)
            )
        ) as client:
            adapter = OpenAIAdapter(
                api_key=SecretStr("fixture"), models=(definition(Provider.OPENAI),), client=client
            )
            if substitute:
                with pytest.raises(AdapterFailure):
                    await adapter.execute(request())
            else:
                result = await adapter.execute(request())
                assert result.usage.input_tokens is None
                assert adapter.estimate_cost(MODEL, result.usage) is None


def test_embedding_profile_has_no_implicit_model_assignment():
    registry = catalog_template()
    profile = next(p for p in registry.profiles if p.profile == Profile.EMBEDDING)
    assert profile.task_types == frozenset({TaskType.EMBED})
    assert profile.required_capabilities == frozenset({Capability.EMBEDDINGS})
    assert profile.assignments == ()
    assert registry.models == ()
