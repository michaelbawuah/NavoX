"""Private memory cannot reach response audio before its host-owned notice."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture
from test_vox_inference import FailingRealtime, Hooks, Socket, settings
from test_vox_memory_recall import MemoryCheck

from navox.db.conversation_memory import MemoryConversation, MemoryThread
from navox.memory import history
from navox.memory.extraction import mark_followup_exposure, release_unexposed_followups
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline
from navox.vox.inference_realtime import OpenAIRealtimeAdapter
from navox.vox.inference_worker import ConfiguredVoiceWorker
from navox.vox.recall_delivery import PRIVACY_NOTICE, RecallDeliveryCoordinator
from navox.vox.recall_tools import MemoryReadTools
from navox.vox.session_context import CallContextKey, InMemoryContextStore, VoxCallContext
from navox.vox.worker import WorkerAccessLost, WorkerAssignment, WorkerAuthority

history_env = shared_history_fixture


async def setup_recall(env, *, notice_available=True, context_id=None):
    generation = await enable(env)
    now = datetime.now(UTC)
    await archive(
        env,
        generation,
        question="I have an interview tomorrow.",
        when=now - timedelta(days=2),
    )
    check = MemoryCheck(env)
    key = f"vox:{context_id or uuid4()}"
    delivery = RecallDeliveryCoordinator(
        check,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        context_key=key,
        notice_available=notice_available,
        clock=lambda: now,
    )
    tools = MemoryReadTools(check, env.settings, context_key=key, delivery=delivery)
    result = await tools.context("interview")
    return generation, now, check, delivery, tools, result


async def thread_value(env):
    async with env.factory() as database:
        row = await database.scalar(select(MemoryThread))
        if row is None:
            return None, None
        return row, history.decrypt(
            env.settings, env.user_id, env.workspace_id, row.id, "thread", row.encrypted_content
        )


async def other_recall(env, now, *, context_id=None):
    check = MemoryCheck(env)
    key = f"vox:{context_id or uuid4()}"
    delivery = RecallDeliveryCoordinator(
        check,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        context_key=key,
        notice_available=True,
        clock=lambda: now,
    )
    return await MemoryReadTools(check, env.settings, context_key=key, delivery=delivery).context(
        ""
    )


@pytest.mark.asyncio
async def test_notice_and_output_exposure_are_distinct_from_completed_followup(history_env):
    env = history_env
    _, _, _, delivery, tools, result = await setup_recall(env)
    assert result["threads"] and result["past_context"]
    assert "_followup_claim_ids" not in result
    assert delivery.needs_output_boundary
    with pytest.raises(WorkerAccessLost, match="notice"):
        await delivery.output_started()
    assert await delivery.before_output() == PRIVACY_NOTICE
    _, before = await thread_value(env)
    assert before["delivery_version"] == 1 and "exposure_started_at" not in before
    await delivery.notice_played(PRIVACY_NOTICE)
    await delivery.played(PRIVACY_NOTICE)
    _, after_notice = await thread_value(env)
    assert "exposure_started_at" not in after_notice
    await delivery.output_started()
    assert not delivery.needs_output_boundary
    row, exposed = await thread_value(env)
    assert row.status == "offered" and "exposure_started_at" in exposed
    assert "responded_at" not in exposed
    # An uncertain/paraphrased exposure is not offered a second time even before
    # the stronger completed-answer receipt can be committed.
    assert (await tools.context(""))["threads"] == []
    await delivery.played("How did your interview go?")
    row, responded = await thread_value(env)
    assert row.status == "responded" and "responded_at" in responded
    assert await delivery.before_output() is None


@pytest.mark.asyncio
async def test_empty_warm_call_close_releases_only_its_unexposed_claim(history_env):
    env = history_env
    _, now, check, delivery, _, _ = await setup_recall(env)
    row, before = await thread_value(env)
    assert row.status == "offered"
    assert (await other_recall(env, now))["threads"] == []
    # Media authority is normally retired before close cleanup. The release
    # uses captured owner/context/generation and returns no private contents.
    check.authority = WorkerAuthority(uuid4(), uuid4())
    await delivery.close()
    row, value = await thread_value(env)
    assert row.status == "open"
    assert "offered_context" not in value and "delivery_version" not in value
    assert (await other_recall(env, now))["threads"]
    with pytest.raises(WorkerAccessLost):
        await delivery.output_started()


@pytest.mark.asyncio
async def test_failure_after_generic_notice_but_before_private_audio_releases_offer(history_env):
    env = history_env
    _, now, _, delivery, _, _ = await setup_recall(env)
    await delivery.before_output()
    await delivery.notice_played(PRIVACY_NOTICE)
    await delivery.failed()
    row, value = await thread_value(env)
    assert row.status == "open" and "exposure_started_at" not in value
    assert (await other_recall(env, now))["threads"]


@pytest.mark.asyncio
async def test_partial_or_unknown_private_audio_never_releases_or_reoffers(history_env):
    env = history_env
    _, now, _, delivery, _, _ = await setup_recall(env)
    await delivery.before_output()
    await delivery.notice_played(PRIVACY_NOTICE)
    await delivery.output_started()
    await delivery.failed()
    row, value = await thread_value(env)
    assert row.status == "offered" and "exposure_started_at" in value
    assert (await other_recall(env, now))["threads"] == []


@pytest.mark.asyncio
async def test_legacy_offer_without_exposure_receipt_is_not_proven_unheard(history_env):
    env = history_env
    generation = await enable(env)
    now, context_id = datetime.now(UTC), uuid4()
    await archive(
        env, generation, question="I have an exam tomorrow.", when=now - timedelta(days=2)
    )
    key = f"vox:{context_id}"
    check = MemoryCheck(env)
    assert (await MemoryReadTools(check, env.settings, context_key=key).context())["threads"]
    delivery = RecallDeliveryCoordinator(
        check,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        context_key=key,
        notice_available=True,
    )
    await MemoryReadTools(check, env.settings, context_key=key, delivery=delivery).context()
    await delivery.close()
    row, value = await thread_value(env)
    assert row.status == "offered" and "delivery_version" not in value
    assert (await other_recall(env, now))["threads"] == []


@pytest.mark.asyncio
async def test_unavailable_notice_withholds_protected_context_before_claiming(history_env):
    env = history_env
    _, now, _, delivery, _, result = await setup_recall(env, notice_available=False)
    assert result["threads"] == [] and result["past_context"] == []
    assert not result["spoken_privacy_warning_required"]
    assert result["protected_memory_withheld"] is True
    row, value = await thread_value(env)
    assert row.status == "open" and "offered_context" not in value
    assert await delivery.before_output() is None
    assert (await other_recall(env, now))["threads"]


@pytest.mark.asyncio
async def test_later_private_lookup_reopens_boundary_without_repeating_notice(history_env):
    env = history_env
    _, _, _, delivery, tools, _ = await setup_recall(env)
    await delivery.before_output()
    await delivery.notice_played(PRIVACY_NOTICE)
    await delivery.output_started()
    assert not delivery.needs_output_boundary
    # A later tool result after public prelude must be fenced before its next
    # audio frame, even though this call has already completed the notice.
    assert (await tools.context("interview"))["past_context"]
    assert delivery.needs_output_boundary
    assert await delivery.before_output() is None
    await delivery.output_started()
    assert not delivery.needs_output_boundary


@pytest.mark.asyncio
async def test_arbitrary_notice_text_does_not_release_gate_or_escape_in_error(history_env):
    env = history_env
    _, _, _, delivery, _, _ = await setup_recall(env)
    await delivery.before_output()
    private = "synthetic private unplayed provider transcript"
    with pytest.raises(WorkerAccessLost) as rejected:
        await delivery.notice_played(private)
    assert private not in str(rejected.value)
    with pytest.raises(WorkerAccessLost):
        await delivery.output_started()


@pytest.mark.asyncio
@pytest.mark.parametrize("moment", ["before_notice", "after_notice"])
async def test_source_expiry_is_rechecked_before_private_output(history_env, moment):
    env = history_env
    _, now, _, delivery, _, _ = await setup_recall(env)
    if moment == "after_notice":
        await delivery.before_output()
        await delivery.notice_played(PRIVACY_NOTICE)
    async with env.factory() as database:
        source = await database.scalar(select(MemoryConversation))
        source.expires_at = now - timedelta(seconds=1)
        await database.commit()
    with pytest.raises(WorkerAccessLost, match="sources"):
        if moment == "before_notice":
            await delivery.before_output()
        else:
            await delivery.output_started()
    _, value = await thread_value(env)
    assert "exposure_started_at" not in value


@pytest.mark.asyncio
async def test_source_forget_fences_pending_notice_and_cannot_be_released_back(history_env):
    env = history_env
    _, _, _, delivery, _, _ = await setup_recall(env)
    await delivery.before_output()
    source = (await env.client.get("/api/v1/memory/history")).json()[0]
    assert (await env.client.delete(f"/api/v1/memory/history/{source['id']}")).status_code == 204
    with pytest.raises(WorkerAccessLost):
        await delivery.notice_played(PRIVACY_NOTICE)
    await delivery.close()
    assert await thread_value(env) == (None, None)


@pytest.mark.asyncio
async def test_privacy_change_during_notice_never_releases_private_audio(history_env):
    env = history_env
    _, _, _, delivery, _, _ = await setup_recall(env)
    await delivery.before_output()
    await enable(env, off_record=True)
    with pytest.raises(WorkerAccessLost, match="privacy"):
        await delivery.notice_played(PRIVACY_NOTICE)
    with pytest.raises(WorkerAccessLost):
        await delivery.output_started()


@pytest.mark.asyncio
async def test_owner_change_and_interrupted_notice_do_not_authorize_output(history_env):
    env = history_env
    _, _, check, delivery, _, _ = await setup_recall(env)
    await delivery.before_output()
    delivery.invalidate("interrupted")
    with pytest.raises(WorkerAccessLost, match="notice"):
        await delivery.notice_played(PRIVACY_NOTICE)
    check.authority = WorkerAuthority(uuid4(), uuid4())
    with pytest.raises(WorkerAccessLost, match="owner"):
        await delivery.before_output()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["context", "generation", "identifier"])
async def test_release_cannot_borrow_another_context_generation_or_claim(history_env, change):
    env = history_env
    generation, now, _, delivery, _, _ = await setup_recall(env)
    row, _ = await thread_value(env)
    async with env.factory() as database:
        released = await release_unexposed_followups(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            context_key="vox:" + str(uuid4()) if change == "context" else delivery.context_key,
            generation=generation + 1 if change == "generation" else generation,
            thread_ids=frozenset({uuid4() if change == "identifier" else row.id}),
            now=now,
        )
        await database.commit()
    assert released == frozenset()
    current, value = await thread_value(env)
    assert current.status == "offered" and value["offered_context"] == delivery.context_key


@pytest.mark.asyncio
async def test_exposure_uses_parent_transaction_and_rollback_keeps_offer_releasable(history_env):
    env = history_env
    generation, now, _, delivery, _, _ = await setup_recall(env)
    row, _ = await thread_value(env)
    async with env.factory() as database:
        assert await mark_followup_exposure(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            context_key=delivery.context_key,
            generation=generation,
            thread_ids=frozenset({row.id}),
            now=now,
        ) == frozenset({row.id})
        await database.rollback()
    _, value = await thread_value(env)
    assert "exposure_started_at" not in value
    await delivery.close()
    assert (await thread_value(env))[0].status == "open"


@pytest.mark.asyncio
async def test_released_claim_cannot_be_exposed_from_old_cached_context(history_env):
    env = history_env
    generation, now, _, delivery, _, _ = await setup_recall(env)
    row, _ = await thread_value(env)
    await delivery.before_output()
    await delivery.notice_played(PRIVACY_NOTICE)
    async with env.factory() as database:
        await release_unexposed_followups(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            context_key=delivery.context_key,
            generation=generation,
            thread_ids=frozenset({row.id}),
            now=now,
        )
        await database.commit()
    with pytest.raises(WorkerAccessLost, match="claim"):
        await delivery.output_started()


@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Exposure/release ordering requires isolated PostgreSQL owner locks",
)
@pytest.mark.asyncio
async def test_concurrent_release_and_exposure_have_only_one_valid_winner(history_env):
    env = history_env
    generation, now, _, delivery, _, _ = await setup_recall(env)
    row, _ = await thread_value(env)

    async def apply(operation):
        async with env.factory() as database:
            result = await operation(
                database,
                env.settings,
                env.user_id,
                env.workspace_id,
                context_key=delivery.context_key,
                generation=generation,
                thread_ids=frozenset({row.id}),
                now=now,
            )
            await database.commit()
            return result

    exposed, released = await asyncio.gather(
        apply(mark_followup_exposure), apply(release_unexposed_followups)
    )
    assert bool(exposed) != bool(released)
    current, value = await thread_value(env)
    assert (current.status == "open") == bool(released)
    assert ("exposure_started_at" in value) == bool(exposed)


async def voice_worker(env, check, delivery, realtime, pipeline, output, *, mode):
    connection = uuid4()
    context = await VoxCallContext.load(
        CallContextKey(
            env.user_id, env.workspace_id, UUID(delivery.context_key.split(":", 1)[1]), 0
        ),
        InMemoryContextStore(),
    )
    return ConfiguredVoiceWorker(
        WorkerAssignment(
            connection, "vx-" + connection.hex, "vc-" + connection.hex, "va-" + connection.hex
        ),
        check,
        output,
        VoxInferenceConfig(mode=mode),
        context,
        realtime,
        pipeline,
        host_hooks=delivery,
    )


class RecordedOutput:
    def __init__(self, env):
        self.env, self.events = env, []

    async def play(self, audio, check):
        await check()
        _, value = await thread_value(self.env)
        text = audio.decode()
        if text == PRIVACY_NOTICE:
            assert "exposure_started_at" not in value
        else:
            assert "exposure_started_at" in value
        self.events.append(text)

    async def play_pcm(self, audio, check):
        await check()
        _, value = await thread_value(self.env)
        assert "exposure_started_at" in value
        self.events.append("private-model-pcm")

    async def finish_pcm(self, check):
        await check()

    async def clear(self):
        pass


@pytest.mark.asyncio
async def test_pipeline_host_notice_precedes_model_audio_and_uses_existing_metered_tts(history_env):
    env = history_env
    _, _, check, delivery, tools, _ = await setup_recall(env)
    usage, paths = Hooks(), []

    async def handle(request):
        body = json.loads(request.content)
        paths.append(request.url.path)
        if request.url.path.endswith("completions"):
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "How did your interview go?"}}]}
            )
        assert request.url.path.endswith("speech")
        return httpx.Response(200, content=body["input"].encode())

    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(mode="cascade"),
        settings(),
        check,
        usage,
        transport=httpx.MockTransport(handle),
        memory_context=tools.context,
        host_hooks=delivery,
    )
    output = RecordedOutput(env)
    worker = await voice_worker(
        env, check, delivery, FailingRealtime(False), pipeline, output, mode="cascade"
    )
    await worker._respond(None, 0, text="Hello.")
    assert not worker.failed
    assert output.events == [PRIVACY_NOTICE, "How did your interview go?"]
    assert paths.count("/v1/chat/completions") == 1
    assert paths.count("/v1/audio/speech") == 2
    assert len(usage.started) == len(usage.finished) == 3
    row, value = await thread_value(env)
    assert row.status == "responded" and "responded_at" in value
    await worker.close()


@pytest.mark.asyncio
async def test_realtime_host_notice_precedes_first_private_pcm_even_if_model_omits_it(history_env):
    env = history_env
    _, _, check, delivery, tools, _ = await setup_recall(env)
    socket, usage = Socket(), Hooks()

    async def connect(url, headers):
        return socket

    async def handle(request):
        assert request.url.path == "/v1/audio/speech"
        assert json.loads(request.content)["input"] == PRIVACY_NOTICE
        return httpx.Response(200, content=PRIVACY_NOTICE.encode())

    realtime = OpenAIRealtimeAdapter(
        VoxInferenceConfig(),
        settings(),
        check,
        usage,
        connector=connect,
        memory_context=tools.context,
        host_hooks=delivery,
    )
    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(),
        settings(),
        check,
        usage,
        transport=httpx.MockTransport(handle),
        host_hooks=delivery,
    )
    output = RecordedOutput(env)
    worker = await voice_worker(env, check, delivery, realtime, pipeline, output, mode="realtime")
    await worker._respond(None, 0, text="Hello.")
    assert not worker.failed
    assert output.events == [PRIVACY_NOTICE, "private-model-pcm"]
    assert worker.context.history(worker.assignment.connection_id)[-1].answer == (
        PRIVACY_NOTICE + " A useful answer."
    )
    await worker.close()


@pytest.mark.asyncio
async def test_actual_realtime_warm_is_silent_and_close_releases_unexposed_offer(history_env):
    env = history_env
    _, now, check, delivery, tools, _ = await setup_recall(env)
    socket, usage = Socket(), Hooks()

    async def connect(url, headers):
        return socket

    async def forbidden(request):
        pytest.fail("Warm must never request TTS, a model reply or any audio")

    realtime = OpenAIRealtimeAdapter(
        VoxInferenceConfig(),
        settings(),
        check,
        usage,
        connector=connect,
        memory_context=tools.context,
        host_hooks=delivery,
    )
    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(), settings(), check, usage, transport=httpx.MockTransport(forbidden)
    )
    output = RecordedOutput(env)
    worker = await voice_worker(env, check, delivery, realtime, pipeline, output, mode="realtime")
    await worker.warm()
    assert output.events == [] and usage.started == []
    assert not any(event["type"] == "response.create" for event in socket.sent)
    await worker.close()
    assert socket.closed and (await thread_value(env))[0].status == "open"
    assert (await other_recall(env, now))["threads"]


@pytest.mark.asyncio
async def test_failed_notice_never_publishes_private_audio_and_releases_claim(history_env):
    env = history_env
    _, now, check, delivery, tools, _ = await setup_recall(env)
    usage = Hooks()

    async def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith("completions"):
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "How did your interview go?"}}]}
            )
        if body["input"] == PRIVACY_NOTICE:
            return httpx.Response(503)
        return httpx.Response(200, content=body["input"].encode())

    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(mode="cascade"),
        settings(),
        check,
        usage,
        transport=httpx.MockTransport(handle),
        memory_context=tools.context,
        host_hooks=delivery,
    )
    output = RecordedOutput(env)
    worker = await voice_worker(
        env, check, delivery, FailingRealtime(False), pipeline, output, mode="cascade"
    )
    await worker._respond(None, 0, text="Hello.")
    assert worker.failed and output.events == []
    assert (await thread_value(env))[0].status == "open"
    assert (await other_recall(env, now))["threads"]
    await worker.close()
