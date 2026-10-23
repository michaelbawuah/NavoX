"""Server-only inference callbacks and monotonic room/activity metering.

Audio activity is a union of server-observed activity windows, so simultaneous
input/output is not counted twice. No PCM, utterance, or provider body is retained.
The transport owner must run heartbeat and call note_audio BEFORE forwarding each
bounded audio frame; this hook alone does not authorize a microphone or speaker.
"""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import TYPE_CHECKING
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select

from navox.db.vox_usage import VoxUsageEvent, VoxUsageStream
from navox.vox import usage
from navox.vox.usage_schemas import UsageCounters, UsageUpdate

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
    def __init__(self, check: DatabaseAuthority):
        self.check = check
        self.factory = check.factory
        self._lock = asyncio.Lock()
        self._transport_id: UUID | None = None
        self._call_id: UUID | None = None
        self._base = UsageCounters()
        self._started = 0.0
        self._last = 0.0
        self._voice_ms = 0.0
        self._windows: list[tuple[float, float]] = []
        self._closed = False

    async def _ensure_transport(self) -> None:
        if self._transport_id is not None:
            return
        authority, intent = await self.check.intent()
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
        self._started = self._last = monotonic()

    def _active_seconds(self, start: float, end: float) -> float:
        cursor, result = start, 0.0
        for a, b in sorted(self._windows):
            a, b = max(a, cursor), min(b, end)
            if a < b:
                result += b - a
                cursor = b
        return result

    async def _meter(self) -> None:
        await self._ensure_transport()
        assert self._transport_id is not None
        now = monotonic()
        self._voice_ms += self._active_seconds(self._last, now) * 1000
        self._last = now
        self._windows = [(a, b) for a, b in self._windows if b > now]
        elapsed = (now - self._started) * 1000
        speaking = int(min(elapsed, self._voice_ms))
        idle = int(max(0, elapsed - self._voice_ms))
        connected = speaking + idle
        counters = UsageCounters(
            speaking_ms=self._base.speaking_ms + speaking,
            idle_ms=self._base.idle_ms + idle,
            connected_ms=self._base.connected_ms + connected,
        )
        async with self.factory() as database:
            row = await database.get(VoxUsageStream, self._transport_id)
            if row is None:
                raise usage.UsageError(409, "Voice usage authority ended")
            if counters != UsageCounters.model_validate(row.cumulative):
                await usage.apply_usage_update(
                    database,
                    self._transport_id,
                    UsageUpdate(event_id=uuid4(), sequence=row.sequence + 1, cumulative=counters),
                )
                await database.commit()

    async def check_budget(self, *, voice_frame_ms: int = 100) -> None:
        """Fresh media authority plus bounded voice capacity before paid I/O/audio."""
        if self._closed or type(voice_frame_ms) is not int or not 0 <= voice_frame_ms <= 60_000:
            raise usage.UsageError(409, "Voice metering ended")
        async with self._lock:
            authority = await self.check()
            await self._meter()
            async with self.factory() as database:
                status = await usage.budget_status(
                    database, authority.user_id, authority.workspace_id
                )
                await database.commit()
            now = monotonic()
            pending = int(
                self._active_seconds(now, max((b for _, b in self._windows), default=now)) * 1000
            )
            if status.blocked or status.remaining_voice_ms < max(voice_frame_ms, pending):
                raise usage.UsageError(429, "Your voice allowance has been reached")

    async def note_audio(self, duration_ms: int) -> None:
        """Call before transmitting a frame, at its actual server playback cadence."""
        if type(duration_ms) is not int or not 1 <= duration_ms <= 1000:
            raise usage.UsageError(422, "Audio metering frame is outside its bound")
        await self.check_budget(voice_frame_ms=duration_ms)
        async with self._lock:
            now = monotonic()
            # A shared monotonic activity interval joins overlapping input/output.
            self._windows.append((now, now + duration_ms / 1000))

    async def begin(self, stream_id: UUID, provider: str, model: str, token_ceiling: int) -> None:
        await self.check_budget()
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
            )
            await database.commit()  # Durable reservation MUST precede external work.
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
            await self.check_budget()
            await asyncio.sleep(1)

    async def close(self) -> None:
        if self._closed:
            return
        async with self._lock:
            try:
                if self._transport_id is not None:
                    await self._meter()
            finally:
                self._closed = True


DatabaseUsageHooks = WorkerUsageHooks
