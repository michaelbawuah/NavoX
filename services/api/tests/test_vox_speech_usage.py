"""Speech usage comes from a completed provider stream, never estimated tokens."""

import base64
import json

import httpx
import pytest
from test_vox_calls import vox_env as _shared_vox_env
from test_vox_inference import Check, Hooks, settings

from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline, ProviderRejected
from navox.vox.inference_speech import decode_speech_events
from navox.vox.worker import WorkerAccessLost
from navox.vox.worker_usage import normalize_usage

vox_env = _shared_vox_env


def event(value):
    return "data: " + json.dumps(value) + "\n\n"


def speech(*, usage=None, terminal=True):
    body = ": keepalive\n\nevent: speech.audio.delta\n"
    body += event({"type": "speech.audio.delta", "audio": base64.b64encode(b"mp3-one").decode()})
    body += event({"type": "speech.audio.delta", "audio": base64.b64encode(b"-two").decode()})
    if terminal:
        body += event({"type": "speech.audio.done", "usage": usage})
        body += "data: [DONE]\n\n"
    return body.encode()


@pytest.mark.asyncio
async def test_speech_sse_collects_audio_and_settles_exact_modality_usage():
    hooks = Hooks()
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        if request.url.path.endswith("completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Ready."}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "prompt_tokens_details": {"cached_tokens": 2},
                    },
                },
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            content=speech(usage={"input_tokens": 6, "output_tokens": 55, "total_tokens": 61}),
        )

    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(tts_stream_format="sse"),
        settings(),
        Check(),
        hooks,
        transport=httpx.MockTransport(handle),
    )
    reply = await pipeline.respond(None, (), question="Say ready.")
    assert reply.audio == b"mp3-one-two"
    assert seen[-1]["stream_format"] == "sse"
    llm, complete = normalize_usage(hooks.finished[0][1])
    assert complete and (llm.input_text, llm.output_text, llm.cached_text) == (10, 3, 2)
    tts, complete = normalize_usage(hooks.finished[1][1])
    assert complete and (tts.input_text, tts.output_audio, tts.total_tokens) == (6, 55, 61)
    assert all(item[2] for item in hooks.finished)


@pytest.mark.parametrize(
    "body",
    [
        speech(terminal=False),
        b"data: [DONE]\n\n",
        event({"type": "speech.audio.delta", "audio": "%%%"}).encode(),
        event({"type": "speech.audio.delta", "audio": []}).encode(),
        event({"type": "error", "message": "private diagnostic"}).encode(),
        speech() + event({"type": "speech.audio.done"}).encode(),
        speech() + event({"type": "speech.audio.delta", "audio": "YQ=="}).encode(),
        b"data: []\n\n",
        b"data: {broken}\n\n",
        b"data: \xff\n\n",
    ],
)
def test_malformed_or_incomplete_speech_is_rejected(body):
    with pytest.raises(ValueError):
        decode_speech_events(body)


def test_speech_accepts_crlf_and_multiline_data():
    body = speech(usage={"input_tokens": 2, "output_tokens": 3})
    body = body.replace(b'data: {"type":', b'data: {\ndata: "type":')
    audio, usage = decode_speech_events(body.replace(b"\n", b"\r\n"))
    assert audio == b"mp3-one-two"
    assert usage == {"input_tokens": 2, "output_tokens": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,content_type,raises",
    [
        (speech(terminal=False), "text/event-stream", True),
        (speech(), "audio/mpeg", True),
        (speech(), "text/event-stream", False),
        (speech(usage={"input_tokens": True, "output_tokens": -1}), "text/event-stream", False),
    ],
)
async def test_missing_usage_keeps_reservation_and_invalid_audio_is_not_returned(
    body, content_type, raises
):
    hooks = Hooks()
    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(tts_stream_format="sse"),
        settings(),
        Check(),
        hooks,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": content_type}, content=body)
        ),
    )

    async def run():
        return await pipeline._request(
            pipeline.config.tts,
            "/audio/speech",
            input_bound=100,
            payload={"stream_format": "sse"},
        )

    if raises:
        with pytest.raises(ProviderRejected):
            await run()
    else:
        audio, _ = await run()
        assert audio == b"mp3-one-two"
    assert hooks.finished[-1][1:] == ({}, False)


def test_encoded_and_decoded_speech_bounds(monkeypatch):
    from navox.vox import inference_speech

    monkeypatch.setattr(inference_speech, "MAX_WIRE_BYTES", 10)
    with pytest.raises(ValueError):
        decode_speech_events(speech())
    monkeypatch.setattr(inference_speech, "MAX_WIRE_BYTES", 10000)
    monkeypatch.setattr(inference_speech, "MAX_AUDIO_BYTES", 5)
    with pytest.raises(ValueError):
        decode_speech_events(speech())


@pytest.mark.asyncio
async def test_revocation_during_speech_stops_read_and_leaves_usage_uncertain():
    check, hooks = Check(), Hooks()

    class RevokedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield speech(terminal=False)
            check.active = False
            yield event(
                {"type": "speech.audio.done", "usage": {"input_tokens": 1, "output_tokens": 1}}
            ).encode()

    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(tts_stream_format="sse"),
        settings(),
        check,
        hooks,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=RevokedStream()
            )
        ),
    )
    with pytest.raises(WorkerAccessLost):
        await pipeline._request(
            pipeline.config.tts, "/audio/speech", input_bound=100, payload={"stream_format": "sse"}
        )
    assert hooks.finished[-1][1:] == ({}, False)


@pytest.mark.parametrize("seconds,expected", [(0, 0), (2.5, 2500), (0.0001, 1), (60, 60000)])
def test_duration_billing_preserves_reported_time_without_inventing_tokens(seconds, expected):
    from decimal import Decimal

    from navox.vox.inference_http import usage_counts
    from navox.vox.usage import estimate
    from navox.vox.usage_schemas import ModelPricing

    raw = usage_counts({"type": "duration", "seconds": seconds})
    counters, complete = normalize_usage(raw)
    assert complete and counters.billed_audio_ms == expected
    assert counters.total_tokens == 0
    rates = ModelPricing(
        provider="openai",
        model="gpt-transcribe",
        version="test",
        audio_usd_per_minute=Decimal("0.0045"),
    )
    assert estimate(counters, rates.model_dump(mode="json"), "provider") == (
        Decimal(expected) * Decimal("0.0045") / 60000
    )
    if expected:
        assert estimate(counters, None, "provider") is None
        assert (
            estimate(
                counters,
                {
                    "provider": "openai",
                    "model": "gpt-transcribe",
                    "version": "missing-duration-rate",
                },
                "provider",
            )
            is None
        )


@pytest.mark.parametrize("seconds", [True, -1, float("nan"), float("inf"), "2", None, 3601])
def test_invalid_duration_never_marks_usage_complete(seconds):
    from navox.vox.inference_http import usage_counts

    assert usage_counts({"type": "duration", "seconds": seconds}) == {}


def test_mixed_billing_units_are_rejected():
    from navox.vox.usage import UsageError

    with pytest.raises(UsageError):
        normalize_usage({"billed_audio_ms": 1000, "input_tokens": 9})


@pytest.mark.asyncio
async def test_duration_releases_hold_and_is_in_call_and_daily_cost(vox_env):
    from decimal import Decimal
    from uuid import UUID, uuid4

    from test_vox_calls import start

    from navox.vox import usage
    from navox.vox.usage_schemas import ModelPricing, UsageCounters, UsagePolicy, UsageUpdate

    env = vox_env
    call, _ = await start(env)
    stream = uuid4()
    async with env.factory() as db:
        await usage.update_policy(
            db,
            UsagePolicy(
                model_pricing=[
                    ModelPricing(
                        provider="openai",
                        model="gpt-transcribe",
                        version="test",
                        audio_usd_per_minute=Decimal("0.0045"),
                    )
                ]
            ),
            expected_revision=0,
        )
        await usage.admit_provider_stream(
            db,
            env.user_id,
            env.workspace_id,
            UUID(call["id"]),
            stream_id=stream,
            provider="openai",
            model="gpt-transcribe",
            token_ceiling=4000,
        )
        await usage.apply_usage_update(
            db,
            stream,
            UsageUpdate(
                event_id=uuid4(), sequence=1, cumulative=UsageCounters(billed_audio_ms=2500)
            ),
        )
        await usage.finish_usage_stream(db, stream, usage_complete=True)
        await db.commit()
    async with env.factory() as db:
        status = await usage.budget_status(db, env.user_id, env.workspace_id)
        assert status.daily.reserved_tokens == 0
        assert status.daily.tokens == 0
        assert status.daily.estimated_cost_usd == Decimal("0.0001875")
        total = await usage.call_totals(db, env.user_id, env.workspace_id, UUID(call["id"]))
        assert total.estimated_cost_usd == Decimal("0.0001875")


def test_standard_context_price_does_not_underprice_larger_requests():
    from decimal import Decimal

    from navox.vox.usage import estimate
    from navox.vox.usage_schemas import ModelPricing, UsageCounters

    rates = ModelPricing(
        provider="openai",
        model="gpt-5.6-luna",
        version="test",
        input_text=Decimal("0.20"),
        max_input_tokens=272000,
    )
    assert estimate(
        UsageCounters(input_text=272000), rates.model_dump(mode="json"), "provider"
    ) == Decimal("0.0544")
    assert (
        estimate(UsageCounters(input_text=272001), rates.model_dump(mode="json"), "provider")
        is None
    )
