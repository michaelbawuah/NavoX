"""Regression coverage for microphone backlog and idle/admission races."""

import asyncio
import struct

import pytest

from navox.vox.audio_input import MAX_PENDING_FRAMES, AudioInputBuffer
from navox.vox.worker import WorkerAccessLost


@pytest.mark.asyncio
async def test_speech_survives_slow_admission_without_premature_idle_flush():
    buffer = AudioInputBuffer()
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    received, flushed = [], []
    frames = [struct.pack("<h", i + 1) * 320 for i in range(150)]

    async def receive(frame):
        received.append(frame)
        if len(received) == 1:
            entered.set()
            await release.wait()
        if len(received) == len(frames):
            finished.set()

    async def flush():
        flushed.append(True)

    task = asyncio.create_task(buffer.consume(receive))
    try:
        buffer.put(frames[0])
        await asyncio.wait_for(entered.wait(), 1)
        await buffer.flush_idle(flush)
        for frame in frames[1:]:
            buffer.put(frame)
        await buffer.flush_idle(flush)
        assert flushed == []
        release.set()
        await asyncio.wait_for(finished.wait(), 1)
        assert received == frames
        await buffer.flush_idle(flush)
        assert flushed == [True]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_disconnect_discards_even_a_frame_waiting_for_the_input_lock():
    buffer = AudioInputBuffer()
    received = []

    async def receive(frame):
        received.append(frame)

    async with buffer.lock:
        buffer.put(bytes(640))
        task = asyncio.create_task(buffer.consume(receive))
        await asyncio.sleep(0)
        buffer.discard()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert received == []


def test_overflow_fails_explicitly_instead_of_silently_losing_words():
    buffer = AudioInputBuffer()
    for _ in range(MAX_PENDING_FRAMES):
        buffer.put(bytes(640))
    with pytest.raises(WorkerAccessLost, match="fell behind"):
        buffer.put(bytes(640))
    assert buffer._queue.empty()


@pytest.mark.parametrize("frame", [b"", bytes(639), bytes(642), bytes(64000)])
def test_frame_bound_is_checked_before_buffering(frame):
    with pytest.raises(WorkerAccessLost, match="size changed"):
        AudioInputBuffer().put(frame)
