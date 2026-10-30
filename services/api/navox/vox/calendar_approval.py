"""One-use calendar approval from the current host-observed student turn.

There is deliberately no model-facing approve function. Exact host read-back
must finish playing before a short contextual affirmative can consume it.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from navox.student import service as schedule
from navox.vox.host_turn import HostReply, HostTurnHooks

AFFIRMATIVE = frozenset(
    {
        "yes",
        "yes please",
        "yeah",
        "yeah please",
        "yep",
        "yup",
        "of course",
        "sure",
        "sure please",
        "go ahead",
        "yes go ahead",
        "yeah go ahead",
        "okay",
        "ok",
        "do it",
        "please do",
        "absolutely",
        "yes sure",
        "yeah sure",
        "sure go ahead",
        "of course go ahead",
        "okay go ahead",
        "ok go ahead",
        "yes please do",
        "go ahead please",
    }
)
NEGATIVE = frozenset({"no", "no thanks", "no thank you", "don't", "do not", "cancel", "stop"})
AMBIGUOUS = frozenset({"maybe", "not sure", "i'm not sure", "i guess", "hmm", "probably"})
APPROVAL_SECONDS = 90.0
MAX_READBACK = 600


def normalized_reply(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold().strip().replace("’", "'")
    return " ".join(re.sub(r"[-.,!?;:\u2013\u2014]+", " ", value).split())


@dataclass(frozen=True)
class PendingCalendarAction:
    key: str
    readback: str = field(repr=False)
    validate: Callable[[], Awaitable[None]] = field(repr=False)
    apply: Callable[[], Awaitable[str]] = field(repr=False)
    read_guard: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)


class CalendarApprovalCoordinator(HostTurnHooks):
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._pending: PendingCalendarAction | None = None
        self._queued = False
        self._armed_until: float | None = None
        self._ambiguous = False
        self._followup: str | None = None
        self._invalid_until: float | None = None

    @property
    def needs_input(self) -> bool:
        return self._pending is not None or (
            self._invalid_until is not None and self.clock() < self._invalid_until
        )

    def prepare(self, action: PendingCalendarAction) -> bool:
        self.invalidate("new_proposal")
        if not action.readback or len(action.readback) > MAX_READBACK:
            return False
        self._pending, self._queued = action, True
        return True

    async def after_tool(self) -> HostReply | None:
        action = self._pending
        if action is None or not self._queued:
            return None
        self._queued = False
        # A reviewed study source may have been erased after the tool prepared
        # it. Recheck before releasing cached private fields to the TTS route.
        try:
            await action.validate()
        except schedule.StudentError:
            self.invalidate("source_changed_during_readback")
            return HostReply(
                "That calendar review changed before it could be read. "
                "Please review it again; no new action is confirmed."
            )
        if self._pending is not action:
            return None

        async def authorize() -> None:
            if self._pending is not action:
                raise schedule.StudentError(
                    409, "The exact calendar read-back is no longer current"
                )
            try:
                await (action.read_guard or action.validate)()
            except schedule.StudentError:
                self.invalidate("source_changed_during_readback")
                raise
            if self._pending is not action:
                raise schedule.StudentError(
                    409, "The exact calendar read-back is no longer current"
                )

        return HostReply(action.readback, authorize)

    async def played(self, answer: str) -> None:
        action = self._pending
        if action is None:
            return
        if answer == action.readback:
            try:
                await action.validate()
            except schedule.StudentError:
                self.invalidate("source_changed_during_readback")
                return
            if self._pending is action:
                self._armed_until = self.clock() + APPROVAL_SECONDS
        elif answer == self._followup:
            # A single clarification does not extend the original read-back TTL.
            self._followup = None
        else:
            self.invalidate("different_reply")

    async def input(self, question: str) -> HostReply | None:
        action = self._pending
        decision = normalized_reply(question)
        if action is None:
            if (
                self._invalid_until is not None
                and self.clock() < self._invalid_until
                and decision in AFFIRMATIVE | AMBIGUOUS | NEGATIVE
            ):
                return HostReply(
                    "That calendar review is no longer active. "
                    "Please review it again; no new action is confirmed."
                )
            self._invalid_until = None
            return None
        if decision in NEGATIVE:
            self.invalidate("declined")
            return HostReply("Cancelled this voice approval. No new calendar action was submitted.")
        if self._armed_until is None or self.clock() >= self._armed_until:
            self.invalidate("expired_or_unplayed")
            return HostReply(
                "That voice review is no longer active. Please review the event again."
            )
        if decision in AMBIGUOUS and not self._ambiguous:
            self._ambiguous = True
            self._followup = (
                "Should I apply exactly the calendar action I just read back? Say yes or no."
            )
            return HostReply(self._followup)
        # Refinements, unrelated acknowledgements and compound replies never
        # approve the earlier payload. The model may prepare a new exact review.
        if decision not in AFFIRMATIVE:
            self.invalidate("refined_or_unrelated")
            return None
        # Consume BEFORE any await. Parallel turns/replays cannot apply twice.
        self.invalidate("consumed")
        try:
            await action.validate()
            return HostReply(await action.apply())
        except schedule.StudentError:
            return HostReply(
                "The event or its availability changed. Review it again; "
                "I haven't confirmed a new action."
            )

    def invalidate(self, reason: str) -> None:
        had_review = self._pending is not None or self._invalid_until is not None
        self._invalid_until = (
            self.clock() + APPROVAL_SECONDS
            if had_review
            and reason in {"interrupted", "source_changed_during_readback", "expired_or_unplayed"}
            else None
        )
        self._pending = None
        self._queued = False
        self._armed_until = None
        self._ambiguous = False
        self._followup = None

    async def failed(self) -> None:
        self.invalidate("failed")

    async def close(self) -> None:
        self.invalidate("closed")
