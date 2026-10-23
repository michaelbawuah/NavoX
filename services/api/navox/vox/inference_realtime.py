"""One persistent server-side OpenAI-compatible Realtime socket per active Vox call.

RTC stays owned by worker_livekit. Local VAD supplies PCM frames; only explicit commits
generate replies. A quiet-boundary rollover restores heard text, never a new greeting.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import av
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from navox.core.settings import Settings
from navox.vox.inference_config import VoxInferenceConfig, route_credential
from navox.vox.inference_http import (
    INSTRUCTIONS,
    Check,
    ProviderRejected,
    ProviderUnavailable,
    ReadTools,
    UsageHooks,
    bounded_text,
    usage_counts,
)
from navox.vox.worker import MemoryTurn

MAX_EVENT_BYTES = 1_048_576
MAX_INPUT_BYTES = 960_000
MAX_CONTEXT_TOKENS = 128_000


class Socket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


class NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception | str:
        return exc  # Operator allowlists never authorize a redirected host/path.


async def open_socket(url: str, headers: dict[str, str]) -> Socket:
    logger = logging.getLogger("navox.vox.socket")
    logger.disabled = True  # Protocol debug logs must never receive audio or headers.
    return await NoRedirectConnect(
        url,
        additional_headers=headers,
        proxy=None,
        compression=None,
        max_size=MAX_EVENT_BYTES,
        max_queue=8,
        open_timeout=10,
        close_timeout=2,
        ping_interval=20,
        ping_timeout=20,
        logger=logger,
    )


def pcm24(frame: bytes, resampler: av.AudioResampler) -> bytes:
    source = av.AudioFrame(format="s16", layout="mono", samples=len(frame) // 2)
    source.sample_rate = 16_000
    source.planes[0].update(frame)
    return b"".join(
        bytes(value.planes[0])[: value.samples * 2] for value in resampler.resample(source)
    )


class OpenAIRealtimeAdapter:
    def __init__(
        self,
        config: VoxInferenceConfig,
        settings: Settings,
        check: Check,
        usage: UsageHooks,
        tools: ReadTools | None = None,
        *,
        connector: Callable[[str, dict[str, str]], Awaitable[Socket]] = open_socket,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config, self.settings, self.check, self.usage = config, settings, check, usage
        self.tools, self.connector, self.clock = tools, connector, clock
        self.socket: Socket | None = None
        self.opened_at = 0.0
        self._lock = asyncio.Lock()
        self._resampler: av.AudioResampler | None = None
        self._input_bytes = 0
        self._context_ceiling = 0
        self._stream: UUID | None = None
        self._transcription_stream: UUID | None = None
        self._question = ""
        self._question_item: str | None = None
        self._response_id: str | None = None
        self._output_item_id: str | None = None
        self._reply_complete = False
        self._heard_revision = 0
        self._cancelled_stream: UUID | None = None
        self.output_started = False
        self.tool_started = False

    async def _send(self, event: dict[str, Any]) -> None:
        await self.check()
        if self.socket is None:
            raise ProviderUnavailable("The realtime connection is unavailable")
        try:
            await self.socket.send(json.dumps(event, separators=(",", ":"), ensure_ascii=False))
        except (ConnectionClosed, OSError):
            raise ProviderUnavailable("The realtime connection is unavailable") from None

    async def _event(self) -> dict[str, Any]:
        await self.check()
        if self.socket is None:
            raise ProviderUnavailable("The realtime connection is unavailable")
        try:
            raw = await self.socket.recv()
            if not isinstance(raw, str) or len(raw.encode()) > MAX_EVENT_BYTES:
                raise ProviderRejected("The realtime event is outside its bound")
            event = json.loads(raw)
        except (ConnectionClosed, OSError):
            raise ProviderUnavailable("The realtime connection is unavailable") from None
        except (ValueError, RecursionError):
            raise ProviderRejected("The realtime event is invalid") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ProviderRejected("The realtime event is invalid")
        if event["type"] == "error":
            error = event.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            if code in {"server_error", "service_unavailable", "rate_limit_exceeded"}:
                raise ProviderUnavailable("The realtime provider is temporarily unavailable")
            raise ProviderRejected("The realtime provider rejected this request")
        await self.check()
        return event

    async def _connect(self, history: tuple[MemoryTurn, ...]) -> None:
        if self.socket is not None:
            return
        route = self.config.realtime
        parsed = urlsplit(route.base_url)
        url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                parsed.path + "/realtime",
                urlencode({"model": route.model}),
                "",
            )
        )
        key = route_credential(route, self.settings)
        await self.check()
        try:
            self.socket = await self.connector(
                url, {"Authorization": "Bearer " + key.get_secret_value()}
            )
        except InvalidStatus as exc:
            if exc.response.status_code in {408, 429, 500, 502, 503, 504}:
                raise ProviderUnavailable("The realtime provider is unavailable") from None
            raise ProviderRejected("The realtime provider rejected the connection") from None
        except (OSError, TimeoutError):
            raise ProviderUnavailable("The realtime connection is unavailable") from None
        self.opened_at = self.clock()
        try:
            async with asyncio.timeout(10):
                await self._send(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "model": route.model,
                            "instructions": INSTRUCTIONS,
                            "output_modalities": ["audio"],
                            "max_output_tokens": self.config.max_output_tokens,
                            "truncation": "disabled",
                            # Realtime rejects Chat/Responses' strict tool field.
                            # The server still validates arguments before execution.
                            "tools": [
                                {key: value for key, value in tool.items() if key != "strict"}
                                for tool in self.tools.definitions()
                            ]
                            if self.tools
                            else [],
                            "tool_choice": "auto" if self.tools else "none",
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcm", "rate": 24000},
                                    "turn_detection": None,
                                    "transcription": {"model": self.config.stt.model},
                                },
                                "output": {
                                    "format": {"type": "audio/pcm", "rate": 24000},
                                    "voice": route.voice,
                                },
                            },
                        },
                    }
                )
                for _ in range(20):
                    if (await self._event())["type"] == "session.updated":
                        break
                else:
                    raise ProviderRejected("The realtime session was not acknowledged")
                self._context_ceiling = len(INSTRUCTIONS.encode()) + 2048
                for turn in history:
                    for role, value in (("user", turn.question), ("assistant", turn.answer)):
                        self._context_ceiling += len(value.encode()) + 64
                        if self._context_ceiling > MAX_CONTEXT_TOKENS:
                            raise ProviderRejected("The call context requires compaction")
                        await self._send(
                            {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": role,
                                    "content": [
                                        {
                                            "type": "input_text"
                                            if role == "user"
                                            else "output_text",
                                            "text": value,
                                        }
                                    ],
                                },
                            }
                        )
        except BaseException:
            await self.close()
            raise

    async def rollover_if_quiet(self, history: tuple[MemoryTurn, ...]) -> None:
        """Warm replacement independently; never block or discard a new speech onset."""
        if (
            self.socket is None
            or self._stream is not None
            or self._response_id is not None
            or self.clock() - self.opened_at < self.config.rollover_seconds
        ):
            return
        revision = self._heard_revision
        replacement = OpenAIRealtimeAdapter(
            self.config,
            self.settings,
            self.check,
            self.usage,
            self.tools,
            connector=self.connector,
            clock=self.clock,
        )
        old = None
        try:
            await replacement._connect(history)
            async with self._lock:
                if (
                    self._stream is not None
                    or self._response_id is not None
                    or revision != self._heard_revision
                ):
                    return
                await self.check()
                old, self.socket = self.socket, replacement.socket
                replacement.socket = None
                self.opened_at = replacement.opened_at
                self._context_ceiling = replacement._context_ceiling
        finally:
            await replacement.close()
            if old is not None:
                try:
                    async with asyncio.timeout(2):
                        await old.close()
                except Exception:
                    pass

    async def begin_turn(self, history: tuple[MemoryTurn, ...]) -> None:
        """Only called for local voice or an explicit authenticated text command."""
        async with self._lock:
            if self._stream is not None:
                return
            await self._connect(history)
            ceiling = self._context_ceiling + 3000 + self.config.max_output_tokens
            if ceiling > MAX_CONTEXT_TOKENS:
                raise ProviderRejected("The call context requires compaction")
            self._stream = uuid4()
            self._transcription_stream = uuid4()
            await self.usage.begin(
                self._stream, self.config.realtime.provider, self.config.realtime.model, ceiling
            )
            await self.usage.begin(
                self._transcription_stream,
                self.config.stt.provider,
                self.config.stt.model,
                3000 + self.config.max_output_tokens,
            )
            self._input_bytes = 0
            self._question = ""
            self._question_item = None
            self._resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
            self.output_started = self.tool_started = False
            self._response_id = self._output_item_id = None
            self._reply_complete = False

    async def append_audio(self, frame: bytes) -> None:
        if self._stream is None or self._resampler is None:
            raise ProviderRejected("The realtime input turn was not started")
        if not frame or len(frame) % 2 or len(frame) > 3200:
            raise ProviderRejected("The realtime frame is outside its bound")
        self._input_bytes += len(frame)
        if self._input_bytes > MAX_INPUT_BYTES:
            raise ProviderRejected("The realtime utterance is too long")
        converted = pcm24(frame, self._resampler)
        if converted:
            await self._send(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(converted).decode("ascii"),
                }
            )

    async def _transcript(self, event: dict[str, Any]) -> None:
        if event.get("item_id") != self._question_item:
            return
        if event["type"] == "conversation.item.input_audio_transcription.completed":
            self._question = bounded_text(event.get("transcript"))
            if self._transcription_stream is not None:
                counts = usage_counts(event.get("usage"))
                await self.usage.finish(self._transcription_stream, counts, bool(counts))
                self._transcription_stream = None
        elif event["type"] == "conversation.item.input_audio_transcription.failed":
            raise ProviderRejected("The realtime question could not be transcribed")

    async def respond(
        self, emit: Callable[[bytes], Awaitable[None]], *, text: str | None = None
    ) -> MemoryTurn:
        """Commit local input and stream bounded PCM; same socket stays open afterward."""
        answer = ""
        answer_parts: list[str] = []
        audio_bytes = 0
        tools_seen: set[str] = set()
        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds):
                if text is not None:
                    self._question = bounded_text(text)
                    await self._send(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": self._question}],
                            },
                        }
                    )
                    if self._transcription_stream:
                        await self.usage.finish(self._transcription_stream, {}, True)
                        self._transcription_stream = None
                else:
                    if self._input_bytes < 6400:
                        raise ProviderRejected("The realtime utterance was too short")
                    if self._resampler:
                        tail = b"".join(
                            bytes(f.planes[0])[: f.samples * 2]
                            for f in self._resampler.resample(None)
                        )
                        if tail:
                            await self._send(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(tail).decode("ascii"),
                                }
                            )
                    await self._send({"type": "input_audio_buffer.commit"})
                    for _ in range(100):
                        event = await self._event()
                        if event["type"] == "input_audio_buffer.committed":
                            self._question_item = bounded_text(event.get("item_id"), 200)
                            break
                    else:
                        raise ProviderRejected("The realtime input was not acknowledged")
                turn_id = uuid4().hex
                for _ in range(4):
                    await self._send(
                        {
                            "type": "response.create",
                            "response": {"metadata": {"navox_turn": turn_id}},
                        }
                    )
                    response_id = None
                    pending_tools: list[dict[str, Any]] = []
                    while True:
                        event = await self._event()
                        kind = event["type"]
                        if kind.startswith("conversation.item.input_audio_transcription."):
                            await self._transcript(event)
                            continue
                        if kind == "response.created":
                            response = event.get("response", {})
                            if response.get("metadata", {}).get("navox_turn") == turn_id:
                                response_id = response.get("id")
                                self._response_id = response_id
                                self._reply_complete = False
                            continue
                        if response_id is None:
                            continue
                        if kind == "response.done":
                            response = event.get("response", {})
                            if response.get("id") != response_id:
                                continue
                            counts = usage_counts(response.get("usage"))
                            if "input_tokens" in counts and "output_tokens" in counts:
                                self._context_ceiling = (
                                    counts["input_tokens"] + counts["output_tokens"] + 2048
                                )
                            if self._stream:
                                await self.usage.finish(self._stream, counts, bool(counts))
                                self._stream = None
                            self._reply_complete = True
                            if response.get("status") != "completed":
                                raise ProviderUnavailable("The realtime reply did not complete")
                            break
                        if event.get("response_id") != response_id:
                            continue
                        if kind == "response.output_audio.delta":
                            try:
                                raw = base64.b64decode(event.get("delta", ""), validate=True)
                            except (ValueError, binascii.Error):
                                raise ProviderRejected("The realtime audio is invalid") from None
                            audio_bytes += len(raw)
                            if (
                                not raw
                                or len(raw) % 2
                                or audio_bytes > 48000 * self.config.max_response_audio_seconds
                            ):
                                raise ProviderRejected("The realtime audio exceeded its bound")
                            item_id = event.get("item_id")
                            if isinstance(item_id, str):
                                self._output_item_id = item_id
                            await self.check()
                            self.output_started = True  # Conservative before first speaker write.
                            await emit(raw)
                        elif kind == "response.output_audio_transcript.done":
                            answer_parts.append(bounded_text(event.get("transcript")))
                            answer = bounded_text(" ".join(answer_parts), 8000)
                        elif kind == "response.output_item.done":
                            item = event.get("item", {})
                            if item.get("type") == "function_call":
                                pending_tools.append(item)
                    if not pending_tools:
                        if not answer or not audio_bytes:
                            raise ProviderRejected("The realtime reply contained no spoken answer")
                        # Transcription can finish just after audio generation.
                        for _ in range(100):
                            if self._question:
                                break
                            await self._transcript(await self._event())
                        if not self._question:
                            raise ProviderRejected(
                                "The realtime question transcript is unavailable"
                            )
                        return MemoryTurn(self._question, answer)
                    if not self.tools or len(pending_tools) != 1:
                        raise ProviderRejected("The realtime tool request is outside its bound")
                    item = pending_tools[0]
                    call_id = bounded_text(item.get("call_id"), 200)
                    if call_id in tools_seen:
                        raise ProviderRejected("The realtime tool repeated its request")
                    tools_seen.add(call_id)
                    arguments = json.loads(bounded_text(item.get("arguments"), 4096))
                    if not isinstance(arguments, dict):
                        raise ProviderRejected("The realtime tool arguments are invalid")
                    self.tool_started = True
                    await self.check()
                    observed = await self.tools.execute(item.get("name", ""), arguments)
                    await self.check()
                    tool_text = json.dumps(observed, ensure_ascii=False)
                    if len(tool_text.encode()) > 32768:
                        raise ProviderRejected("The schedule result is too large")
                    await self._send(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": tool_text,
                            },
                        }
                    )
                    self._context_ceiling += len(tool_text.encode()) + 64
                    self._stream = uuid4()
                    await self.usage.begin(
                        self._stream,
                        self.config.realtime.provider,
                        self.config.realtime.model,
                        self._context_ceiling + 3000 + self.config.max_output_tokens,
                    )
                raise ProviderRejected("The realtime tool limit was reached")
        except (ConnectionClosed, OSError, TimeoutError):
            raise ProviderUnavailable("The realtime provider is temporarily unavailable") from None
        finally:
            if self._stream is not None and self._response_id is not None:
                self._cancelled_stream = self._stream
            for stream in (self._stream, self._transcription_stream):
                if stream is not None:
                    await self.usage.finish(stream, {}, False)
            self._stream = self._transcription_stream = None

    def mark_played(self) -> None:
        self._heard_revision += 1
        self._response_id = self._output_item_id = None
        self.output_started = False

    async def interrupt(self, played_audio_ms: int | None = None) -> MemoryTurn | None:
        pending = (self._stream, self._transcription_stream)
        self._stream = self._transcription_stream = None
        for stream in pending:
            if stream is not None:
                await self.usage.finish(stream, {}, False)
        if self.socket is None:
            return None
        partial: MemoryTurn | None = None
        try:
            async with asyncio.timeout(2):
                if self._response_id and not self._reply_complete:
                    await self._send({"type": "response.cancel", "response_id": self._response_id})
                if self._output_item_id and played_audio_ms is not None:
                    item_id = self._output_item_id
                    await self._send(
                        {
                            "type": "conversation.item.truncate",
                            "item_id": item_id,
                            "content_index": 0,
                            "audio_end_ms": max(0, played_audio_ms),
                        }
                    )
                    retrieved = False
                    for _ in range(100):
                        event = await self._event()
                        kind = event["type"]
                        if kind.startswith("conversation.item.input_audio_transcription."):
                            await self._transcript(event)
                        if (
                            kind == "conversation.item.truncated"
                            and event.get("item_id") == item_id
                        ):
                            await self._send(
                                {"type": "conversation.item.retrieve", "item_id": item_id}
                            )
                        if kind == "conversation.item.retrieved":
                            item = event.get("item", {})
                            if item.get("id") != item_id:
                                continue
                            fragments = [
                                content.get("transcript") or content.get("text")
                                for content in item.get("content", [])
                                if isinstance(content, dict)
                            ]
                            # Only the server's post-truncate transcript is a heard-prefix source.
                            prefix = " ".join(
                                value for value in fragments if isinstance(value, str)
                            )
                            if self._question and played_audio_ms > 0:
                                partial = MemoryTurn(
                                    self._question,
                                    bounded_text(prefix, 8000)
                                    if prefix.strip()
                                    else "[Interrupted reply; transcript unknown.]",
                                )
                            retrieved = True
                            break
                    if not retrieved:
                        raise ProviderUnavailable("The interrupted reply was not acknowledged")
                elif self._response_id and played_audio_ms is not None:
                    # No identifiable audio item: discard uncertain provider context.
                    raise ProviderUnavailable("The interrupted audio item is unavailable")
                await self._send({"type": "input_audio_buffer.clear"})
                self.mark_played()
                return partial
        except Exception:
            await self.close()
            if self._question and played_audio_ms is not None:
                return MemoryTurn(
                    self._question,
                    "[The spoken reply was interrupted. Its unplayed remainder was not retained.]",
                )
            return None

    async def close(self) -> None:
        socket, self.socket = self.socket, None
        self._resampler = None
        if socket is not None:
            try:
                async with asyncio.timeout(2):
                    await socket.close()
            except Exception:
                pass
