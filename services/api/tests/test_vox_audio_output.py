"""Audio continuity under scheduling jitter, without network or recorded speech."""

import asyncio
import math
import struct

import av
import pytest

from navox.vox.worker import WorkerAccessLost
from navox.vox.worker_livekit import OUTPUT_QUEUE_MS, LiveKitAudioOutput


def tone(samples=48000, frequency=440):
    return struct.pack(
        f"<{samples}h",
        *(int(6000 * math.sin(2 * math.pi * frequency * i / 24000)) for i in range(samples)),
    )


class Clock:
    now = 0.0


class TimedSource:
    """A deterministic real-time drain with the RTC source's bounded backpressure."""

    def __init__(self, clock, capacity_ms):
        self.clock = clock
        self.capacity = capacity_ms / 1000
        self.end = clock.now
        self.frames = []
        self.gaps = []
        self.maximum_queue = 0.0
        self.cleared = 0

    @property
    def queued_duration(self):
        return max(0.0, self.end - self.clock.now)

    async def capture_frame(self, frame):
        if self.frames and self.clock.now - self.end > 0.002:
            self.gaps.append(self.clock.now - self.end)
        duration = frame.samples_per_channel / frame.sample_rate
        self.clock.now += max(0.0, self.queued_duration + duration - self.capacity)
        self.end = max(self.end, self.clock.now) + duration
        self.frames.append(bytes(frame.data))
        self.maximum_queue = max(self.maximum_queue, self.queued_duration)

    async def wait_for_playout(self):
        self.clock.now = max(self.clock.now, self.end)

    def clear_queue(self):
        self.end = self.clock.now
        self.cleared += 1


def resampled(pcm):
    frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
    frame.sample_rate = 24000
    frame.planes[0].update(pcm)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    return b"".join(
        bytes(f.planes[0])[: f.samples * 2]
        for f in [*resampler.resample(frame), *resampler.resample(None)]
    )


@pytest.mark.asyncio
async def test_bounded_output_absorbs_proof_jitter_without_losing_or_reordering_samples():
    pcm = tone()
    results = []
    for capacity in (20, OUTPUT_QUEUE_MS):
        clock = Clock()
        source = TimedSource(clock, capacity)
        output = LiveKitAudioOutput(source)
        proofs = 0

        async def check(clock=clock):
            nonlocal proofs
            proofs += 1
            clock.now += 0.080 if proofs % 25 == 0 else 0.002

        for offset in range(0, len(pcm), 4800):
            await output.play_pcm(pcm[offset : offset + 4800], check)
        await output.finish_pcm(check)
        assert b"".join(source.frames) == resampled(pcm)
        assert all(0 < len(frame) <= 1920 for frame in source.frames)
        assert proofs == len(source.frames) + 1
        assert source.maximum_queue <= capacity / 1000 + 1e-9
        results.append(source.gaps)
    assert len(results[0]) >= 3  # Regression reproduces the previous starvation.
    assert results[1] == []
    assert OUTPUT_QUEUE_MS <= 250  # Remain a short, bounded speech queue.


@pytest.mark.asyncio
async def test_interruption_counts_only_submitted_audio_and_discards_buffered_tail():
    clock = Clock()
    source = TimedSource(clock, OUTPUT_QUEUE_MS)
    output = LiveKitAudioOutput(source)
    waiting = asyncio.Event()
    release = asyncio.Event()
    checks = 0

    async def check():
        nonlocal checks
        checks += 1
        if checks == 2:
            waiting.set()
            await release.wait()

    task = asyncio.create_task(output.play_pcm(tone(2400), check))
    await waiting.wait()
    # Only the first 20ms was submitted. The rest of this 100ms provider chunk
    # must not be treated as heard when truncating the interrupted conversation.
    assert output.played_pcm_ms() == 0
    clock.now += 0.010
    assert 9 <= output.played_pcm_ms() <= 10
    task.cancel()
    await output.clear()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.queued_duration == 0
    assert source.cleared == 1
    assert len(source.frames) == 1
    assert output.played_pcm_ms() == 0
    source.frames.clear()

    async def current():
        pass

    replacement = tone(2400, frequency=880)
    await output.play_pcm(replacement, current)
    await output.finish_pcm(current)
    assert b"".join(source.frames) == resampled(replacement)


@pytest.mark.asyncio
async def test_revoked_proof_prevents_next_frame_and_clear_stops_queued_speech():
    source = TimedSource(Clock(), OUTPUT_QUEUE_MS)
    output = LiveKitAudioOutput(source)
    checks = 0

    async def check():
        nonlocal checks
        checks += 1
        if checks == 5:
            raise WorkerAccessLost("Synthetic revoked epoch")

    with pytest.raises(WorkerAccessLost):
        await output.play_pcm(tone(2400), check)
    assert len(source.frames) == 4
    assert source.queued_duration > 0
    # Both worker implementations clear the source on failed authority.
    await output.clear()
    assert source.queued_duration == 0
    assert output.played_pcm_ms() == 0
