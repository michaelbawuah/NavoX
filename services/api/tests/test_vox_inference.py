"""Synthetic transport proofs only; these tests do not qualify a live voice call."""

import asyncio
import base64
import json
import struct
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from navox.core.settings import Settings
from navox.vox.inference_config import (
    InferenceConfigurationError,
    VoxInferenceConfig,
    load_inference_config,
)
from navox.vox.inference_http import OpenAICompatiblePipeline, ProviderUnavailable
from navox.vox.inference_realtime import NoRedirectConnect, OpenAIRealtimeAdapter
from navox.vox.inference_worker import ConfiguredVoiceWorker
from navox.vox.session_context import (
    CallContextKey,
    ContextSnapshot,
    InMemoryContextStore,
    VoxCallContext,
)
from navox.vox.worker import MemoryTurn, WorkerAccessLost, WorkerAssignment, WorkerAuthority


class Hooks:
    def __init__(self):
        self.started = []
        self.finished = []

    async def begin(self, stream_id, provider, model, token_ceiling):
        self.started.append((stream_id, provider, model, token_ceiling))

    async def finish(self, stream_id, usage, complete):
        self.finished.append((stream_id, usage, complete))


class Check:
    def __init__(self):
        self.authority = WorkerAuthority(uuid4(), uuid4())
        self.active = True

    async def __call__(self):
        if not self.active:
            raise WorkerAccessLost("Ended")
        return self.authority


class Socket:
    def __init__(self):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.index = 0

    async def send(self, message):
        event = json.loads(message)
        self.sent.append(event)
        kind = event["type"]
        if kind == "session.update":
            self.events.put_nowait({"type": "session.updated"})
        elif kind == "input_audio_buffer.commit":
            self.index += 1
            item = "question_" + str(self.index)
            self.events.put_nowait({"type": "input_audio_buffer.committed", "item_id": item})
            self.events.put_nowait(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": item,
                    "transcript": "Question " + str(self.index),
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            )
        elif kind == "response.create":
            response_id = "response_" + str(self.index)
            self.events.put_nowait(
                {
                    "type": "response.created",
                    "response": {"id": response_id, "metadata": event["response"]["metadata"]},
                }
            )
            self.events.put_nowait(
                {
                    "type": "response.output_audio.delta",
                    "response_id": response_id,
                    "delta": base64.b64encode(struct.pack("<h", 2000) * 480).decode(),
                }
            )
            self.events.put_nowait(
                {
                    "type": "response.output_audio_transcript.done",
                    "response_id": response_id,
                    "transcript": "A useful answer.",
                }
            )
            self.events.put_nowait(
                {
                    "type": "response.done",
                    "response": {
                        "id": response_id,
                        "status": "completed",
                        "usage": {"input_tokens": 12, "output_tokens": 7},
                    },
                }
            )

    async def recv(self):
        return json.dumps(await self.events.get())

    async def close(self):
        self.closed = True


def settings():
    return Settings(openai_api_key=SecretStr("synthetic-not-a-real-key"))


@pytest.mark.asyncio
async def test_one_persistent_socket_for_multiple_voice_turns_and_no_automatic_vad():
    socket = Socket()
    opened = []

    async def connect(url, headers):
        opened.append(url)
        assert headers["Authorization"].startswith("Bearer ")
        return socket

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), connector=connect
    )
    emitted = []
    for _ in range(2):
        await adapter.begin_turn(())
        for _ in range(10):
            await adapter.append_audio(struct.pack("<h", 2000) * 320)
        result = await adapter.respond(lambda value: append(emitted, value))
        assert result.answer == "A useful answer."
    assert len(opened) == 1
    assert opened[0].endswith("/v1/realtime?model=gpt-realtime-2.1")
    session = socket.sent[0]["session"]
    assert session["audio"]["input"]["turn_detection"] is None
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert session["audio"]["output"]["format"]["rate"] == 24000
    assert session["truncation"] == "disabled"
    assert len(emitted) == 2
    await adapter.close()
    assert socket.closed


async def append(target, value):
    target.append(value)


@pytest.mark.asyncio
async def test_quiet_rollover_restores_all_heard_turns_without_repeat_greeting():
    sockets = []
    now = [0.0]

    async def connect(url, headers):
        result = Socket()
        sockets.append(result)
        return result

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), connector=connect, clock=lambda: now[0]
    )
    history = tuple(MemoryTurn("Q" + str(i), "A" + str(i)) for i in range(9))
    await adapter.begin_turn(history)
    await adapter.interrupt()
    now[0] = 3400
    await adapter.rollover_if_quiet(history)
    await adapter.begin_turn(history)
    seeded = [e for e in sockets[-1].sent if e["type"] == "conversation.item.create"]
    assert len(seeded) == 18
    assert seeded[0]["item"]["content"][0]["text"] == "Q0"
    assert seeded[-1]["item"]["content"][0]["text"] == "A8"
    assert not any(e["type"] == "response.create" for e in sockets[-1].sent)
    await adapter.close()


@pytest.mark.asyncio
async def test_call_context_isolation_stale_cas_and_no_four_pair_truncation():
    check = Check()
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 1)
    store = InMemoryContextStore()
    first = await VoxCallContext.load(key, store)
    stale = await VoxCallContext.load(key, store)
    epoch = uuid4()
    first.begin_epoch(epoch)
    stale.begin_epoch(epoch)
    for i in range(9):
        await first.commit(epoch, "question" + str(i), "answer" + str(i))
    assert len(first.history(epoch)) == 9
    with pytest.raises(WorkerAccessLost):
        await stale.commit(epoch, "stale", "must not overwrite")
    other = replace(key, user_id=uuid4())
    assert await store.load(other) == ContextSnapshot()
    first.begin_epoch(uuid4())
    with pytest.raises(WorkerAccessLost):
        first.history(epoch)
    first.close()


def test_operator_configuration_no_remote_paths_extra_values_symlinks_or_world_writes(tmp_path):
    assert load_inference_config(Settings()) is None
    path = tmp_path / "vox.json"
    path.write_text(VoxInferenceConfig().model_dump_json())
    path.chmod(0o600)
    configured = Settings(student_vox_inference_config_path=str(path))
    assert load_inference_config(configured).realtime.model == "gpt-realtime-2.1"
    path.chmod(0o666)
    with pytest.raises(InferenceConfigurationError):
        load_inference_config(configured)
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(InferenceConfigurationError):
        load_inference_config(Settings(student_vox_inference_config_path=str(link)))
    for patch in (
        {"realtime": {"model": "gpt-realtime-2.1", "base_url": "https://unknown.invalid/v1"}},
        {"allowed_origins": ["http://untrusted.example"]},
        {"api_key": "must-not-accept-an-inline-key"},
    ):
        with pytest.raises(ValueError):
            VoxInferenceConfig.model_validate(patch)


def test_websocket_redirects_are_rejected():
    error = RuntimeError("synthetic redirect")
    assert NoRedirectConnect.process_redirect(None, error) is error


@pytest.mark.asyncio
async def test_real_http_pipeline_uses_configured_models_voice_and_bounded_endpoints():
    seen = []

    async def handle(request):
        seen.append((request.url.path, request.content))
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(
                200,
                json={"text": "Explain gravity", "usage": {"input_tokens": 4, "output_tokens": 2}},
            )
        if request.url.path.endswith("completions"):
            body = json.loads(request.content)
            assert body["model"] == "gpt-5.6-luna"
            assert len(body["messages"]) == 12  # system + five complete pairs + question
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Gravity attracts."}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 4},
                },
            )
        body = json.loads(request.content)
        assert body["voice"] == "marin"
        return httpx.Response(200, content=b"synthetic-mp3")

    hooks = Hooks()
    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(), settings(), Check(), hooks, transport=httpx.MockTransport(handle)
    )
    history = tuple(MemoryTurn("Q", "A") for _ in range(5))
    reply = await pipeline.respond(b"synthetic-wave", history)
    assert reply.audio == b"synthetic-mp3"
    assert [p for p, _ in seen] == [
        "/v1/audio/transcriptions",
        "/v1/chat/completions",
        "/v1/audio/speech",
    ]
    assert len(hooks.started) == 3
    assert hooks.finished[-1][2] is False  # TTS usage unknown never reported as free.


class Output:
    def __init__(self):
        self.played = []
        self.cleared = 0

    async def clear(self):
        self.cleared += 1

    async def play(self, value, check):
        await check()
        self.played.append(value)

    async def play_pcm(self, value, check):
        await check()
        self.played.append(value)

    async def finish_pcm(self, check):
        await check()


class FailingRealtime:
    tool_started = False

    def __init__(self, partial):
        self.output_started = partial
        self.closed = False

    async def respond(self, emit, text=None):
        raise ProviderUnavailable("synthetic transport fault")

    async def close(self):
        self.closed = True

    async def interrupt(self, **kwargs):
        self.closed = True


class Pipeline:
    def __init__(self):
        self.calls = 0

    async def respond(self, audio, history, question=None):
        from navox.vox.inference_http import PipelineReply

        self.calls += 1
        return PipelineReply("Question", "Answer.", b"fallback")


@pytest.mark.asyncio
@pytest.mark.parametrize("partial,expected", [(False, 1), (True, 0)])
async def test_fallback_only_before_any_output_and_never_replays_a_half_spoken_answer(
    partial, expected
):
    check, output = Check(), Output()
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0)
    context = await VoxCallContext.load(key, InMemoryContextStore())
    connection = uuid4()
    assignment = WorkerAssignment(
        connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
    )
    pipeline = Pipeline()
    worker = ConfiguredVoiceWorker(
        assignment, check, output, VoxInferenceConfig(), context, FailingRealtime(partial), pipeline
    )
    await worker._respond(b"pcm", 0)
    assert pipeline.calls == expected
    assert len(context.history(connection)) == expected
    assert worker.failed is False
    await worker.close()


@pytest.mark.asyncio
async def test_revoked_authority_cannot_enter_either_provider_path():
    check, output = Check(), Output()
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0)
    context = await VoxCallContext.load(key, InMemoryContextStore())
    connection = uuid4()
    worker = ConfiguredVoiceWorker(
        WorkerAssignment(
            connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
        ),
        check,
        output,
        VoxInferenceConfig(),
        context,
        FailingRealtime(False),
        Pipeline(),
    )
    check.active = False
    await worker._respond(b"pcm", 0)
    assert worker.pipeline.calls == 0
    assert worker.failed
    await worker.close()


class Tools:
    def __init__(self):
        self.calls = []

    def definitions(self):
        return [
            {
                "type": "function",
                "name": "get_student_schedule",
                "description": "Read schedule",
                "parameters": {"type": "object"},
            }
        ]

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return {"status": "ok", "classes": ["Physics"]}


class ToolSocket(Socket):
    def __init__(self):
        super().__init__()
        self.responses = 0

    async def send(self, message):
        event = json.loads(message)
        if event["type"] != "response.create" or self.responses:
            if event["type"] == "response.create":
                self.responses += 1
            return await super().send(message)
        self.responses += 1
        self.sent.append(event)
        response = "tool-response"
        for output in [
            {
                "type": "response.created",
                "response": {"id": response, "metadata": event["response"]["metadata"]},
            },
            {
                "type": "response.output_audio.delta",
                "response_id": response,
                "delta": base64.b64encode(b"\0\0" * 480).decode(),
            },
            {
                "type": "response.output_audio_transcript.done",
                "response_id": response,
                "transcript": "Let me check.",
            },
            {
                "type": "response.output_item.done",
                "response_id": response,
                "item": {
                    "type": "function_call",
                    "call_id": "schedule-call",
                    "name": "get_student_schedule",
                    "arguments": '{"mode":"next","date":null}',
                },
            },
            {
                "type": "response.done",
                "response": {
                    "id": response,
                    "status": "completed",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                },
            },
        ]:
            self.events.put_nowait(output)


@pytest.mark.asyncio
async def test_audible_prelude_then_verified_schedule_tool_then_final_answer_same_socket():
    socket, tools, emitted = ToolSocket(), Tools(), []

    async def connect(url, headers):
        return socket

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), tools, connector=connect
    )
    await adapter.begin_turn(())
    for _ in range(10):
        await adapter.append_audio(struct.pack("<h", 2000) * 320)
    turn = await adapter.respond(lambda value: append(emitted, value))
    assert tools.calls == [("get_student_schedule", {"mode": "next", "date": None})]
    assert turn.answer == "Let me check. A useful answer."
    assert len(emitted) == 2
    assert adapter.tool_started
    assert adapter._context_ceiling < 3000  # actual usage, not fixed 4000/turn growth
    await adapter.close()


class InterruptedSocket(Socket):
    async def send(self, message):
        event = json.loads(message)
        if event["type"] == "response.create":
            self.sent.append(event)
            for output in [
                {
                    "type": "response.created",
                    "response": {"id": "r1", "metadata": event["response"]["metadata"]},
                },
                {
                    "type": "response.output_audio.delta",
                    "response_id": "r1",
                    "item_id": "a1",
                    "delta": base64.b64encode(b"\0\0" * 480).decode(),
                },
            ]:
                self.events.put_nowait(output)
        elif event["type"] == "conversation.item.truncate":
            self.sent.append(event)
            self.events.put_nowait({"type": "conversation.item.truncated", "item_id": "a1"})
        elif event["type"] == "conversation.item.retrieve":
            self.sent.append(event)
            self.events.put_nowait(
                {
                    "type": "conversation.item.retrieved",
                    "item": {
                        "id": "a1",
                        "content": [
                            {"type": "output_audio", "transcript": "The first option is Physics."}
                        ],
                    },
                }
            )
        else:
            await super().send(message)


@pytest.mark.asyncio
async def test_barge_in_truncates_to_played_offset_retains_question_and_exact_heard_prefix():
    socket = InterruptedSocket()

    async def connect(url, headers):
        return socket

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), connector=connect
    )
    await adapter.begin_turn(())
    for _ in range(10):
        await adapter.append_audio(struct.pack("<h", 2000) * 320)
    reached = asyncio.Event()

    async def emit(value):
        reached.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(adapter.respond(emit))
    await asyncio.wait_for(reached.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    partial = await adapter.interrupt(played_audio_ms=20)
    assert partial == MemoryTurn("Question 1", "The first option is Physics.")
    assert not socket.closed
    truncation = [event for event in socket.sent if event["type"] == "conversation.item.truncate"]
    assert truncation == [
        {
            "type": "conversation.item.truncate",
            "item_id": "a1",
            "content_index": 0,
            "audio_end_ms": 20,
        }
    ]
    assert not any("unheard" in str(event) for event in socket.sent)
    await adapter.close()


@pytest.mark.asyncio
async def test_proactive_quiet_rollover_keeps_first_new_speech_on_old_socket_during_warmup():
    sockets, now, warm_started, release = [], [0.0], asyncio.Event(), asyncio.Event()

    async def connect(url, headers):
        if sockets:
            warm_started.set()
            await release.wait()
        result = Socket()
        sockets.append(result)
        return result

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), connector=connect, clock=lambda: now[0]
    )
    await adapter._connect(())
    now[0] = 3400
    replacement = asyncio.create_task(adapter.rollover_if_quiet(()))
    await asyncio.wait_for(warm_started.wait(), 1)
    # New speech must use the still-warm original connection without waiting for DNS/TLS.
    await asyncio.wait_for(adapter.begin_turn(()), 0.1)
    await adapter.append_audio(struct.pack("<h", 2000) * 320)
    assert any(event["type"] == "input_audio_buffer.append" for event in sockets[0].sent)
    release.set()
    await replacement
    assert adapter.socket is sockets[0]
    assert sockets[1].closed  # Warm replacement cannot displace an active utterance.
    await adapter.interrupt()
    await adapter.rollover_if_quiet(())
    assert adapter.socket is sockets[2]
    assert sockets[0].closed
    assert not any(event["type"] == "response.create" for event in sockets[2].sent)
    await adapter.close()


def test_nested_cache_modalities_and_question_limit_are_not_invented_or_truncated():
    from navox.vox.inference_http import ProviderRejected, bounded_text, usage_counts

    counts = usage_counts(
        {
            "input_tokens": 40,
            "output_tokens": 8,
            "input_token_details": {
                "cached_tokens": 20,
                "cached_tokens_details": {"text_tokens": 12, "audio_tokens": 8},
            },
        }
    )
    assert counts["input_cached_text_tokens"] == 12
    assert counts["input_cached_audio_tokens"] == 8
    assert usage_counts({"input_tokens": True}) == {}
    with pytest.raises(ProviderRejected):
        bounded_text("q" * 4001)


class RecordingRealtime(FailingRealtime):
    def __init__(self):
        super().__init__(False)
        self.frames = []

    async def begin_turn(self, history):
        pass

    async def append_audio(self, frame):
        self.frames.append(frame)


@pytest.mark.asyncio
async def test_local_vad_sends_bounded_preroll_and_hangover_but_no_continuous_silence():
    check, output, realtime = Check(), Output(), RecordingRealtime()
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0)
    context = await VoxCallContext.load(key, InMemoryContextStore())
    connection = uuid4()
    worker = ConfiguredVoiceWorker(
        WorkerAssignment(
            connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
        ),
        check,
        output,
        VoxInferenceConfig(),
        context,
        realtime,
        Pipeline(),
    )
    quiet = b"\0\0" * 320
    voiced = struct.pack("<h", 2000) * 320
    for _ in range(100):
        await worker.receive(worker.assignment.client_identity, quiet)
    assert realtime.frames == []
    await worker.receive(worker.assignment.client_identity, voiced)
    for _ in range(3):
        await worker.receive(worker.assignment.client_identity, quiet)
    assert realtime.frames == [quiet] * 10 + [voiced] + [quiet] * 3
    await worker.close()


@pytest.mark.asyncio
async def test_realtime_pcm_bridge_resamples_24k_to_48k_and_reports_only_actual_frames():
    from navox.vox.worker_livekit import LiveKitAudioOutput

    class Source:
        queued_duration = 0

        def __init__(self):
            self.samples = 0

        def clear_queue(self):
            pass

        async def capture_frame(self, frame):
            assert frame.sample_rate == 48000
            assert frame.num_channels == 1
            self.samples += frame.samples_per_channel

        async def wait_for_playout(self):
            pass

    class Usage:
        def __init__(self):
            self.durations = []

        async def note_audio(self, duration, *, direction="input"):
            self.durations.append(duration)

    source, usage = Source(), Usage()
    output = LiveKitAudioOutput(source)
    output.usage = usage

    async def check():
        return None

    await output.play_pcm(struct.pack("<h", 1500) * 2400, check)
    await output.finish_pcm(check)
    assert source.samples == 4800
    assert sum(usage.durations) == 100
    assert max(usage.durations) <= 20


@pytest.mark.asyncio
async def test_invalid_realtime_or_cancelled_turn_never_silently_falls_back():
    from navox.vox.inference_http import ProviderRejected

    class Rejected(FailingRealtime):
        async def respond(self, emit, text=None):
            raise ProviderRejected("synthetic quota/config denial")

    check, output = Check(), Output()
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0)
    context = await VoxCallContext.load(key, InMemoryContextStore())
    connection = uuid4()
    worker = ConfiguredVoiceWorker(
        WorkerAssignment(
            connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
        ),
        check,
        output,
        VoxInferenceConfig(),
        context,
        Rejected(False),
        Pipeline(),
    )
    await worker._respond(b"pcm", 0)
    assert worker.pipeline.calls == 0
    assert worker.failed
    assert context.history(connection) == ()
    await worker.close()


@pytest.mark.asyncio
async def test_worker_followup_receives_interrupted_question_and_only_verified_heard_prefix():
    class Partial(FailingRealtime):
        async def interrupt(self, **kwargs):
            return MemoryTurn("Which two classes?", "The first is Physics.")

    class FollowupPipeline(Pipeline):
        async def respond(self, audio, history, question=None):
            from navox.vox.inference_http import PipelineReply

            assert question == "What about the second one?"
            assert history == (MemoryTurn("Which two classes?", "The first is Physics."),)
            return PipelineReply(question, "The second needs a fresh schedule lookup.", b"audio")

    check, output = Check(), Output()
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0)
    context = await VoxCallContext.load(key, InMemoryContextStore())
    connection = uuid4()
    worker = ConfiguredVoiceWorker(
        WorkerAssignment(
            connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
        ),
        check,
        output,
        VoxInferenceConfig(),
        context,
        Partial(False),
        FollowupPipeline(),
    )
    worker._task = asyncio.create_task(asyncio.Event().wait())
    await worker.interrupt()
    worker._fallback_until = float("inf")
    await worker._respond(None, worker._epoch, "What about the second one?")
    assert len(context.history(connection)) == 2
    assert worker.failed is False
    await worker.close()


@pytest.mark.asyncio
async def test_call_end_closes_provider_socket_even_when_barge_in_keeps_it_open():
    class Healthy(FailingRealtime):
        async def interrupt(self, **kwargs):
            return None

    check, output, realtime = Check(), Output(), Healthy(False)
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0)
    context = await VoxCallContext.load(key, InMemoryContextStore())
    connection = uuid4()
    worker = ConfiguredVoiceWorker(
        WorkerAssignment(
            connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
        ),
        check,
        output,
        VoxInferenceConfig(),
        context,
        realtime,
        Pipeline(),
    )
    await worker.close()
    assert realtime.closed
    assert await context.store.load(key) == ContextSnapshot()
