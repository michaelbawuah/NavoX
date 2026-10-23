"""Configured live-call turn owner preserving NavoX's fresh authority and audio fences."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol

from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline, ProviderUnavailable, UsageHooks
from navox.vox.inference_realtime import OpenAIRealtimeAdapter
from navox.vox.session_context import VoxCallContext
from navox.vox.worker import (
    MAX_TURN_BYTES,
    MIN_VOICE_SAMPLES,
    SAMPLE_WIDTH,
    SILENCE_SAMPLES,
    AudioOutput,
    AuthorityCheck,
    MemoryTurn,
    WorkerAccessLost,
    WorkerAssignment,
    _wav,
    is_voice,
)


class StreamingOutput(AudioOutput, Protocol):
    async def play_pcm(self, pcm: bytes, check: Callable[[], Awaitable[None]]) -> None: ...
    async def finish_pcm(self, check: Callable[[], Awaitable[None]]) -> None: ...


class ConfiguredVoiceWorker:
    def __init__(
        self,
        assignment: WorkerAssignment,
        check: AuthorityCheck,
        output: StreamingOutput,
        config: VoxInferenceConfig,
        context: VoxCallContext,
        realtime: OpenAIRealtimeAdapter,
        pipeline: OpenAICompatiblePipeline,
        *,
        usage: UsageHooks | None = None,
    ):
        self.assignment, self.check, self.output = assignment, check, output
        self.config, self.context = config, context
        self.realtime, self.pipeline = realtime, pipeline
        self.usage = usage
        self._preroll: deque[bytes] = deque(maxlen=10)
        self.context.begin_epoch(assignment.connection_id)
        self.failed = False
        self._closed = False
        self._pcm = bytearray()
        self._voiced = self._silence = 0
        self._last_voice_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._epoch = 0
        self._fallback_until = 0.0
        self._maintenance: asyncio.Task[None] | None = None
        self._maintenance_retry_at = 0.0

    async def _authority(self) -> None:
        current = await self.check()
        if (current.user_id, current.workspace_id) != (
            self.context.key.user_id,
            self.context.key.workspace_id,
        ):
            raise WorkerAccessLost("The voice call owner changed")

    def _history(self) -> tuple[MemoryTurn, ...]:
        return self.context.history(self.assignment.connection_id)

    def _primary(self) -> bool:
        return self.config.mode == "realtime" and time.monotonic() >= self._fallback_until

    async def warm(self) -> None:
        """Explicit admitted call only; opening a socket sends no microphone audio."""
        if self._primary():
            try:
                await self._authority()
                await self.realtime._connect(self._history())
            except ProviderUnavailable:
                if not self.config.fallback_enabled:
                    raise
                await self.realtime.interrupt()
                self._fallback_until = time.monotonic() + 30

    async def maintain(self) -> None:
        if self._maintenance and self._maintenance.done():
            try:
                self._maintenance.result()
            except ProviderUnavailable:
                self._maintenance_retry_at = time.monotonic() + 30
            self._maintenance = None
        if (
            not self._closed
            and not self.failed
            and self._primary()
            and self._task is None
            and not self._pcm
            and self._maintenance is None
            and time.monotonic() >= self._maintenance_retry_at
        ):
            self._maintenance = asyncio.create_task(
                self.realtime.rollover_if_quiet(self._history())
            )

    async def receive(self, participant_identity: str, pcm: bytes) -> None:
        if self._closed or self.failed or participant_identity != self.assignment.client_identity:
            raise WorkerAccessLost("The voice participant is unavailable")
        voiced = is_voice(pcm)
        await self._authority()
        if voiced and self._task is not None:
            await self.interrupt()
        if not voiced and not self._pcm:
            self._preroll.append(pcm)
            return  # At most 200ms preroll; never continuously upload quiet audio.
        starting = not self._pcm
        frames = [*self._preroll, pcm] if starting else [pcm]
        self._preroll.clear()
        self._pcm.extend(b"".join(frames))
        samples = len(pcm) // SAMPLE_WIDTH
        if voiced:
            self._silence = 0
            self._voiced += samples
            self._last_voice_at = time.monotonic()
            if self.usage:
                await self.usage.note_audio(samples * 1000 // 16000, direction="input")
        else:
            self._silence += samples
        # Keep consonants and short pauses through a bounded 600ms hangover.
        if self._primary():
            try:
                await self.realtime.begin_turn(self._history())
                for frame in frames:
                    await self.realtime.append_audio(frame)
            except ProviderUnavailable:
                if not self.config.fallback_enabled:
                    raise
                await self.realtime.interrupt()
                self._fallback_until = time.monotonic() + 30
        if len(self._pcm) >= MAX_TURN_BYTES or self._silence >= SILENCE_SAMPLES:
            self._seal_turn()

    async def flush_idle(self) -> None:
        if self._closed or not self._pcm or self._last_voice_at is None:
            return
        await self._authority()
        if self._last_voice_at is not None and time.monotonic() - self._last_voice_at >= 0.6:
            self._seal_turn()

    def _seal_turn(self) -> None:
        pcm = bytes(self._pcm[:MAX_TURN_BYTES])
        enough = self._voiced >= MIN_VOICE_SAMPLES
        logging.getLogger(__name__).info(
            "Vox input turn sealed",
            extra={"voiced_ms": self._voiced // 16, "pcm_ms": len(pcm) // 32, "eligible": enough},
        )
        self._pcm.clear()
        self._voiced = self._silence = 0
        self._last_voice_at = None
        if enough:
            self._epoch += 1
            self._task = asyncio.create_task(self._respond(pcm, self._epoch))
        else:
            # A short local noise event must not leave a provider input buffer pending.
            self._epoch += 1
            self._task = asyncio.create_task(self._discard_short_turn())

    async def _discard_short_turn(self) -> None:
        try:
            await self.realtime.interrupt()
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _respond(self, pcm: bytes | None, epoch: int, text: str | None = None) -> None:
        async def current() -> None:
            if self._closed or epoch != self._epoch:
                raise WorkerAccessLost("The voice turn was interrupted")
            await self._authority()

        async def emit(frame: bytes) -> None:
            await current()
            await self.output.play_pcm(frame, current)

        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds + 5):
                await current()
                turn = None
                if self._primary():
                    try:
                        if text is not None:
                            await self.realtime.begin_turn(self._history())
                        turn = await self.realtime.respond(emit, text=text)
                        await self.output.finish_pcm(current)
                    except ProviderUnavailable:
                        can_retry = (
                            self.config.fallback_enabled
                            and not self.realtime.output_started
                            and not self.realtime.tool_started
                        )
                        played_ms = getattr(self.output, "played_pcm_ms", lambda: 0)()
                        partial = await self.realtime.interrupt(
                            played_audio_ms=played_ms if self.realtime.output_started else None
                        )
                        self._fallback_until = time.monotonic() + 30
                        if not can_retry:
                            # No repeated half-spoken answer or re-executed tool.
                            await self.output.clear()
                            if partial is not None:
                                await current()
                                await self.context.commit(
                                    self.assignment.connection_id, partial.question, partial.answer
                                )
                            return
                if turn is None:
                    reply = await self.pipeline.respond(
                        _wav(pcm) if pcm is not None else None, self._history(), question=text
                    )
                    await current()
                    await self.output.play(reply.audio, current)
                    turn = MemoryTurn(reply.question, reply.answer)
                await current()
                await self.context.commit(self.assignment.connection_id, turn.question, turn.answer)
                if hasattr(self.realtime, "mark_played"):
                    self.realtime.mark_played()
                logging.getLogger(__name__).info("Vox spoken turn completed")
        except asyncio.CancelledError:
            raise
        except WorkerAccessLost:
            self.failed = not self._closed and epoch == self._epoch
            await self.output.clear()
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Vox spoken turn failed", extra={"error_type": type(error).__name__}
            )
            # No raw provider exception, transcript, tool result or credential is logged.
            self.failed = True
            await self.output.clear()
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def text_turn(self, text: str) -> None:
        """Only server-consumed authenticated wake commands may enter this seam."""
        await self._authority()
        await self.interrupt()
        self._epoch += 1
        self._task = asyncio.create_task(self._respond(None, self._epoch, text))

    async def interrupt(self) -> None:
        self._pcm.clear()
        self._preroll.clear()
        self._voiced = self._silence = 0
        self._last_voice_at = None
        self._epoch += 1
        task, self._task = self._task, None
        played_ms = getattr(self.output, "played_pcm_ms", lambda: 0)()
        if task:
            task.cancel()
        await self.output.clear()
        if task:
            done, _ = await asyncio.wait({task}, timeout=2)
            if not done:
                self._closed = True
                raise WorkerAccessLost("The interrupted voice response did not stop")
            if not task.cancelled():
                task.result()
        partial = await self.realtime.interrupt(played_audio_ms=played_ms if task else None)
        if partial is not None and not self._closed:
            await self._authority()
            await self.context.commit(
                self.assignment.connection_id, partial.question, partial.answer
            )

    async def close(self) -> None:
        self._closed = True
        self._pcm.clear()
        self._last_voice_at = None
        if self._maintenance is not None:
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)
            self._maintenance = None
        try:
            await self.interrupt()
        finally:
            await self.realtime.close()
            self.context.close()
