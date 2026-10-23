"""Bounded, transient microphone buffering while provider admission is pending."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from navox.vox.worker import WorkerAccessLost

# Five seconds of 20 ms, mono PCM16 frames: 160 KB, never persisted.
MAX_PENDING_FRAMES = 250


class AudioInputBuffer:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self._queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(MAX_PENDING_FRAMES)
        self._processing = False
        self._generation = 0

    def put(self, pcm: bytes) -> None:
        if len(pcm) != 640:
            raise WorkerAccessLost("The microphone frame size changed")
        try:
            self._queue.put_nowait((self._generation, pcm))
        except asyncio.QueueFull:
            # Do not silently drop words or replay an arbitrarily old backlog.
            self.discard()
            raise WorkerAccessLost("Microphone processing fell behind") from None

    def discard(self) -> None:
        self._generation += 1
        while not self._queue.empty():
            self._queue.get_nowait()

    async def consume(self, receive: Callable[[bytes], Awaitable[None]]) -> None:
        try:
            while True:
                generation, pcm = await self._queue.get()
                self._processing = True
                try:
                    async with self.lock:
                        if generation == self._generation:
                            await receive(pcm)
                finally:
                    self._processing = False
        finally:
            self.discard()

    async def flush_idle(self, flush: Callable[[], Awaitable[None]]) -> None:
        # Check both sides of the lock: getting a frame and acquiring the lock
        # are separate operations. A pending admission is not student silence.
        if self._processing or not self._queue.empty():
            return
        async with self.lock:
            if not self._processing and self._queue.empty():
                await flush()
