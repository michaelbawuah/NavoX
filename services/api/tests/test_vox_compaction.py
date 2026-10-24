"""Active-call compaction and rollover races; no physical-phone acceptance claims."""

import asyncio
import json
import struct
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from test_vox_inference import Check, Hooks, Output, Socket, settings

from navox.vox.context_store import decode_checkpoint, encode_checkpoint
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline, ProviderRejected
from navox.vox.inference_realtime import OpenAIRealtimeAdapter
from navox.vox.inference_worker import ConfiguredVoiceWorker
from navox.vox.session_context import (
    SUMMARY_LABEL,
    CallContextKey,
    ContextSnapshot,
    InMemoryContextStore,
    VoxCallContext,
    compaction_prefix,
    history_messages,
)
from navox.vox.worker import MemoryTurn, WorkerAccessLost, WorkerAssignment


def long_history():
    return tuple(MemoryTurn(f"Question {i}: " + "context " * 60, "Answer " * 50) for i in range(12))


async def context_for(check, turns):
    key = CallContextKey(check.authority.user_id, check.authority.workspace_id, uuid4(), 0)
    store = InMemoryContextStore()
    await store.compare_and_set(key, 0, turns)
    context = await VoxCallContext.load(key, store)
    epoch = uuid4()
    context.begin_epoch(epoch)
    return context, store, epoch


@pytest.mark.asyncio
async def test_compaction_preserves_recent_and_concurrently_completed_turns_and_roundtrips():
    context, store, epoch = await context_for(Check(), long_history())
    original = context.history(epoch)
    prefix = compaction_prefix(original, threshold=8000, keep_recent=3)
    assert prefix and original[-1] not in prefix
    await context.commit(epoch, "Correction: the room is 42.", "I heard room 42.")
    assert await context.compact(
        epoch, prefix, "The student discussed a course. Details uncertain."
    )
    final = context.history(epoch)
    assert final[0].kind == "summary"
    assert final[1:] == (
        *original[len(prefix) :],
        MemoryTurn("Correction: the room is 42.", "I heard room 42."),
    )
    assert not await context.compact(epoch, prefix, "A stale result must not overwrite.")
    snapshot = await store.load(context.key)
    cipher, session = Fernet(Fernet.generate_key()), uuid4()
    wire = encode_checkpoint(cipher, context.key, session, snapshot)
    assert "room 42" not in wire
    assert decode_checkpoint(cipher, context.key, session, snapshot.revision, wire) == snapshot
    messages = history_messages(final)
    assert messages[0]["role"] == "user"
    assert "not a new request" in messages[0]["content"]
    assert messages[-1]["content"] == "I heard room 42."


@pytest.mark.asyncio
async def test_closed_context_refuses_late_summary_without_changing_checkpoint():
    context, store, epoch = await context_for(Check(), long_history())
    before = await store.load(context.key)
    context.close()
    with pytest.raises(WorkerAccessLost):
        await context.compact(epoch, before.turns[:-2], "Late private summary")
    assert await store.load(context.key) == before


@pytest.mark.asyncio
async def test_invalid_or_nonshrinking_summary_never_discards_original_history():
    context, store, epoch = await context_for(Check(), (MemoryTurn("a", "b"),) * 3)
    before = await store.load(context.key)
    for summary in ("", "x" * 8001, "This summary is larger than the tiny original context." * 20):
        with pytest.raises(ValueError):
            await context.compact(epoch, before.turns[:-1], summary)
        assert await store.load(context.key) == before
    with pytest.raises(ValueError):
        ContextSnapshot(1, (MemoryTurn("Q", "A"), MemoryTurn(SUMMARY_LABEL, "text", "summary")))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish,content",
    [("stop", "Exact identifier: 42."), ("length", "Partial"), ("stop", "x" * 2001)],
)
async def test_summary_request_is_metered_tool_free_nonstored_and_rejects_incomplete(
    finish, content
):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["store"] is False and "tools" not in body
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": finish, "message": {"content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6},
            },
        )

    hooks = Hooks()
    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(), settings(), Check(), hooks, transport=httpx.MockTransport(handler)
    )
    if finish == "stop" and len(content) < 2000:
        assert await pipeline.summarize(long_history()) == content
    else:
        with pytest.raises(ProviderRejected):
            await pipeline.summarize(long_history())
    assert len(seen) == len(hooks.started) == len(hooks.finished) == 1
    assert hooks.finished[0][1]["input_text_tokens"] == 20


@pytest.mark.asyncio
async def test_context_pressure_renews_session_without_greeting_or_waiting_for_age_cap():
    sockets = []

    async def connect(*_):
        socket = Socket()
        sockets.append(socket)
        return socket

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), connector=connect
    )
    history = (
        MemoryTurn(SUMMARY_LABEL, "Student's identifier is 42.", "summary"),
        MemoryTurn("Q", "A"),
    )
    await adapter._connect(history)
    adapter._context_ceiling = 100_000
    assert await adapter.rollover_if_quiet(history)
    assert sockets[0].closed and adapter.socket is sockets[1]
    seeded = [e for e in sockets[1].sent if e["type"] == "conversation.item.create"]
    assert (
        len(seeded) == 3 and "Student's identifier is 42" in seeded[0]["item"]["content"][0]["text"]
    )
    assert not any(e["type"] == "response.create" for e in sockets[1].sent)
    await adapter.close()


@pytest.mark.asyncio
async def test_close_during_warmup_cannot_resurrect_provider_socket():
    sockets, started, release = [], asyncio.Event(), asyncio.Event()

    async def connect(*_):
        if sockets:
            started.set()
            await release.wait()
        socket = Socket()
        sockets.append(socket)
        return socket

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(), settings(), Check(), Hooks(), connector=connect
    )
    await adapter._connect(())
    pending = asyncio.create_task(adapter.rollover_if_quiet((), force=True))
    await started.wait()
    await adapter.close()
    release.set()
    assert await pending is False
    assert adapter.socket is None and all(s.closed for s in sockets)


@pytest.mark.asyncio
async def test_speech_onset_during_summary_uses_old_socket_then_switches_at_quiet_boundary():
    check, started, release, sockets = Check(), asyncio.Event(), asyncio.Event(), []
    context, _, epoch = await context_for(check, long_history())

    class Pipeline:
        async def summarize(self, history):
            started.set()
            await release.wait()
            return "Student is studying a course."

    async def connect(*_):
        socket = Socket()
        sockets.append(socket)
        return socket

    config = VoxInferenceConfig(context_compact_after_bytes=8000, context_keep_recent_turns=2)
    adapter = OpenAIRealtimeAdapter(config, settings(), check, Hooks(), connector=connect)
    worker = ConfiguredVoiceWorker(
        WorkerAssignment(epoch, "vx-test", "vc-test", "va-test"),
        check,
        Output(),
        config,
        context,
        adapter,
        Pipeline(),
    )
    await worker.warm()
    maintenance = asyncio.create_task(worker._maintain_context())
    await started.wait()
    await asyncio.wait_for(worker.receive("vc-test", struct.pack("<h", 2000) * 320), 0.2)
    assert any(e["type"] == "input_audio_buffer.append" for e in sockets[0].sent)
    release.set()
    await maintenance
    assert worker._context_refresh_required and adapter.socket is sockets[0]
    await worker.interrupt()
    await worker._maintain_context()
    assert adapter.socket is sockets[1] and not worker._context_refresh_required
    assert not worker.failed
    await worker.close()
