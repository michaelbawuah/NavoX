"""Bounded off-record voice turns behind an exact, freshly checked media epoch.

This module does not connect RTC by itself. The production bridge is in
``worker_livekit``; tests can replace only its audio transport and model boundary.
Conversation bodies and PCM live in this process only. Existing gateway traces
remain content-free and existing grants, evaluations and speech quotas still apply.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import struct
import wave
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from navox.ai.context import ContextDenied, MinimizedContext, reject_credentials
from navox.ai.conversation import (
    CONVERSATION_PROMPT,
    CONVERSATION_SCHEMA,
    MAX_CONVERSATION_HISTORY,
    ConversationAnswer,
    conversation_task,
)
from navox.ai.foundation.contracts import (
    AITask,
    JSONDocument,
    Profile,
    ProviderPolicy,
    Sensitivity,
    TaskType,
)
from navox.ai.runtime import GatewayRuntime
from navox.ai.speech_service import MAX_AUDIO_MILLISECONDS, bounded_transcript, transcribe_audio
from navox.ai.speech_synthesis import bounded_speech_text, synthesize_speech
from navox.ai.store import GatewayStore
from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.models import User, UserSession, Workspace, WorkspaceMembership
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.intelligence.contracts import SourceDocument
from navox.vox import media

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
MAX_FRAME_BYTES = SAMPLE_RATE * SAMPLE_WIDTH // 10  # At most 100 ms per authority check.
MAX_TURN_BYTES = SAMPLE_RATE * SAMPLE_WIDTH * MAX_AUDIO_MILLISECONDS // 1000
SILENCE_SAMPLES = SAMPLE_RATE * 600 // 1000
MIN_VOICE_SAMPLES = SAMPLE_RATE * 200 // 1000
VOICE_RMS = 0.012
MAX_SESSION_TURNS = 20


class WorkerAccessLost(PermissionError):
    """The assigned epoch no longer authorizes receiving or speaking."""


@dataclass(frozen=True)
class WorkerAssignment:
    connection_id: UUID
    room_name: str
    client_identity: str
    agent_identity: str

    @classmethod
    def from_metadata(cls, value: str) -> WorkerAssignment:
        """Dispatch metadata names only an epoch, never an owner or grant."""
        if not isinstance(value, str) or len(value.encode()) > 1024:
            raise WorkerAccessLost("Invalid voice assignment")
        try:
            decoded = json.loads(value)
            if not isinstance(decoded, dict) or set(decoded) != {
                "connection_id",
                "room_name",
                "client_identity",
                "agent_identity",
            }:
                raise ValueError
            if not all(isinstance(item, str) for item in decoded.values()):
                raise ValueError
            epoch = UUID(decoded["connection_id"])
            assignment = cls(
                epoch, decoded["room_name"], decoded["client_identity"], decoded["agent_identity"]
            )
        except (ValueError, TypeError, KeyError):
            raise WorkerAccessLost("Invalid voice assignment") from None
        # Deriving these locally also rejects a valid UUID joined to another room.
        if (assignment.room_name, assignment.client_identity, assignment.agent_identity) != (
            "vx-" + epoch.hex,
            "vc-" + epoch.hex,
            "va-" + epoch.hex,
        ):
            raise WorkerAccessLost("Invalid voice assignment")
        return assignment


@dataclass(frozen=True)
class WorkerAuthority:
    user_id: UUID
    workspace_id: UUID


class AuthorityCheck(Protocol):
    async def __call__(self) -> WorkerAuthority: ...


class DatabaseAuthority:
    """One fresh joined proof per input/output frame, without retained row locks.

    The SELECT observes one current database snapshot and its database clock.
    Admission and terminal writers retain their existing User serialization;
    realtime read checks never keep a lock or transaction across network I/O.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        assignment: WorkerAssignment,
        connection: AsyncConnection | None = None,
    ):
        self.factory, self.assignment, self.connection = factory, assignment, connection
        self._connection_lock = asyncio.Lock()
        self._connection_failed = False

    async def __call__(self) -> WorkerAuthority:
        authority, _ = await self.intent()
        return authority

    async def intent(
        self, *, allow_preparing: bool = False
    ) -> tuple[WorkerAuthority, media.ConnectionIntent]:
        try:
            async with asyncio.timeout(1), self._connection_lock:
                if (
                    self._connection_failed
                    or self.connection is not None
                    and (self.connection.closed or self.connection.invalidated)
                ):
                    raise WorkerAccessLost("The exclusive voice worker connection ended")
                return await self._intent(allow_preparing=allow_preparing)
        except (SQLAlchemyError, TimeoutError):
            # A broken physical session must never transparently reconnect and
            # keep serving after another worker has acquired its lost lock.
            self._connection_failed = True
            raise WorkerAccessLost("The exclusive voice worker connection ended") from None

    async def _intent(
        self, *, allow_preparing: bool
    ) -> tuple[WorkerAuthority, media.ConnectionIntent]:
        async with (
            self.factory(bind=self.connection) if self.connection is not None else self.factory()
        ) as database:
            clock = (
                func.clock_timestamp()
                if database.get_bind().dialect.name == "postgresql"
                else func.current_timestamp()
            )
            states = ("confirmed", "preparing") if allow_preparing else ("confirmed",)
            row = (
                await database.execute(
                    select(VoxMediaConnection, VoxCall, UserSession.expires_at, clock)
                    .select_from(VoxMediaConnection)
                    .join(User, User.id == VoxMediaConnection.user_id)
                    .join(UserSession, UserSession.id == VoxMediaConnection.auth_session_id)
                    .join(VoxCall, VoxCall.id == VoxMediaConnection.call_id)
                    .join(VoxState, VoxState.id == VoxMediaConnection.owner_scope_id)
                    .join(Workspace, Workspace.id == VoxMediaConnection.workspace_id)
                    .join(
                        WorkspaceMembership,
                        (WorkspaceMembership.workspace_id == Workspace.id)
                        & (WorkspaceMembership.user_id == User.id),
                    )
                    .join(VoxMediaCleanup, VoxMediaCleanup.connection_id == VoxMediaConnection.id)
                    .where(
                        VoxMediaConnection.id == self.assignment.connection_id,
                        VoxMediaConnection.status.in_(states),
                        VoxMediaConnection.room_name == self.assignment.room_name,
                        VoxMediaConnection.client_identity == self.assignment.client_identity,
                        VoxMediaConnection.agent_identity == self.assignment.agent_identity,
                        User.agent_paused.is_(False),
                        UserSession.user_id == User.id,
                        UserSession.client_type == "web",
                        UserSession.revoked_at.is_(None),
                        VoxCall.status == "reserved",
                        VoxCall.auth_session_id == UserSession.id,
                        VoxCall.user_id == User.id,
                        VoxCall.workspace_id == Workspace.id,
                        VoxCall.generation == VoxMediaConnection.generation,
                        VoxState.user_id == User.id,
                        VoxState.workspace_id == Workspace.id,
                        VoxState.generation == VoxMediaConnection.generation,
                        WorkspaceMembership.role == "owner",
                        Workspace.workspace_type == "personal",
                        VoxMediaCleanup.status == "armed",
                        VoxMediaCleanup.owner_scope_id == VoxState.id,
                        VoxMediaCleanup.room_name == VoxMediaConnection.room_name,
                        VoxMediaCleanup.client_identity == VoxMediaConnection.client_identity,
                        VoxMediaCleanup.agent_identity == VoxMediaConnection.agent_identity,
                        VoxMediaCleanup.connection_expires_at == VoxMediaConnection.expires_at,
                        VoxMediaCleanup.created_at == VoxMediaConnection.created_at,
                    )
                )
            ).one_or_none()
            if row is None:
                raise WorkerAccessLost("The voice connection has ended")
            connection, call, session_expires_at, checked_at = row
            now = utc(checked_at)
            if (
                utc(connection.expires_at) <= now
                or utc(call.expires_at) <= now
                or utc(connection.expires_at) > min(utc(call.expires_at), utc(session_expires_at))
            ):
                raise WorkerAccessLost("The voice connection has ended")
            return (
                WorkerAuthority(connection.user_id, connection.workspace_id),
                media._intent(connection, call),
            )

    async def retire(self) -> None:
        """Retire only through the physical session that still owns the worker lock.

        A stale loser never borrows a pooled connection to retire its successor.
        The DB operation itself must fail on loss of the original lock session.
        """
        try:
            async with asyncio.timeout(2), self._connection_lock:
                if (
                    self._connection_failed
                    or self.connection is not None
                    and (self.connection.closed or self.connection.invalidated)
                ):
                    return
                await self._retire()
        except (SQLAlchemyError, TimeoutError):
            self._connection_failed = True

    async def _retire(self) -> None:
        async with (
            self.factory(bind=self.connection) if self.connection is not None else self.factory()
        ) as database:
            initial = await database.get(VoxMediaConnection, self.assignment.connection_id)
            if initial is None:
                return  # The independent outbox is rescued by the reconciler.
            await database.scalar(
                select(User)
                .where(User.id == initial.user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            connection = await database.get(
                VoxMediaConnection, self.assignment.connection_id, populate_existing=True
            )
            if connection is not None and (
                connection.room_name,
                connection.client_identity,
                connection.agent_identity,
            ) == (
                self.assignment.room_name,
                self.assignment.client_identity,
                self.assignment.agent_identity,
            ):
                await media._revoke(
                    database, connection, await database_now(database), "authority-lost"
                )
                await database.commit()


@dataclass(frozen=True)
class MemoryTurn:
    question: str
    answer: str


class TransientConversationContext:
    """Server-origin memory only; no retained AssistantSession entitlement."""

    def __init__(
        self,
        check: AuthorityCheck,
        settings: Settings,
        authority: WorkerAuthority,
        question: str,
        history: tuple[MemoryTurn, ...],
    ):
        self.check, self.settings, self.authority = check, settings, authority
        self.question, self.history = bounded_transcript(question), history

    async def build(
        self, task: AITask, documents: Mapping[UUID, SourceDocument], *, user_request: str = ""
    ) -> MinimizedContext:
        current = await self.check()
        if (
            current != self.authority
            or (
                task.user_id,
                task.workspace_id,
                task.profile,
                task.task_type,
                task.prompt,
                task.output_schema,
                task.sensitivity,
            )
            != (
                current.user_id,
                current.workspace_id,
                Profile.ASSISTANT_INTERACTIVE,
                TaskType.REASON,
                CONVERSATION_PROMPT,
                CONVERSATION_SCHEMA,
                Sensitivity.PERSONAL,
            )
            or task.context_references
            or documents
            or user_request
        ):
            raise ContextDenied("Voice conversation scope changed")
        from navox.ai.features import configured_secrets

        text = json.dumps(
            {
                "schema_version": "assistant-conversation-input.v1",
                "question": self.question,
                "recent_conversation": [
                    {"question": turn.question, "answer": turn.answer}
                    for turn in self.history[-MAX_CONVERSATION_HISTORY:]
                ],
            },
            ensure_ascii=False,
        )
        reject_credentials(text, configured_secrets(self.settings))
        return MinimizedContext(
            workspace_id=current.workspace_id,
            user_id=current.user_id,
            source_ids=(),
            sensitivity=Sensitivity.PERSONAL,
            content=JSONDocument(text=text),
        )


class VoiceGateway(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...
    async def answer(self, question: str, history: tuple[MemoryTurn, ...]) -> str: ...
    async def synthesize(self, answer: str) -> bytes: ...


class PolicyVoiceGateway:
    """Real existing gateway calls, with no alternate keys, models or grants."""

    def __init__(self, runtime: GatewayRuntime, settings: Settings, check: AuthorityCheck):
        self.runtime, self.settings, self.check = runtime, settings, check

    async def transcribe(self, audio: bytes) -> str:
        authority = await self.check()
        value = await transcribe_audio(
            self.runtime,
            self.settings,
            workspace_id=authority.workspace_id,
            user_id=authority.user_id,
            audio=audio,
        )
        if await self.check() != authority:
            raise WorkerAccessLost("Voice authority changed")
        return value

    async def answer(self, question: str, history: tuple[MemoryTurn, ...]) -> str:
        authority = await self.check()
        ceiling = self.runtime.store.operator_policy
        task = conversation_task(
            workspace_id=authority.workspace_id,
            user_id=authority.user_id,
            max_cost=ceiling.max_cost,
            policy=ProviderPolicy(
                workspace_id=authority.workspace_id,
                user_id=authority.user_id,
                revision=1,
                grants=ceiling.grants,
                allow_fallback=False,
                max_fallbacks=0,
            ),
        )
        context = TransientConversationContext(
            self.check, self.settings, authority, question, history
        )

        def validate(value: Any) -> None:
            from navox.ai.features import configured_secrets

            text = bounded_speech_text(ConversationAnswer.model_validate(value).answer)
            reject_credentials(text, configured_secrets(self.settings))

        # Do not call for_conversation: that entitlement requires a native retained
        # session. Ordinary operator/workspace/user grants remain the ceiling here.
        # Off-record voice never adds a shadow recipient, even if a broader
        # operator policy and another qualified profile assignment allow one.
        runtime = GatewayRuntime(
            GatewayStore(
                self.runtime.store.factory,
                ceiling.model_copy(update={"allow_shadow": False}),
            ),
            self.runtime.adapters,
        )
        result = await runtime.execute(
            task, context_builder=context, documents={}, semantic_validator=validate
        )
        if await self.check() != authority:
            raise WorkerAccessLost("Voice authority changed")
        value = ConversationAnswer.model_validate_json(result.output.text)
        validate(value.model_dump())
        return value.answer

    async def synthesize(self, answer: str) -> bytes:
        authority = await self.check()
        audio = await synthesize_speech(
            self.runtime,
            self.settings,
            workspace_id=authority.workspace_id,
            user_id=authority.user_id,
            text=answer,
        )
        if await self.check() != authority:
            raise WorkerAccessLost("Voice authority changed")
        return audio


class AudioOutput(Protocol):
    async def play(self, mp3: bytes, check: Callable[[], Awaitable[None]]) -> None:
        """Check before each bounded PCM frame; stop queueing immediately on failure."""
        ...

    async def clear(self) -> None: ...


def _wav(pcm: bytes) -> bytes:
    handle = io.BytesIO()
    with wave.open(handle, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(SAMPLE_WIDTH)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(pcm)
    return handle.getvalue()


def is_voice(pcm: bytes) -> bool:
    if not pcm or len(pcm) > MAX_FRAME_BYTES or len(pcm) % SAMPLE_WIDTH:
        raise ValueError("Voice frames must be at most 100 ms of 16 kHz mono PCM16")
    squares = sum(sample[0] ** 2 for sample in struct.iter_unpack("<h", pcm))
    return math.sqrt(squares / (len(pcm) // 2)) / 32768 >= VOICE_RMS


class VoiceTurnWorker:
    """One receiving turn and one cancellable provider/output attempt at a time."""

    def __init__(
        self,
        assignment: WorkerAssignment,
        check: AuthorityCheck,
        gateway: VoiceGateway,
        output: AudioOutput,
    ):
        self.assignment, self.check, self.gateway, self.output = assignment, check, gateway, output
        self._pcm = bytearray()
        self._silence = self._voiced = self._epoch = self._turns = 0
        self._history: tuple[MemoryTurn, ...] = ()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self.failed = False
        self._last_voice_at: float | None = None

    async def receive(self, participant_identity: str, pcm: bytes) -> None:
        if self._closed or self.failed or participant_identity != self.assignment.client_identity:
            raise WorkerAccessLost("The voice participant is unavailable")
        voiced = is_voice(pcm)
        await self.check()
        if voiced and self._task is not None:
            await self.interrupt()
        if not voiced and not self._pcm:
            return
        self._pcm.extend(pcm)
        samples = len(pcm) // SAMPLE_WIDTH
        if voiced:
            self._silence = 0
            self._voiced += samples
            self._last_voice_at = asyncio.get_running_loop().time()
        else:
            self._silence += samples
        if len(self._pcm) >= MAX_TURN_BYTES or self._silence >= SILENCE_SAMPLES:
            self._seal_turn()

    async def flush_idle(self) -> None:
        """A VAD-muted upstream may stop frames entirely after speech ends."""
        if self._closed or not self._pcm or self._last_voice_at is None:
            return
        await self.check()
        # Check again after awaiting DB authority: a fresh voiced frame may have
        # arrived concurrently with this watchdog tick.
        if self._last_voice_at is not None and (
            asyncio.get_running_loop().time() - self._last_voice_at >= 0.6
        ):
            self._seal_turn()

    def _seal_turn(self) -> None:
        pcm_turn = bytes(self._pcm[:MAX_TURN_BYTES])
        enough = self._voiced >= MIN_VOICE_SAMPLES
        self._pcm.clear()
        self._voiced = self._silence = 0
        self._last_voice_at = None
        if enough:
            if self._turns >= MAX_SESSION_TURNS:
                raise WorkerAccessLost("The bounded voice turn limit was reached")
            self._turns += 1
            self._epoch += 1
            self._task = asyncio.create_task(self._respond(pcm_turn, self._epoch))

    async def _respond(self, pcm: bytes, epoch: int) -> None:
        async def still_current() -> None:
            if self._closed or epoch != self._epoch:
                raise WorkerAccessLost("The voice turn was interrupted")
            await self.check()

        try:
            async with asyncio.timeout(90):
                await still_current()
                question = bounded_transcript(await self.gateway.transcribe(_wav(pcm)))
                await still_current()
                answer = bounded_speech_text(await self.gateway.answer(question, self._history))
                await still_current()
                audio = await self.gateway.synthesize(answer)
                await still_current()
                await self.output.play(audio, still_current)
                await still_current()
                self._history = (*self._history, MemoryTurn(question, answer))[
                    -MAX_CONVERSATION_HISTORY:
                ]
        except asyncio.CancelledError:
            raise
        except WorkerAccessLost:
            if not self._closed and epoch == self._epoch:
                self.failed = True
            await self.output.clear()
        except Exception:
            # No provider exception, transcript or answer is logged or persisted.
            # Stop any queued output; the next utterance needs fresh authority.
            self.failed = True
            await self.output.clear()
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def interrupt(self) -> None:
        self._epoch += 1
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        await self.output.clear()
        if task is not None:
            done, _ = await asyncio.wait({task}, timeout=2)
            if not done:
                self._closed = True
                self._pcm.clear()
                self._history = ()
                raise WorkerAccessLost("The interrupted voice turn did not stop")
            if not task.cancelled():
                task.result()

    async def close(self) -> None:
        self._closed = True
        self._pcm.clear()
        self._history = ()
        self._last_voice_at = None
        await self.interrupt()
