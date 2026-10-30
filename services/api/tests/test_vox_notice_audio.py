"""Synthetic speaker clock and required-notice failure boundaries."""

import asyncio
import io
import struct

import av
import httpx
import pytest
from test_vox_host_turn import worker_parts
from test_vox_inference import Check, Hooks, Output, Socket, settings

from navox.vox.host_turn import HostTurnHooks
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline
from navox.vox.inference_realtime import OpenAIRealtimeAdapter
from navox.vox.worker import WorkerAccessLost
from navox.vox.worker_livekit import LiveKitAudioOutput


def synthetic_mp3():
    data = io.BytesIO()
    with av.open(data, mode="w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=48_000)
        frame = av.AudioFrame(format="s16p", layout="mono", samples=4800)
        frame.sample_rate = 48_000
        frame.planes[0].update(struct.pack("<h", 900) * 4800)
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return data.getvalue()


class Source:
    queued_duration = 0.0

    def __init__(self):
        self.output = None
        self.notice_clocks = []
        self.clear_during_notice = False

    def clear_queue(self):
        pass

    async def wait_for_playout(self):
        pass

    async def capture_frame(self, frame):
        if self.output._notice_playing:
            self.notice_clocks.append(self.output.played_pcm_ms())
            if self.clear_during_notice:
                self.clear_during_notice = False
                await self.output.clear()
                raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_mid_realtime_notice_preserves_provider_clock_resampler_and_sample_limit():
    source = Source()
    output = source.output = LiveKitAudioOutput(source)

    async def check():
        pass

    await output.play_pcm(struct.pack("<h", 1000) * 2400, check)
    before = (output._pcm_samples, output._pcm_decoded_samples, output._pcm_resampler)
    clock = output.played_pcm_ms()
    assert clock > 0
    await output.play_notice(synthetic_mp3(), check)
    assert source.notice_clocks and set(source.notice_clocks) == {clock}
    assert output.played_pcm_ms() == clock
    assert (output._pcm_samples, output._pcm_decoded_samples, output._pcm_resampler) == before
    await output.play_pcm(struct.pack("<h", 1000) * 2400, check)
    assert output.played_pcm_ms() > clock
    await output.finish_pcm(check)


@pytest.mark.asyncio
async def test_clear_during_notice_wins_over_late_provider_timeline_restore():
    source = Source()
    output = source.output = LiveKitAudioOutput(source)

    async def check():
        pass

    await output.play_pcm(struct.pack("<h", 1000) * 2400, check)
    source.clear_during_notice = True
    with pytest.raises(asyncio.CancelledError):
        await output.play_notice(synthetic_mp3(), check)
    assert output.played_pcm_ms() == 0
    assert output._pcm_resampler is None and output._pcm_decoded_samples == 0


@pytest.mark.asyncio
async def test_source_erasure_during_output_credit_admission_stops_frame_publication():
    erased, frames, admissions = [False], [], []

    class CapturedSource(Source):
        async def capture_frame(self, frame):
            frames.append(frame)

    class Credit:
        async def note_audio(self, duration_ms, *, direction):
            admissions.append((duration_ms, direction))
            erased[0] = True

    async def current():
        if erased[0]:
            raise WorkerAccessLost("The synthetic private source was erased")

    source = CapturedSource()
    output = source.output = LiveKitAudioOutput(source)
    output.usage = Credit()
    with pytest.raises(WorkerAccessLost):
        await output.play(synthetic_mp3(), current)
    assert len(admissions) == 1 and frames == [] and output._pcm_samples == 0


@pytest.mark.asyncio
async def test_actual_realtime_notice_tts_503_is_terminal_before_private_pcm_or_fallback():
    class Notice(HostTurnHooks):
        def __init__(self):
            self.failures = self.exposures = 0

        async def before_output(self):
            return "This reply may use private saved history."

        async def output_started(self):
            self.exposures += 1

        async def failed(self):
            self.failures += 1

    socket, host, requests = Socket(), Notice(), []

    async def connector(url, headers):
        return socket

    async def response(request):
        requests.append(request.url.path)
        return httpx.Response(503, json={"error": "synthetic"})

    config = VoxInferenceConfig()
    realtime = OpenAIRealtimeAdapter(config, settings(), Check(), Hooks(), connector=connector)
    pipeline = OpenAICompatiblePipeline(
        config, settings(), Check(), Hooks(), transport=httpx.MockTransport(response)
    )
    output = Output()
    worker = await worker_parts(realtime, pipeline, output, host)
    await realtime.begin_turn(())
    for _ in range(10):
        await realtime.append_audio(struct.pack("<h", 1000) * 320)
    await worker._respond(b"pcm", 0)
    assert worker.failed and host.failures == 1
    assert host.exposures == 0 and output.played == []
    assert requests == ["/v1/audio/speech"]
    await worker.close()
