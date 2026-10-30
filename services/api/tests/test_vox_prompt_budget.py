"""Every server tool schema is inside the existing native prompt reservation."""

import json

import httpx
import pytest
from test_vox_inference import Check, Hooks, Socket, settings

from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline, ProviderRejected
from navox.vox.inference_realtime import OpenAIRealtimeAdapter


class Tools:
    def __init__(self, size=22_000):
        self.size = size

    def definitions(self):
        return [
            {
                "type": "function",
                "name": "large_fixture_tool",
                "strict": True,
                "description": "x" * self.size,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }
        ]

    async def execute(self, name, arguments):
        pytest.fail("This fixture never authorizes a tool call")


class Budget(Hooks):
    async def begin(self, stream_id, provider, model, token_ceiling):
        self.started.append((stream_id, provider, model, token_ceiling))
        if token_ceiling > 12_000:
            raise ProviderRejected("Synthetic insufficient native allowance")


@pytest.mark.asyncio
async def test_large_cascade_tool_schema_cannot_reach_provider_under_insufficient_allowance():
    calls, budget, tools = [], Budget(), Tools()

    async def handle(request):
        calls.append(request)
        pytest.fail("Native reservation must reject before provider I/O")

    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(),
        settings(),
        Check(),
        budget,
        tools,
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(ProviderRejected, match="insufficient"):
        await pipeline.respond(None, (), question="Question")
    assert calls == [] and len(budget.started) == 1
    assert budget.started[0][-1] > len(json.dumps(tools.definitions()).encode())


@pytest.mark.asyncio
async def test_large_realtime_tool_schema_is_reserved_before_connect_or_prompt_publication():
    connections, budget = [], Budget()

    async def connector(url, headers):
        connections.append(url)
        return Socket()

    realtime = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), budget, Tools(), connector=connector
    )
    with pytest.raises(ProviderRejected, match="insufficient"):
        await realtime.begin_turn(())
    assert connections == [] and realtime.socket is None
    assert len(budget.started) == 1 and budget.started[0][-1] > 22_000


@pytest.mark.asyncio
async def test_unbounded_realtime_configuration_rejected_even_before_warm_connection():
    connections = []

    async def connector(url, headers):
        connections.append(url)
        return Socket()

    realtime = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), Tools(size=140_000), connector=connector
    )
    with pytest.raises(ProviderRejected, match="input bound"):
        await realtime._connect(())
    assert connections == []
