"""Server-only inference callbacks and monotonic room/activity metering.

Audio activity estimates encoded voiced-frame duration on separate input/output
timelines. Same-direction bursts queue in order; simultaneous directions share
one interval. VAD/activity estimates are not provider invoices or proof of hearing.
No PCM, utterance, or provider body is retained.
The transport owner must run heartbeat and call note_audio BEFORE forwarding each
bounded audio frame; this hook alone does not authorize a microphone or speaker.
"""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select

from navox.db.vox_usage import VoxUsageEvent, VoxUsageStream
from navox.vox import usage
from navox.vox.media import ConnectionIntent
from navox.vox.usage_schemas import UsageCounters, UsageUpdate, VoiceCreditGrant

if TYPE_CHECKING:
    from navox.vox.worker import DatabaseAuthority


def normalize_usage(raw: dict[str, int]) -> tuple[UsageCounters, bool]:
    """Retain total quota counts even when modality/cached-price details are absent."""

    def count(*keys: str) -> int | None:
        found = [raw[k] for k in keys if k in raw]
        if any(type(value) is not int or value < 0 for value in found) or len(set(found)) > 1:
            raise usage.UsageError(422, "Invalid provider usage counters")
        return found[0] if found else None

    input_total = count("input_tokens", "prompt_tokens")
    output_total = count("output_tokens", "completion_tokens")
    complete = input_total is not None and output_total is not None
    values: dict[str, int] = {}
    for side, total in (("input", input_total), ("output", output_total)):
        audio = count(side + "_audio_tokens")
        text = count(side + "_text_tokens")
        audio, text = audio or 0, text or 0
        if total is None:
            total = audio + text
        if audio + text > total:
            raise usage.UsageError(422, "Provider modality usage exceeds its total")
        values[side + "_audio"] = audio
        values[side + "_text"] = text
        values["unclassified_" + side] = total - audio - text
    cached_text = count("input_cached_text_tokens")
    cached_audio = count("input_cached_audio_tokens")
    cached = count("input_cached_tokens")
    if cached is None:
        cached = (cached_text or 0) + (cached_audio or 0)
    known_input = (
        input_total if input_total is not None else values["input_audio"] + values["input_text"]
    )
    if cached > known_input:
        raise usage.UsageError(422, "Provider cached usage exceeds input")
    if cached:
        if (
            cached_text is not None
            and cached_audio is not None
            and cached_text + cached_audio == cached
        ):
            if cached_text > values["input_text"] or cached_audio > values["input_audio"]:
                raise usage.UsageError(422, "Cached modality exceeds its input")
            values["cached_text"], values["cached_audio"] = cached_text, cached_audio
        elif (
            values["input_audio"] and not values["input_text"] and not values["unclassified_input"]
        ):
            values["cached_audio"] = cached
        elif (
            values["input_text"] and not values["input_audio"] and not values["unclassified_input"]
        ):
            values["cached_text"] = cached
        else:
            # The cached split across audio/text is unknown. Preserve exact total
            # quota while refusing to invent a modality-specific dollar estimate.
            values["unclassified_input"] = known_input
            values["input_audio"] = values["input_text"] = 0
    total = count("total_tokens")
    counters = UsageCounters.model_validate(values)
    if total is not None and total != counters.total_tokens:
        complete = False
    return counters, complete


class WorkerUsageHooks:
    """One-second durable voice credits; media authority remains fresh per frame."""

    def __init__(self, check: DatabaseAuthority):
        self.check = check
        self.factory = check.factory
        self._lock = asyncio.Lock()
        self._transport_id: UUID | None = None
        self._call_id: UUID | None = None
        self._intent: ConnectionIntent | None = None
        self._worker_nonce = uuid4()
        self._base = UsageCounters()
        self._started = self._last = self._last_flush = 0.0
        self._voice_ms = 0.0
        self._windows: list[tuple[float, float]] = []
        self._direction_ends = {"input": 0.0, "output": 0.0}
        self._grant: VoiceCreditGrant | None = None
        self._grant_deadline = 0.0
        self._grant_voice_base = 0.0
        self._pending_settlement: tuple[VoiceCreditGrant, UsageCounters] | None = None
        self._closed = False

    async def _ensure_transport(self) -> None:
        if self._transport_id is not None:
            return
        authority, intent = await self.check.intent()
        self._intent = intent
        self._call_id = intent.reservation_id
        self._transport_id = uuid5(intent.reservation_id, "vox-transport-usage-v1")
        async with self.factory() as database:
            view = await usage.start_usage_stream(
                database,
                authority.user_id,
                authority.workspace_id,
                intent.reservation_id,
                stream_id=self._transport_id,
            )
            await database.commit()
        self._base = view.cumulative
        self._started = self._last = self._last_flush = monotonic()

    def _active_seconds(self, start: float, end: float) -> float:
        cursor, result = start, 0.0
        for a, b in sorted(self._windows):
            a, b = max(a, cursor), min(b, end)
            if a < b:
                result += b - a
                cursor = b
        return result

    def _advance(self, now: float) -> None:
        self._voice_ms += self._active_seconds(self._last, now) * 1000
        self._last = now
        self._windows = [(max(a, now), b) for a, b in self._windows if b > now]

    def _counters(self, now: float) -> UsageCounters:
        elapsed = max(0.0, (now - self._started) * 1000)
        speaking = int(min(elapsed, self._voice_ms) + 0.000001)
        idle = int(max(0.0, elapsed - self._voice_ms) + 0.000001)
        return UsageCounters(
            speaking_ms=self._base.speaking_ms + speaking,
            idle_ms=self._base.idle_ms + idle,
            connected_ms=self._base.connected_ms + speaking + idle,
        )

    async def _flush(self) -> None:
        """Caller holds hooks lock. A failed/uncertain commit retains its exact retry."""
        assert self._transport_id is not None
        if self._pending_settlement is not None:
            grant, counters = self._pending_settlement
            async with self.factory() as database:
                await usage.settle_voice_credit(database, grant, cumulative=counters)
                await database.commit()
            self._pending_settlement = None
            self._grant = None
        # Do not refund queued activity, or turn an unknown future grant into
        # speech. Current RTC output queue is 20 ms; the API admits at most 1000 ms.
        end = max((b for _, b in self._windows), default=monotonic())
        delay = max(0.0, end - monotonic())
        if delay:
            await asyncio.sleep(min(1.0, delay))
        now = monotonic()
        if now + 0.000001 < end:
            raise usage.UsageError(409, "Voice activity has not reached its flush boundary")
        self._advance(now)
        counters = self._counters(now)
        async with self.factory() as database:
            if self._grant is not None:
                self._pending_settlement = self._grant, counters
                await usage.settle_voice_credit(database, self._grant, cumulative=counters)
            else:
                row = await database.get(VoxUsageStream, self._transport_id)
                if row is None:
                    raise usage.UsageError(409, "Voice usage authority ended")
                if counters != UsageCounters.model_validate(row.cumulative):
                    await usage.apply_usage_update(
                        database,
                        self._transport_id,
                        UsageUpdate(
                            event_id=uuid4(), sequence=row.sequence + 1, cumulative=counters
                        ),
                    )
            await database.commit()
        self._pending_settlement = None
        self._grant = None
        self._last_flush = now

    async def _reserve(self) -> None:
        assert self._transport_id is not None and self._intent is not None
        authority, intent = await self.check.intent()
        if (intent.connection_id, intent.reservation_id, intent.generation) != (
            self._intent.connection_id,
            self._intent.reservation_id,
            self._intent.generation,
        ):
            raise usage.UsageError(409, "Voice credit epoch changed")
        requested = monotonic()
        async with self.factory() as database:
            grant = await usage.reserve_voice_credit(
                database,
                authority.user_id,
                authority.workspace_id,
                intent.reservation_id,
                stream_id=self._transport_id,
                connection_id=intent.connection_id,
                generation=intent.generation,
                worker_nonce=self._worker_nonce,
                grant_id=uuid4(),
            )
            await database.commit()  # Durable shared-budget debit precedes any audio.
        snapshot = self._counters(self._last_flush)
        values = self._base.model_dump()
        for name in ("speaking_ms", "idle_ms", "connected_ms"):
            delta = getattr(grant.base_cumulative, name) - getattr(snapshot, name)
            if delta < 0:
                raise usage.UsageError(409, "Voice transport checkpoint changed")
            values[name] += delta
        self._base = UsageCounters.model_validate(values)
        self._grant = grant
        # Start conservatively before the DB request, never after its reply.
        self._grant_deadline = requested + (grant.expires_at - grant.granted_at).total_seconds()
        self._grant_voice_base = self._voice_ms
        await self.check()  # Revoke/logout during a lock wait cannot admit a frame.

    async def check_budget(self, *, voice_frame_ms: int = 0, force: bool = True) -> None:
        if type(voice_frame_ms) is not int or not 0 <= voice_frame_ms <= 1000:
            raise usage.UsageError(422, "Voice metering frame is outside its bound")
        async with self._lock:
            if self._closed:
                raise usage.UsageError(409, "Voice metering ended")
            await self.check()
            await self._ensure_transport()
            now = monotonic()
            if (
                force
                or self._grant is None
                or now >= self._grant_deadline
                or now - self._last_flush >= 1
            ):
                await self._flush()
                await self._reserve()
            if self._grant is None or self._grant.granted_ms < voice_frame_ms:
                raise usage.UsageError(429, "Your voice allowance has been reached")

    async def note_audio(
        self, duration_ms: int, *, direction: Literal["input", "output"] = "input"
    ) -> None:
        """Fresh authority per frame; consume only this worker's committed credit."""
        if (
            type(duration_ms) is not int
            or not 1 <= duration_ms <= 1000
            or direction not in {"input", "output"}
        ):
            raise usage.UsageError(422, "Audio metering frame is outside its bound")
        async with self._lock:
            if self._closed:
                raise usage.UsageError(409, "Voice metering ended")
            await self.check()
            await self._ensure_transport()
            for attempt in range(3):
                now = monotonic()
                self._advance(now)
                start = max(now, self._direction_ends[direction])
                end = start + duration_ms / 1000
                if self._grant is not None and end <= self._grant_deadline + 0.000001:
                    self._windows.append((start, end))
                    projected = (
                        self._voice_ms
                        - self._grant_voice_base
                        + self._active_seconds(now, max(b for _, b in self._windows)) * 1000
                    )
                    if projected <= self._grant.granted_ms + 0.000001:
                        self._direction_ends[direction] = end
                        return
                    self._windows.pop()
                if attempt == 2:
                    break
                if self._grant is not None and self._grant.granted_ms < duration_ms:
                    boundary = min(self._grant.day_end, self._grant.month_end)
                    # A clipped local-period grant may wait for at most this frame.
                    if (
                        self._grant.expires_at != boundary
                        or self._grant_deadline - now > duration_ms / 1000
                    ):
                        raise usage.UsageError(429, "Your voice allowance has been reached")
                    await asyncio.sleep(max(0, self._grant_deadline - now))
                await self._flush()
                await self._reserve()
            raise usage.UsageError(429, "Your voice allowance has been reached")

    async def begin(self, stream_id: UUID, provider: str, model: str, token_ceiling: int) -> None:
        async with self._lock:
            if self._closed:
                raise usage.UsageError(409, "Voice metering ended")
            await self.check()
            await self._ensure_transport()
            await self._flush()
            await self._reserve()
            assert self._grant is not None and self._intent is not None
            authority, intent = await self.check.intent()
            async with self.factory() as database:
                await usage.admit_provider_stream(
                    database,
                    authority.user_id,
                    authority.workspace_id,
                    intent.reservation_id,
                    stream_id=stream_id,
                    provider=provider,
                    model=model,
                    token_ceiling=token_ceiling,
                    voice_credit=self._grant,
                    voice_connection_id=intent.connection_id,
                    voice_worker_nonce=self._worker_nonce,
                )
                await database.commit()
            await self.check()

    async def finish(self, stream_id: UUID, raw: dict[str, int], complete: bool) -> None:
        # Billing already incurred can be recorded after media revocation. No new
        # grant/provider I/O is performed, and missing/deleted owners still fence it.
        async with self._lock, self.factory() as database:
            row = await database.get(VoxUsageStream, stream_id)
            if row is None:
                raise usage.UsageError(404, "Usage stream not found")
            if row.call_id != self._call_id or row.kind != "provider":
                raise usage.UsageError(403, "Usage stream belongs to another call")
            counters, all_counts_known = normalize_usage(raw)
            if raw:
                event_id = uuid5(stream_id, counters.model_dump_json())
                previous = await database.scalar(
                    select(VoxUsageEvent.id).where(
                        VoxUsageEvent.stream_id == stream_id, VoxUsageEvent.event_id == event_id
                    )
                )
                if previous is None:
                    await usage.apply_usage_update(
                        database,
                        stream_id,
                        UsageUpdate(
                            event_id=event_id, sequence=row.sequence + 1, cumulative=counters
                        ),
                    )
            await usage.finish_usage_stream(
                database, stream_id, usage_complete=complete and all_counts_known
            )
            await database.commit()

    async def heartbeat(self) -> None:
        while not self._closed:
            await self.check_budget(force=False)
            await asyncio.sleep(1)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            try:
                if self._transport_id is not None:
                    await self._flush()
            finally:
                self._closed = True


DatabaseUsageHooks = WorkerUsageHooks
