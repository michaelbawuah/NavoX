"""Host-owned notice and conservative receipts for private recalled speech.

The coordinator retains identifiers and delivery state, never transcript text.
Its caller plays the fixed notice through the already configured, metered TTS
route. A failed notice never authorizes private response audio.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.conversation_memory import MemoryConversation
from navox.memory import history
from navox.memory.extraction import (
    MAX_AUTOMATIC_ROWS,
    mark_followup_exposure,
    mark_responded_followups,
    release_unexposed_followups,
)
from navox.memory.service import lock_owner
from navox.vox.host_turn import HostTurnHooks
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost, WorkerAuthority

PRIVACY_NOTICE = "This reply may include details from your private saved conversation."


class RecallDeliveryCoordinator(HostTurnHooks):
    def __init__(
        self,
        check: DatabaseAuthority,
        settings: Settings,
        *,
        user_id: UUID,
        workspace_id: UUID,
        context_key: str,
        notice_available: bool,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.check, self.settings = check, settings
        self.user_id, self.workspace_id = user_id, workspace_id
        self.context_key, self.notice_available = context_key, notice_available
        self.clock = clock
        self._generation: int | None = None
        self._source_ids: set[UUID] = set()
        self._claim_ids: set[UUID] = set()
        self._unexposed_ids: set[UUID] = set()
        self._exposed_ids: set[UUID] = set()
        self._notice_needed = False
        self._notice_prepared = False
        self._notice_acked = False
        self._boundary_needed = False
        self._closed = False

    @property
    def needs_output_boundary(self) -> bool:
        return self._closed or self._boundary_needed

    def observe(self, value: dict[str, Any], *, generation: int) -> dict[str, Any]:
        """Called inside the owner-locked read, before its context is released."""
        if self._closed or (self._generation is not None and self._generation != generation):
            raise WorkerAccessLost("The recalled-memory context changed")
        self._generation = generation
        result = {key: item for key, item in value.items() if key != "_followup_claim_ids"}
        if not self.notice_available:
            return {
                **result,
                "threads": [],
                "past_context": [],
                "spoken_privacy_warning_required": False,
                "protected_memory_withheld": True,
                "message": (
                    "Private saved conversation is unavailable for speech. "
                    "Use private History instead; do not infer its contents."
                ),
            }
        if value.get("status") != "ok":
            return result
        sources: set[UUID] = set()
        claims: set[UUID] = set()
        try:
            for kind in ("past_context", "threads"):
                for item in value.get(kind, []):
                    sources.add(UUID(item["source_id"]))
            claims.update(UUID(item) for item in value.get("_followup_claim_ids", []))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise WorkerAccessLost("The recalled-memory result is invalid") from None
        if (
            len(sources | self._source_ids) > MAX_AUTOMATIC_ROWS
            or len(claims | self._claim_ids) > MAX_AUTOMATIC_ROWS
        ):
            raise WorkerAccessLost("The recalled-memory context exceeded its bound")
        self._source_ids.update(sources)
        self._claim_ids.update(claims)
        self._unexposed_ids.update(claims - self._exposed_ids)
        if sources and not self._notice_acked:
            self._notice_needed = True
        if sources:
            self._boundary_needed = True
        return result

    async def _authority(self) -> WorkerAuthority:
        current = await self.check()
        if current != WorkerAuthority(self.user_id, self.workspace_id):
            raise WorkerAccessLost("The recalled-memory owner changed")
        return current

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        if self._closed or self._generation is None:
            raise WorkerAccessLost("The recalled-memory context is unavailable")
        authority = await self._authority()
        async with self.check.factory() as database:
            await lock_owner(database, self.user_id)
            privacy = await history.privacy_row(database, self.user_id, self.workspace_id)
            if (
                privacy is None
                or not privacy.history_enabled
                or privacy.off_the_record
                or privacy.disclosure_version != 1
                or privacy.generation != self._generation
            ):
                raise WorkerAccessLost("The recalled-memory privacy state changed")
            if self._source_ids:
                current_sources = set(
                    await database.scalars(
                        select(MemoryConversation.id).where(
                            *history.owner_scope(
                                MemoryConversation, self.user_id, self.workspace_id
                            ),
                            MemoryConversation.id.in_(self._source_ids),
                            MemoryConversation.erased_at.is_(None),
                            MemoryConversation.encrypted_summary.is_not(None),
                            (
                                MemoryConversation.expires_at.is_(None)
                                | (MemoryConversation.expires_at > history.utc(self.clock()))
                            ),
                        )
                    )
                )
                if current_sources != self._source_ids:
                    raise WorkerAccessLost("The recalled-memory sources changed")
            yield database
            if self._closed:
                raise WorkerAccessLost("The recalled-memory context ended")
            await database.commit()
        if await self._authority() != authority:
            raise WorkerAccessLost("The recalled-memory owner changed")

    async def before_output(self) -> str | None:
        if self._closed:
            raise WorkerAccessLost("The recalled-memory context ended")
        if not self._source_ids:
            return None
        async with self._session():
            pass
        if self._notice_needed:
            self._notice_prepared = True
            return PRIVACY_NOTICE
        return None

    async def notice_played(self, notice: str) -> None:
        if not self._notice_prepared or notice != PRIVACY_NOTICE or not self._notice_needed:
            raise WorkerAccessLost("The private-memory notice was not completed")
        async with self._session():
            pass
        self._notice_prepared = self._notice_needed = False
        self._notice_acked = True

    async def output_started(self) -> None:
        if self._closed:
            raise WorkerAccessLost("The recalled-memory context ended")
        if not self._source_ids:
            return
        if not self._notice_acked or self._notice_needed:
            raise WorkerAccessLost("The private-memory notice is required before output")
        expected = frozenset(self._unexposed_ids)
        async with self._session() as database:
            if expected:
                assert self._generation is not None
                exposed = await mark_followup_exposure(
                    database,
                    self.settings,
                    self.user_id,
                    self.workspace_id,
                    context_key=self.context_key,
                    generation=self._generation,
                    thread_ids=expected,
                    now=history.utc(self.clock()),
                )
                if exposed != expected:
                    raise WorkerAccessLost("The saved follow-up claim changed")
        self._exposed_ids.update(expected)
        self._unexposed_ids.difference_update(expected)
        self._boundary_needed = False

    async def played(self, answer: str) -> None:
        if not self._exposed_ids or self._closed:
            return
        async with self._session() as database:
            assert self._generation is not None
            await mark_responded_followups(
                database,
                self.settings,
                self.user_id,
                self.workspace_id,
                answer,
                now=history.utc(self.clock()),
                context_key=self.context_key,
                generation=self._generation,
            )

    def invalidate(self, reason: str) -> None:
        # An interrupted notice grants nothing. Previously completed notices
        # remain valid for the same verified generation; claims stay reserved.
        self._notice_prepared = False
        self._boundary_needed = bool(self._source_ids)
        if reason == "closed":
            self._closed = True

    async def failed(self) -> None:
        # A released context must never publish old provider state afterward.
        self._closed = True
        self._notice_prepared = False
        if self._generation is None or not self._claim_ids:
            return
        # Media authority is normally gone at close. Cleanup uses only the
        # captured owner, exact claim identifiers, context and privacy generation.
        async with self.check.factory() as database:
            await release_unexposed_followups(
                database,
                self.settings,
                self.user_id,
                self.workspace_id,
                context_key=self.context_key,
                generation=self._generation,
                thread_ids=frozenset(self._claim_ids),
                now=history.utc(self.clock()),
            )
            await database.commit()

    async def close(self) -> None:
        await self.failed()
