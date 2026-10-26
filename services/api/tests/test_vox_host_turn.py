"""Host input/playout boundaries, using synthetic providers and audio only."""

import asyncio
import json
import struct
from uuid import uuid4

import httpx
import pytest
from test_vox_inference import Check, FailingRealtime, Hooks, Output, Socket, settings

from navox.student import service as schedule
from navox.vox.calendar_approval import CalendarApprovalCoordinator, PendingCalendarAction
from navox.vox.host_turn import CompositeHostTurnHooks, HostReply, HostReplyRequired, HostTurnHooks
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline, PipelineReply
from navox.vox.inference_realtime import OpenAIRealtimeAdapter
from navox.vox.inference_worker import ConfiguredVoiceWorker
from navox.vox.session_context import CallContextKey, InMemoryContextStore, VoxCallContext
from navox.vox.worker import WorkerAssignment

READBACK = "Add Study meeting to My calendar on Monday at 3 PM, UTC?"


def action(calls, *, validate=None):
    async def fresh():
        calls.append("fresh")
        if validate:
            await validate()

    async def apply():
        calls.append("apply")
        return "The provider confirmed this exact event."

    return PendingCalendarAction("receipt:revision:digest", READBACK, fresh, apply)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "yes",
    [
        "yes",
        "Yeah!",
        "of course",
        "sure",
        "go ahead",
        "yes please",
        "Sure, go ahead.",
        "Of course — go ahead",
        "Okay, go ahead!",
    ],
)
async def test_exact_played_review_accepts_one_current_contextual_yes_only_once(yes):
    calls = []
    host = CalendarApprovalCoordinator()
    assert host.prepare(action(calls))
    assert (await host.after_tool()).answer == READBACK
    assert calls == []
    await host.played(READBACK)
    assert calls == ["fresh"]
    reply = await host.input(yes)
    assert "provider confirmed" in reply.answer
    assert calls == ["fresh", "fresh", "apply"]
    assert await host.input(yes) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply", ["yes but make it four", "yeah no", "sure, change the title", "yes it is sunny"]
)
async def test_refinements_and_unrelated_acknowledgements_never_apply(reply):
    calls = []
    host = CalendarApprovalCoordinator()
    host.prepare(action(calls))
    await host.played(READBACK)
    assert await host.input(reply) is None
    assert "apply" not in calls and not host.needs_input


@pytest.mark.asyncio
async def test_unplayed_interrupted_expired_and_changed_sources_cannot_approve():
    calls, now = [], [0.0]
    host = CalendarApprovalCoordinator(clock=lambda: now[0])
    host.prepare(action(calls))
    assert "no longer active" in (await host.input("yes")).answer
    host.prepare(action(calls))
    await host.played(READBACK)
    host.invalidate("interrupted")
    assert "no longer active" in (await host.input("yes")).answer
    host.prepare(action(calls))
    await host.played(READBACK)
    now[0] = 90
    assert "no longer active" in (await host.input("yes")).answer

    changed = [False]

    async def validate():
        if changed[0]:
            raise schedule.StudentError(409, "synthetic private source changed")

    host.prepare(action(calls, validate=validate))
    await host.played(READBACK)
    changed[0] = True
    assert "changed" in (await host.input("yes")).answer
    assert "apply" not in calls


@pytest.mark.asyncio
async def test_source_change_during_readback_keeps_next_yes_in_host_failure_path():
    calls = []

    async def changed():
        raise schedule.StudentError(409, "changed synthetic source")

    host = CalendarApprovalCoordinator()
    host.prepare(action(calls, validate=changed))
    await host.after_tool()
    await host.played(READBACK)
    assert host.needs_input
    assert "no longer active" in (await host.input("yes")).answer
    assert "apply" not in calls
    assert await host.input("Instead, find tomorrow's lunch options") is None
    assert not host.needs_input


@pytest.mark.asyncio
async def test_ambiguous_reply_gets_one_clarification_without_extending_ttl():
    calls, now = [], [0.0]
    host = CalendarApprovalCoordinator(clock=lambda: now[0])
    host.prepare(action(calls))
    await host.played(READBACK)
    now[0] = 80
    clarification = await host.input("maybe")
    assert "yes or no" in clarification.answer
    await host.played(clarification.answer)
    now[0] = 91
    assert "no longer active" in (await host.input("yes")).answer
    assert "apply" not in calls


class Immediate(HostTurnHooks):
    needs_input = True

    def __init__(self):
        self.questions = []

    async def input(self, question):
        self.questions.append(question)
        return HostReply(READBACK)


@pytest.mark.asyncio
async def test_realtime_host_uses_only_matching_current_transcription_before_model_response():
    socket, host = Socket(), Immediate()

    async def connect(url, headers):
        return socket

    realtime = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), connector=connect, host_hooks=host
    )
    await realtime.begin_turn(())
    for _ in range(10):
        await realtime.append_audio(struct.pack("<h", 2000) * 320)
    with pytest.raises(HostReplyRequired) as caught:
        await realtime.respond(lambda raw: asyncio.sleep(0))
    assert host.questions == ["Question 1"]
    assert caught.value.answer == READBACK
    assert str(caught.value) == "A host-owned reply is required"
    assert not any(event["type"] == "response.create" for event in socket.sent)
    await realtime.close()


@pytest.mark.asyncio
async def test_host_input_uses_existing_metered_tts_without_another_model_request():
    requests, host = [], Immediate()

    async def handle(request):
        requests.append((request.url.path, json.loads(request.content)))
        assert request.url.path == "/v1/audio/speech"
        return httpx.Response(200, content=b"synthetic-mp3")

    usage = Hooks()
    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(),
        settings(),
        Check(),
        usage,
        transport=httpx.MockTransport(handle),
        host_hooks=host,
    )
    reply = await pipeline.respond(None, (), question="yes")
    assert reply.answer == READBACK and host.questions == ["yes"]
    assert requests[0][1]["input"] == READBACK
    assert len(usage.started) == 1 and usage.finished[0][2] is False


async def worker_parts(realtime, pipeline, output, host, *, mode="realtime"):
    check, connection = Check(), uuid4()
    context = await VoxCallContext.load(
        CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0),
        InMemoryContextStore(),
    )
    worker = ConfiguredVoiceWorker(
        WorkerAssignment(
            connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
        ),
        check,
        output,
        VoxInferenceConfig(mode=mode),
        context,
        realtime,
        pipeline,
        host_hooks=host,
    )
    return worker


@pytest.mark.asyncio
async def test_notice_never_arms_approval_and_full_host_readback_must_finish():
    calls, playing, release = [], asyncio.Event(), asyncio.Event()
    host = CalendarApprovalCoordinator()
    host.prepare(action(calls))
    await host.after_tool()

    class Notice(HostTurnHooks):
        async def before_output(self):
            return "Private saved context may be used."

        async def notice_played(self, text):
            calls.append("notice_played")

        async def output_started(self):
            calls.append("response_exposure")

    class Realtime(FailingRealtime):
        async def respond(self, emit, text=None):
            raise HostReplyRequired("prepare it", READBACK)

    class Speech:
        async def speak(self, question, answer):
            calls.append(answer)
            return PipelineReply(question, answer, answer.encode())

    class Playback(Output):
        async def play(self, data, check):
            await check()
            if data == READBACK.encode():
                playing.set()
                await release.wait()
            calls.append("played:" + data.decode())

    worker = await worker_parts(
        Realtime(False), Speech(), Playback(), CompositeHostTurnHooks(host, Notice())
    )
    task = asyncio.create_task(worker._respond(b"pcm", 0))
    await asyncio.wait_for(playing.wait(), 1)
    assert host._armed_until is None
    assert calls.index("notice_played") < calls.index("response_exposure")
    release.set()
    await task
    assert host._armed_until is not None
    assert calls.index("played:" + READBACK) < calls.index("fresh")
    await worker.close()


@pytest.mark.asyncio
async def test_idle_authenticated_typed_answer_preserves_pending_review():
    calls, host = [], CalendarApprovalCoordinator()
    host.prepare(action(calls))
    await host.after_tool()
    await host.played(READBACK)

    async def handle(request):
        assert request.url.path == "/v1/audio/speech"
        return httpx.Response(200, content=b"synthetic-mp3")

    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(mode="cascade"),
        settings(),
        Check(),
        Hooks(),
        transport=httpx.MockTransport(handle),
        host_hooks=host,
    )
    worker = await worker_parts(FailingRealtime(False), pipeline, Output(), host, mode="cascade")
    await worker.text_turn("yes")
    await worker._task
    assert calls.count("apply") == 1 and not worker.failed
    await worker.close()
