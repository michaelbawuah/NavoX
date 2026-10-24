import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from navox.core.settings import Settings
from navox.vox.inference_config import ProviderRoute, VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline, ProviderRejected
from navox.vox.web_tools import WEB_TOOL, PublicWebTools, cited_result
from navox.vox.worker import WorkerAccessLost, WorkerAuthority


def response():
    return {
        "status": "completed",
        "output": [
            {"type": "web_search_call", "status": "completed"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Confirmed fixture [source]",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "start_index": 18,
                                "end_index": 26,
                                "title": "Official fixtures",
                                "url": "https://example.org/fixtures",
                            }
                        ],
                    }
                ],
            },
        ],
        "usage": {"input_tokens": 42, "output_tokens": 20, "total_tokens": 62},
    }


class Check:
    authority = WorkerAuthority(uuid4(), uuid4())
    active = True

    async def __call__(self):
        if not self.active:
            raise WorkerAccessLost("Synthetic revoked owner")
        return self.authority

    @asynccontextmanager
    async def factory(self):
        yield object()


class Usage:
    def __init__(self):
        self.begun = []
        self.finished = []

    async def begin(self, *args):
        self.begun.append(args)

    async def finish(self, *args):
        self.finished.append(args)


def setup(monkeypatch, handler):
    from navox.vox import web_tools

    async def owner(*_args):
        return SimpleNamespace(timezone="America/New_York")

    monkeypatch.setattr(web_tools.student, "owner", owner)
    check, usage = Check(), Usage()
    config = VoxInferenceConfig(web_search=ProviderRoute(model="gpt-5.6-luna"))
    settings = Settings(_env_file=None, openai_api_key="synthetic-test-key")
    pipeline = OpenAICompatiblePipeline(
        config, settings, check, usage, transport=httpx.MockTransport(handler)
    )
    published = []

    async def publish(value):
        published.append(value)

    tool = PublicWebTools(check, config, settings, usage, publish, pipeline=pipeline)
    return tool, check, usage, published


def test_search_disabled_by_default_and_endpoint_must_be_allowlisted():
    assert VoxInferenceConfig().web_search is None
    with pytest.raises(ValueError):
        VoxInferenceConfig(
            web_search=ProviderRoute(model="test", base_url="https://unapproved.example/v1")
        )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.org",
        "https://127.0.0.1/",
        "https://user:pass@example.org/",
        "https://box.internal/",
    ],
)
def test_invalid_citations_never_reach_browser(url):
    data = response()
    data["output"][1]["content"][0]["annotations"][0]["url"] = url
    with pytest.raises(ProviderRejected):
        cited_result(data, "2026-10-24T00:00:00+00:00")


@pytest.mark.parametrize("fault", ["no_search", "no_citations", "incomplete", "bad_offsets"])
def test_answer_requires_completed_search_and_bound_citations(fault):
    data = response()
    if fault == "no_search":
        data["output"].pop(0)
    elif fault == "no_citations":
        data["output"][1]["content"][0]["annotations"] = []
    elif fault == "incomplete":
        data["status"] = "incomplete"
    else:
        data["output"][1]["content"][0]["annotations"][0]["end_index"] = 999
    with pytest.raises(ProviderRejected):
        cited_result(data, "2026-10-24T00:00:00+00:00")


@pytest.mark.asyncio
async def test_real_http_adapter_searches_only_public_query_with_citations_and_usage(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response())

    tool, _, usage, published = setup(monkeypatch, handler)
    result = await tool.execute(WEB_TOOL, {"query": "When is the next public fixture?"})
    assert result["status"] == "complete"
    assert [event["status"] for event in published] == ["searching", "complete"]
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/responses"
    assert payload["store"] is False
    assert payload["tool_choice"] == "required"
    assert payload["tools"] == [{"type": "web_search", "external_web_access": True}]
    assert set(json.loads(payload["input"])) == {"question", "current_time", "user_timezone"}
    assert usage.begun[0][1:3] == ("openai-web-search", "gpt-5.6-luna")
    assert usage.finished[0][1]["total_tokens"] == 62
    assert usage.finished[0][2] is True


@pytest.mark.asyncio
async def test_failed_search_returns_unavailable_without_guessing_or_claiming_it_was_free(
    monkeypatch,
):
    tool, _, usage, published = setup(monkeypatch, lambda _: httpx.Response(503))
    result = await tool.execute(WEB_TOOL, {"query": "Next public fixture"})
    assert result["status"] == "unavailable"
    assert [event["status"] for event in published] == ["searching", "unavailable"]
    assert usage.finished[0][1:] == ({}, False)


@pytest.mark.asyncio
async def test_revoked_owner_never_receives_completed_search(monkeypatch):
    def handler(_):
        check.active = False
        return httpx.Response(200, json=response())

    tool, check, _, published = setup(monkeypatch, handler)
    with pytest.raises(WorkerAccessLost):
        await tool.execute(WEB_TOOL, {"query": "Next public fixture"})
    assert [event["status"] for event in published] == ["searching"]
