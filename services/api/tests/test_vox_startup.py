"""Synthetic RTC startup proofs; no provider or actual microphone is contacted."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from navox.core.settings import Settings
from navox.vox import worker_livekit, worker_usage
from navox.vox.audio_input import AudioInputBuffer
from navox.vox.worker import WorkerAssignment, WorkerAuthority


@pytest.fixture
def startup(monkeypatch):
    epoch = uuid4()
    assignment = WorkerAssignment(epoch, "vx-" + epoch.hex, "vc-" + epoch.hex, "va-" + epoch.hex)
    metadata = json.dumps({"connection_id": str(epoch), "generation": 4})
    state = SimpleNamespace(
        events=[],
        handlers={},
        warm_started=asyncio.Event(),
        warm_release=asyncio.Event(),
        received=asyncio.Event(),
        published=asyncio.Event(),
        with_microphone=True,
        publish_error=None,
        worker_close_error=None,
        usage_close_error=None,
        pcm=b"\x01\x01" * 320,
    )
    authority = WorkerAuthority(uuid4(), uuid4())

    class Check:
        async def __call__(self):
            await asyncio.sleep(0)  # Real authority checks yield for their database read.
            return authority

        async def intent(self, **kwargs):
            return authority, SimpleNamespace(
                generation=4, reservation_id=uuid4(), status="confirmed"
            )

    @asynccontextmanager
    async def active_epoch(check, current_assignment):
        assert current_assignment == assignment
        yield check

    class Source:
        def __init__(self, *args, **kwargs):
            pass

        async def aclose(self):
            state.events.append("source_closed")

    class Input(AudioInputBuffer):
        async def consume(self, receive):
            state.events.append("consumer_started")
            try:
                await super().consume(receive)
            finally:
                state.events.append("consumer_stopped")

    class Usage:
        def __init__(self, *args, **kwargs):
            pass

        async def heartbeat(self):
            state.events.append("heartbeat_started")
            try:
                await asyncio.Event().wait()
            finally:
                state.events.append("heartbeat_stopped")

        async def close(self):
            state.events.append("usage_closed")
            if state.usage_close_error is not None:
                raise state.usage_close_error

    class Worker:
        failed = False
        _task = True

        def __init__(self, *args, **kwargs):
            pass

        async def warm(self):
            state.warm_started.set()
            await state.warm_release.wait()
            state.events.append("warmed")

        async def receive(self, identity, pcm):
            assert identity == assignment.client_identity
            assert pcm == state.pcm
            state.received.set()

        async def flush_idle(self):
            pass

        async def maintain(self):
            pass

        async def close(self):
            state.events.append("worker_closed")
            if state.worker_close_error is not None:
                raise state.worker_close_error

    class Stream:
        def __init__(self, *args, **kwargs):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                await asyncio.Event().wait()
            self.sent = True
            return SimpleNamespace(frame=SimpleNamespace(data=state.pcm))

        async def aclose(self):
            state.events.append("stream_closed")

    participant = SimpleNamespace(
        identity=assignment.client_identity, metadata=metadata, track_publications={}
    )
    track = SimpleNamespace(kind=worker_livekit.rtc.TrackKind.KIND_AUDIO)
    publication = SimpleNamespace(
        sid="owned-input",
        source=worker_livekit.rtc.TrackSource.SOURCE_MICROPHONE,
        kind=worker_livekit.rtc.TrackKind.KIND_AUDIO,
    )

    def set_subscribed(value):
        assert value is True
        state.events.append("input_subscribed")
        state.handlers["track_subscribed"](track, publication, participant)

    publication.set_subscribed = set_subscribed

    async def publish_track(*args, **kwargs):
        state.events.append("output_published")
        assert state.events[0] == "warmed"
        assert {"consumer_started", "heartbeat_started"} <= set(state.events)
        assert "watch_started" not in state.events
        assert "track_published" in state.handlers
        state.published.set()
        if state.publish_error:
            raise state.publish_error
        if state.with_microphone:
            # The client starts capture as soon as this output becomes observable.
            participant.track_publications[publication.sid] = publication
            state.handlers["track_published"](publication, participant)
            await asyncio.wait_for(state.received.wait(), 1)
        state.handlers["disconnected"]()

    async def disconnect():
        state.events.append("disconnected")

    room = SimpleNamespace(
        name=assignment.room_name,
        local_participant=SimpleNamespace(
            identity=assignment.agent_identity, metadata=metadata, publish_track=publish_track
        ),
        remote_participants={assignment.client_identity: participant},
        on=lambda event, callback: state.handlers.__setitem__(event, callback),
        off=lambda event, callback: state.handlers.pop(event),
        disconnect=disconnect,
    )
    ctx = SimpleNamespace(
        job=SimpleNamespace(
            metadata=json.dumps(asdict(assignment), default=str),
            room=SimpleNamespace(name=assignment.room_name),
            agent_name="navox-test",
        ),
        room=room,
        connect=AsyncMock(),
    )
    config = SimpleNamespace(max_context_characters=2048, model_dump_json=lambda: "{}")

    def cloud_config(current_settings):
        if current_settings is None:
            state.events.append("watch_started")

    monkeypatch.setattr(worker_livekit, "DatabaseAuthority", lambda *args: Check())
    monkeypatch.setattr(worker_livekit, "_active_epoch", active_epoch)
    monkeypatch.setattr(worker_livekit, "get_session_factory", lambda: None)
    monkeypatch.setattr(worker_livekit, "cloud_config", cloud_config)
    monkeypatch.setattr(worker_livekit, "get_settings", lambda: None)
    monkeypatch.setattr(worker_livekit, "load_inference_config", lambda settings: config)
    monkeypatch.setattr(worker_livekit, "host_speech_available", lambda *args: False)
    monkeypatch.setattr(worker_livekit, "VoxCallContext", SimpleNamespace(load=AsyncMock()))
    for name in (
        "DatabaseContextStore",
        "RecallDeliveryCoordinator",
        "MemoryReadTools",
        "CalendarApprovalCoordinator",
        "CompositeHostTurnHooks",
        "VoxReadTools",
        "PlanningTools",
        "CaptureTools",
        "ScheduleTools",
        "PublicWebTools",
        "MemoryTools",
        "MemorySaveTools",
        "MemoryLearningTools",
        "CalendarTools",
        "OpenAIRealtimeAdapter",
        "OpenAICompatiblePipeline",
    ):
        monkeypatch.setattr(
            worker_livekit, name, lambda *args, **kwargs: SimpleNamespace(context=None)
        )
    monkeypatch.setattr(worker_livekit, "ConfiguredVoiceWorker", Worker)
    monkeypatch.setattr(worker_livekit, "AudioInputBuffer", Input)
    monkeypatch.setattr(worker_usage, "WorkerUsageHooks", Usage)
    monkeypatch.setattr(worker_livekit.rtc, "AudioSource", Source)
    monkeypatch.setattr(worker_livekit.rtc, "AudioStream", Stream)
    monkeypatch.setattr(
        worker_livekit.rtc,
        "LocalAudioTrack",
        SimpleNamespace(create_audio_track=lambda *args: object()),
    )
    settings = Settings(
        _env_file=None, app_environment="test", student_vox_livekit_agent_name="navox-test"
    )
    return ctx, settings, state


@pytest.mark.asyncio
@pytest.mark.parametrize("with_microphone", [True, False])
async def test_output_readiness_waits_for_warm_and_input_handlers_not_a_client_mic(
    startup, with_microphone
):
    ctx, settings, state = startup
    state.with_microphone = with_microphone
    task = asyncio.create_task(worker_livekit.serve_job(ctx, settings))
    try:
        await asyncio.wait_for(state.warm_started.wait(), 1)
        assert not state.published.is_set()
        assert not state.handlers
        state.warm_release.set()
        await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert state.published.is_set()
    assert state.received.is_set() is with_microphone
    assert {"consumer_stopped", "heartbeat_stopped", "worker_closed", "usage_closed"} <= set(
        state.events
    )
    assert not state.handlers
    assert state.events[-2:] == ["source_closed", "disconnected"]


@pytest.mark.asyncio
async def test_output_publish_failure_stops_already_started_input_and_usage(startup):
    ctx, settings, state = startup
    state.publish_error = RuntimeError("synthetic publication failure")
    state.warm_release.set()
    with pytest.raises(RuntimeError, match="synthetic publication failure"):
        await asyncio.wait_for(worker_livekit.serve_job(ctx, settings), 2)
    assert {"consumer_stopped", "heartbeat_stopped", "worker_closed", "usage_closed"} <= set(
        state.events
    )
    assert not state.handlers
    assert not state.received.is_set()
    assert "watch_started" not in state.events
    assert state.events[-2:] == ["source_closed", "disconnected"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("worker_error", "usage_error"),
    [
        (RuntimeError("synthetic worker shutdown"), None),
        (None, RuntimeError("synthetic private usage detail")),
        (
            RuntimeError("synthetic worker shutdown"),
            RuntimeError("synthetic private usage detail"),
        ),
        (asyncio.CancelledError(), RuntimeError("synthetic private usage detail")),
    ],
)
async def test_shutdown_flushes_usage_and_preserves_the_first_close_failure(
    startup, caplog, worker_error, usage_error
):
    ctx, settings, state = startup
    state.with_microphone = False
    state.worker_close_error = worker_error
    state.usage_close_error = usage_error
    state.warm_release.set()
    expected = worker_error if worker_error is not None else usage_error
    with pytest.raises(type(expected)) as raised:
        await asyncio.wait_for(worker_livekit.serve_job(ctx, settings), 2)
    assert raised.value is expected
    assert state.events.count("worker_closed") == state.events.count("usage_closed") == 1
    assert state.events.index("worker_closed") < state.events.index("usage_closed")
    assert {"consumer_stopped", "heartbeat_stopped"} <= set(state.events)
    assert not state.handlers
    assert state.events[-2:] == ["source_closed", "disconnected"]
    secondary = [
        record for record in caplog.records if getattr(record, "stage", None) == "usage_close"
    ]
    assert len(secondary) == (1 if worker_error is not None and usage_error is not None else 0)
    if secondary:
        assert secondary[0].getMessage() == "Vox worker stopped"
        assert secondary[0].error_type == "RuntimeError"
    assert "synthetic private usage detail" not in caplog.text
