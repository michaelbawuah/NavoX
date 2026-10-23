"""Bounded, off-record heard conversation shared only by the same verified call.

Storage adapters own encryption, active-call TTL and fresh DB ownership. This module
never serializes audio and never derives an owner from model or browser input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from navox.vox.worker import MemoryTurn, WorkerAccessLost

MAX_TURN_CHARACTERS = 8_000
MAX_CONTEXT_CHARACTERS = 512_000


@dataclass(frozen=True)
class CallContextKey:
    user_id: UUID
    workspace_id: UUID
    call_id: UUID
    generation: int

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, UUID) for value in (self.user_id, self.workspace_id, self.call_id)
            )
            or type(self.generation) is not int
            or self.generation < 0
        ):
            raise ValueError("Invalid call context identity")


@dataclass(frozen=True)
class ContextSnapshot:
    revision: int = 0
    turns: tuple[MemoryTurn, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("Invalid call context revision")
        validate_turns(self.turns, MAX_CONTEXT_CHARACTERS)


class ContextStore(Protocol):
    async def load(self, key: CallContextKey) -> ContextSnapshot: ...
    async def compare_and_set(
        self, key: CallContextKey, expected_revision: int, turns: tuple[MemoryTurn, ...]
    ) -> bool: ...
    async def delete(self, key: CallContextKey) -> None: ...


def validate_turns(turns: tuple[MemoryTurn, ...], maximum: int) -> None:
    if not isinstance(turns, tuple):
        raise ValueError("Call context must be immutable")
    size = 0
    for turn in turns:
        if not isinstance(turn, MemoryTurn):
            raise ValueError("Invalid call context turn")
        for value in (turn.question, turn.answer):
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_TURN_CHARACTERS:
                raise ValueError("Call context turn is outside its bound")
            size += len(value)
    if size > maximum:
        # Never silently discard heard conversation; rollover/compaction is explicit.
        raise ValueError("Call context reached its configured limit")


class VoxCallContext:
    def __init__(
        self,
        key: CallContextKey,
        store: ContextStore,
        snapshot: ContextSnapshot,
        *,
        max_characters: int = 400_000,
    ):
        if not 1000 <= max_characters <= MAX_CONTEXT_CHARACTERS:
            raise ValueError("Invalid call context limit")
        validate_turns(snapshot.turns, max_characters)
        self.key, self.store = key, store
        self._snapshot = snapshot
        self.max_characters = max_characters
        self._epoch: UUID | None = None
        self._closed = False

    @classmethod
    async def load(
        cls, key: CallContextKey, store: ContextStore, *, max_characters: int = 400_000
    ) -> VoxCallContext:
        return cls(key, store, await store.load(key), max_characters=max_characters)

    def begin_epoch(self, epoch: UUID) -> None:
        if self._closed or not isinstance(epoch, UUID):
            raise WorkerAccessLost("The call context has ended")
        self._epoch = epoch

    def history(self, epoch: UUID) -> tuple[MemoryTurn, ...]:
        if self._closed or epoch != self._epoch:
            raise WorkerAccessLost("The call context epoch changed")
        return self._snapshot.turns

    async def commit(self, epoch: UUID, question: str, answer: str) -> None:
        turns = (*self.history(epoch), MemoryTurn(question, answer))
        validate_turns(turns, self.max_characters)
        revision = self._snapshot.revision
        if not await self.store.compare_and_set(self.key, revision, turns):
            raise WorkerAccessLost("The call context changed")
        # An epoch may have been retired while the storage operation was in flight.
        if self._closed or self._epoch != epoch:
            raise WorkerAccessLost("The call context epoch changed")
        self._snapshot = ContextSnapshot(revision + 1, turns)

    def close(self) -> None:
        """Release this worker's memory; terminal ownership cleanup purges storage."""
        self._closed = True
        self._epoch = None
        self._snapshot = ContextSnapshot()


class InMemoryContextStore:
    """Explicit non-durable test seam; never the production reconnect store."""

    def __init__(self) -> None:
        self._values: dict[CallContextKey, ContextSnapshot] = {}

    async def load(self, key: CallContextKey) -> ContextSnapshot:
        return self._values.get(key, ContextSnapshot())

    async def compare_and_set(
        self, key: CallContextKey, expected_revision: int, turns: tuple[MemoryTurn, ...]
    ) -> bool:
        current = self._values.get(key, ContextSnapshot())
        if current.revision != expected_revision:
            return False
        self._values[key] = ContextSnapshot(expected_revision + 1, turns)
        return True

    async def delete(self, key: CallContextKey) -> None:
        self._values.pop(key, None)
