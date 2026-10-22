"""Worker privacy/lifecycle proofs; no tests here qualify a real mic or RTC call."""

import asyncio
import io
import json
import struct
import wave
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import av
import pytest
from sqlalchemy import delete, event, select
from test_ai_evaluation import model_for
from test_ai_runtime import FakeAdapter
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import begin, confirm

from navox.ai.catalog import catalog_template
from navox.ai.control import record_evaluation
from navox.ai.conversation import (
    CONVERSATION_PROMPT,
    CONVERSATION_SCHEMA,
    ConversationAnswer,
    conversation_task,
)
from navox.ai.foundation.contracts import (
    Profile,
    Provider,
    ProviderGrant,
    ProviderPolicy,
    Sensitivity,
    TaskType,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.runtime import GatewayRuntime
from navox.ai.store import GatewayStore
from navox.core.settings import Settings
from navox.db.ai_registry import AIProfileAssignment, AITaskRun
from navox.db.models import User, UserSession, WorkspaceMembership
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.vox import media
from navox.vox.worker import (
    DatabaseAuthority,
    PolicyVoiceGateway,
    TransientConversationContext,
    VoiceTurnWorker,
    WorkerAccessLost,
    WorkerAssignment,
    WorkerAuthority,
    is_voice,
)
from navox.vox.worker_livekit import (
    LiveKitAudioOutput,
    _confirmed,
    build_server,
    participant_metadata,
)

vox_env = shared_vox_fixture


def assignment(epoch=None):
    epoch = epoch or uuid4()
    return WorkerAssignment(epoch, "vx-" + epoch.hex, "vc-" + epoch.hex, "va-" + epoch.hex)


def pcm(voiced=True):
    return struct.pack("<h", 2000 if voiced else 0) * 320  # 20 ms.


class Check:
    def __init__(self):
        self.active = True
        self.calls = 0
        self.authority = WorkerAuthority(uuid4(), uuid4())

    async def __call__(self):
        self.calls += 1
        if not self.active:
            raise WorkerAccessLost("Ended")
        return self.authority


class Gateway:
    def __init__(self):
        self.calls = []

    async def transcribe(self, audio):
        with wave.open(io.BytesIO(audio)) as value:
            assert (value.getnchannels(), value.getframerate(), value.getsampwidth()) == (
                1,
                16000,
                2,
            )
            assert 200 <= value.getnframes() * 1000 // 16000 <= 30000
        self.calls.append("transcribe")
        return "Explain gravity"

    async def answer(self, question, history):
        self.calls.append(("answer", question, history))
        return "Gravity attracts masses."

    async def synthesize(self, answer):
        self.calls.append(("synthesize", answer))
        return b"synthetic-test-mp3"


class Output:
    def __init__(self):
        self.played = []
        self.clears = 0

    async def play(self, audio, check):
        await check()
        self.played.append(audio)

    async def clear(self):
        self.clears += 1


async def utterance(worker):
    for _ in range(10):
        await worker.receive(worker.assignment.client_identity, pcm())
    for _ in range(30):
        await worker.receive(worker.assignment.client_identity, pcm(False))


async def settled(worker):
    task = worker._task
    if task is not None:
        await asyncio.wait_for(task, 2)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {**value, "user_id": str(uuid4())},
        lambda value: {**value, "connection_id": "bad"},
        lambda value: {**value, "client_identity": "vc-" + uuid4().hex},
        lambda value: {**value, "room_name": "private-person-name"},
        lambda value: {**value, "agent_identity": 1},
    ],
)
def test_dispatch_cannot_supply_owners_grants_or_mismatched_locators(mutation):
    value = asdict(assignment())
    value["connection_id"] = str(value["connection_id"])
    assert WorkerAssignment.from_metadata(json.dumps(value)).room_name == value["room_name"]
    with pytest.raises(WorkerAccessLost):
        WorkerAssignment.from_metadata(json.dumps(mutation(value)))


def test_metadata_proof_is_exact_and_rejects_bool_generation_or_extra_fields():
    value = assignment()
    metadata = {"connection_id": str(value.connection_id), "generation": 1}
    assert participant_metadata(json.dumps(metadata), value, 1)
    assert not participant_metadata(json.dumps({**metadata, "generation": True}), value, 1)
    assert not participant_metadata(json.dumps({**metadata, "user_id": "x"}), value, 1)
    assert not participant_metadata(json.dumps(metadata), assignment(), 1)


def test_real_worker_cannot_start_with_default_unqualified_flags():
    with pytest.raises(Exception, match="not available"):
        build_server(Settings())


def test_frame_bounds_are_checked_before_any_provider_operation():
    assert is_voice(pcm())
    assert not is_voice(pcm(False))
    for value in (b"", b"a", b"x" * 3202):
        with pytest.raises(ValueError):
            is_voice(value)


def short_mp3():
    """Encode synthetic silence locally to verify the real MP3→RTC PCM bridge."""
    output = io.BytesIO()
    with av.open(output, "w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=48_000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=4800)
        frame.sample_rate = 48_000
        frame.planes[0].update(bytes(frame.planes[0].buffer_size))
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


class PcmSource:
    def __init__(self):
        self.frames = []
        self.playouts = 0

    async def capture_frame(self, frame):
        self.frames.append((frame.sample_rate, frame.num_channels, frame.samples_per_channel))

    async def wait_for_playout(self):
        self.playouts += 1


@pytest.mark.asyncio
async def test_actual_mp3_decode_checks_each_twenty_ms_pcm_frame_before_queueing():
    source = PcmSource()
    checks = 0

    async def check():
        nonlocal checks
        checks += 1

    await LiveKitAudioOutput(source).play(short_mp3(), check)
    assert source.frames and source.playouts == 1 and len(source.frames) == checks
    assert all(
        rate == 48000 and channels == 1 and 0 < samples <= 960
        for rate, channels, samples in source.frames
    )


@pytest.mark.asyncio
async def test_actual_mp3_decode_stops_queueing_on_next_failed_authority_proof():
    source = PcmSource()
    checks = 0

    async def check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise WorkerAccessLost("Ended between output frames")

    with pytest.raises(WorkerAccessLost):
        await LiveKitAudioOutput(source).play(short_mp3(), check)
    assert len(source.frames) == 1 and source.playouts == 0


@pytest.mark.asyncio
async def test_realistic_bounded_turn_uses_wav_then_answer_then_audio_and_memory_only():
    check, gateway, output = Check(), Gateway(), Output()
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    await utterance(worker)
    await settled(worker)
    assert gateway.calls == [
        "transcribe",
        ("answer", "Explain gravity", ()),
        ("synthesize", "Gravity attracts masses."),
    ]
    assert output.played == [b"synthetic-test-mp3"]
    assert check.calls >= 45
    assert worker._history[0].question == "Explain gravity"
    await worker.close()
    assert not worker._history and not worker._pcm and worker._task is None


@pytest.mark.asyncio
async def test_silence_short_noise_and_wrong_participant_never_reach_gateway():
    check, gateway, output = Check(), Gateway(), Output()
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    for _ in range(40):
        await worker.receive(worker.assignment.client_identity, pcm(False))
    await worker.receive(worker.assignment.client_identity, pcm())  # 20 ms is below turn minimum.
    for _ in range(30):
        await worker.receive(worker.assignment.client_identity, pcm(False))
    with pytest.raises(WorkerAccessLost):
        await worker.receive("other-client", pcm())
    assert not gateway.calls and not output.played
    await worker.close()


@pytest.mark.asyncio
async def test_vad_upstream_can_stop_frames_entirely_and_wall_clock_still_seals_turn():
    check, gateway, output = Check(), Gateway(), Output()
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    for _ in range(10):
        await worker.receive(worker.assignment.client_identity, pcm())
    await worker.flush_idle()
    assert not gateway.calls
    await asyncio.sleep(0.61)  # No RTC silence frames are delivered in this interval.
    await worker.flush_idle()
    await settled(worker)
    assert gateway.calls[0] == "transcribe" and output.played
    await worker.close()


@pytest.mark.asyncio
async def test_idle_flush_rechecks_authority_before_releasing_buffer_to_stt():
    check, gateway, output = Check(), Gateway(), Output()
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    for _ in range(10):
        await worker.receive(worker.assignment.client_identity, pcm())
    check.active = False
    with pytest.raises(WorkerAccessLost):
        await worker.flush_idle()
    assert not gateway.calls and not output.played
    await worker.close()


@pytest.mark.asyncio
async def test_loss_during_stt_discards_transcript_before_conversation_or_output():
    check, gateway, output = Check(), Gateway(), Output()

    async def transcribe(audio):
        check.active = False
        return "secret ephemeral phrase"

    gateway.transcribe = transcribe
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    await utterance(worker)
    await settled(worker)
    assert not gateway.calls and not output.played and not worker._history
    await worker.close()


@pytest.mark.asyncio
async def test_barge_in_cancels_slow_stt_and_late_result_cannot_speak_or_become_history():
    check, gateway, output = Check(), Gateway(), Output()
    started = asyncio.Event()

    async def late_transcript(audio):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "a result delivered after cancellation"

    gateway.transcribe = late_transcript
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    await utterance(worker)
    await asyncio.wait_for(started.wait(), 2)
    await worker.receive(worker.assignment.client_identity, pcm())
    assert not output.played and not gateway.calls and not worker._history
    assert not worker.failed
    assert len(worker._pcm) == len(pcm())
    await worker.close()


@pytest.mark.asyncio
async def test_hangup_clears_memory_and_suppresses_late_synthesis():
    check, gateway, output = Check(), Gateway(), Output()
    started = asyncio.Event()

    async def late_audio(answer):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return b"late-media"

    gateway.synthesize = late_audio
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    await utterance(worker)
    await asyncio.wait_for(started.wait(), 2)
    await worker.close()
    assert not output.played and not worker._history and not worker._pcm
    with pytest.raises(WorkerAccessLost):
        await worker.receive(worker.assignment.client_identity, pcm())


@pytest.mark.asyncio
async def test_authority_loss_between_outgoing_frames_clears_queued_audio():
    check, gateway, output = Check(), Gateway(), Output()

    async def play(audio, current):
        await current()
        output.played.append(b"first-frame")
        check.active = False
        await current()
        output.played.append(b"unauthorized-second-frame")

    output.play = play
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    await utterance(worker)
    await settled(worker)
    assert output.played == [b"first-frame"] and output.clears == 1
    assert not worker._history
    await worker.close()


@pytest.mark.asyncio
async def test_provider_failure_is_terminal_instead_of_leaving_a_silent_live_call():
    check, gateway, output = Check(), Gateway(), Output()

    async def unavailable(audio):
        raise RuntimeError("a provider body must not escape the worker")

    gateway.transcribe = unavailable
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    await utterance(worker)
    await settled(worker)
    assert worker.failed and output.clears == 1 and not output.played
    with pytest.raises(WorkerAccessLost):
        await worker.receive(worker.assignment.client_identity, pcm())
    await worker.close()


async def prepared(env):
    _, intent = await begin(env)
    value = WorkerAssignment(
        intent.connection_id, intent.room_name, intent.client_identity, intent.agent_identity
    )
    return intent, DatabaseAuthority(env.factory, value)


@pytest.mark.asyncio
async def test_dispatch_waits_for_confirmation_without_receiving_or_changing_epoch(vox_env):
    intent, check = await prepared(vox_env)
    with pytest.raises(WorkerAccessLost):
        await check()
    task = asyncio.create_task(_confirmed(check))
    await asyncio.sleep(0)
    await confirm(vox_env, intent)
    await asyncio.wait_for(task, 2)
    assert await check() == WorkerAuthority(vox_env.user_id, vox_env.workspace_id)


@pytest.mark.asyncio
async def test_revoked_preparing_dispatch_fails_immediately_instead_of_waiting_to_join(vox_env):
    intent, check = await prepared(vox_env)
    async with vox_env.factory() as database:
        await media.revoke_connection(
            database,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
            intent.connection_id,
        )
    with pytest.raises(WorkerAccessLost):
        await asyncio.wait_for(_confirmed(check), 1)


@pytest.mark.asyncio
async def test_realtime_authority_is_exact_one_joined_query_and_never_restores_connection(vox_env):
    intent, check = await prepared(vox_env)
    await confirm(vox_env, intent)
    statements = []
    engine = vox_env.factory.kw["bind"].sync_engine

    def observed(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observed)
    try:
        assert await check() == WorkerAuthority(vox_env.user_id, vox_env.workspace_id)
    finally:
        event.remove(engine, "before_cursor_execute", observed)
    assert len(statements) == 1 and "JOIN user_sessions" in statements[0]
    assert "FOR UPDATE" not in statements[0]
    with pytest.raises(WorkerAccessLost):
        await DatabaseAuthority(
            vox_env.factory, replace(check.assignment, agent_identity="va-" + uuid4().hex)
        )()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["pause", "logout", "call-end", "generation", "membership", "cleanup", "expired", "erased"],
)
async def test_each_fresh_frame_proof_observes_terminal_and_ownership_changes(vox_env, change):
    intent, check = await prepared(vox_env)
    await confirm(vox_env, intent)
    await check()
    async with vox_env.factory() as database:
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        if change == "pause":
            (await database.get(User, vox_env.user_id)).agent_paused = True
        elif change == "logout":
            (await database.get(UserSession, connection.auth_session_id)).revoked_at = datetime.now(
                UTC
            )
        elif change == "call-end":
            call = await database.get(VoxCall, connection.call_id)
            call.status, call.ended_at, call.end_reason = "ended", datetime.now(UTC), "hang-up"
        elif change == "generation":
            (await database.get(VoxState, connection.owner_scope_id)).generation += 1
        elif change == "membership":
            await database.execute(
                delete(WorkspaceMembership).where(WorkspaceMembership.user_id == vox_env.user_id)
            )
        elif change == "cleanup":
            row = await database.scalar(
                __import__("sqlalchemy")
                .select(VoxMediaCleanup)
                .where(VoxMediaCleanup.connection_id == intent.connection_id)
            )
            row.status, row.requested_at, row.next_attempt_at = (
                "pending",
                datetime.now(UTC),
                datetime.now(UTC),
            )
        elif change == "expired":
            # Preserve immutable media deadlines; expire the login instead.
            (await database.get(UserSession, connection.auth_session_id)).expires_at = datetime.now(
                UTC
            ) - timedelta(seconds=1)
        else:
            await database.delete(connection)
        await database.commit()
    with pytest.raises(WorkerAccessLost):
        await check()


@pytest.mark.asyncio
async def test_exclusive_worker_terminal_path_revokes_exact_epoch_and_queues_cleanup(vox_env):
    intent, check = await prepared(vox_env)
    await confirm(vox_env, intent)
    await check.retire()
    with pytest.raises(WorkerAccessLost):
        await check()
    async with vox_env.factory() as database:
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        assert connection.status == "revoked" and connection.reason == "authority-lost"
        cleanup = await database.scalar(
            __import__("sqlalchemy")
            .select(VoxMediaCleanup)
            .where(VoxMediaCleanup.connection_id == intent.connection_id)
        )
        assert cleanup.status == "pending"


@pytest.mark.asyncio
async def test_voice_answer_denies_shadow_even_when_operator_and_qualified_assignment_allow_it(
    vox_env,
):
    env = vox_env
    intent, check = await prepared(env)
    await confirm(env, intent)
    models = tuple(
        model_for(provider).model_copy(
            update={
                "enabled": True,
                "allowed_sensitivities": frozenset({Sensitivity.PERSONAL}),
                "input_cost_per_million": Decimal("0.1"),
                "output_cost_per_million": Decimal("0.2"),
            }
        )
        for provider in (Provider.OPENAI, Provider.GEMINI)
    )
    template = catalog_template()
    registry = template.model_copy(
        update={
            "models": models,
            "profiles": tuple(
                profile.model_copy(
                    update={
                        "assignments": tuple(model.reference for model in models)
                        if profile.profile == Profile.ASSISTANT_INTERACTIVE
                        else (),
                    }
                )
                for profile in template.profiles
            ),
        }
    )
    async with env.factory() as database:
        await RegistryStore(database).publish(registry, expected_revision=0)
        for model in models:
            assignment_row = await database.get(
                AIProfileAssignment,
                (Profile.ASSISTANT_INTERACTIVE.value, model_key(model.reference)),
            )
            assignment_row.rollout_percent = 100
            assignment_row.shadow_enabled = model.reference.provider == Provider.GEMINI
            await record_evaluation(
                database,
                model_id=model_key(model.reference),
                model_digest=digest(canonical(model)),
                registry_revision=1,
                evidence=EvaluationEvidence(
                    profile=Profile.ASSISTANT_INTERACTIVE,
                    task_type=TaskType.REASON,
                    prompt=CONVERSATION_PROMPT,
                    output_schema=CONVERSATION_SCHEMA,
                    quality=1,
                    reliability=1,
                    p95_latency_ms=10,
                    samples=10,
                    safety_passed=True,
                    evaluated_at=datetime.now(UTC),
                    corpus_version="offline-vox-shadow-fixture",
                ),
            )
        await database.commit()
    grants = tuple(
        ProviderGrant(provider=provider, sensitivities=frozenset({Sensitivity.PERSONAL}))
        for provider in (Provider.OPENAI, Provider.GEMINI)
    )
    operator = PolicyRules(
        grants=grants,
        preferred_provider=Provider.OPENAI,
        max_cost=Decimal("0.01"),
        allow_shadow=True,
    )
    primary = FakeAdapter(Provider.OPENAI, output='{"answer":"Primary voice answer."}')
    shadow = FakeAdapter(Provider.GEMINI, output='{"answer":"Discarded shadow answer."}')
    runtime = GatewayRuntime(
        GatewayStore(env.factory, operator), {Provider.OPENAI: primary, Provider.GEMINI: shadow}
    )
    # Positive control: these are genuinely qualified shadow conditions, not an
    # absent adapter or a failed evaluation that would accidentally mask a leak.
    task = conversation_task(
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        max_cost=operator.max_cost,
        policy=ProviderPolicy(
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            revision=1,
            grants=grants,
            allow_fallback=False,
            max_fallbacks=0,
        ),
    )
    await runtime.execute(
        task,
        context_builder=TransientConversationContext(
            check, env.settings, await check(), "Explain gravity.", ()
        ),
        documents={},
        semantic_validator=lambda value: ConversationAnswer.model_validate(value),
    )
    assert len(primary.calls) == len(shadow.calls) == 1
    primary.calls.clear()
    shadow.calls.clear()
    answer = await PolicyVoiceGateway(runtime, env.settings, check).answer("Explain gravity.", ())
    assert answer == "Primary voice answer."
    assert len(primary.calls) == 1 and not shadow.calls
    assert runtime.store.operator_policy.allow_shadow is True  # Caller policy was not mutated.
    async with env.factory() as database:
        runs = list(
            await database.scalars(
                select(AITaskRun).where(AITaskRun.task_id == primary.calls[0].task_id)
            )
        )
        assert len(runs) == 1 and not runs[0].shadow and runs[0].status == "COMPLETED"
