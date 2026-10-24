"""Named LiveKit worker using only NavoX's approved, bounded voice gateway.

Run ``python -m navox.vox.worker_livekit start`` in a separately bounded API
container. This intentionally does not construct AgentSession or SDK inference
plugins: their providers, transcript retention and tool selection are not the
NavoX authorization boundary. A separate worker_reconcile process retires media.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import av
from livekit import agents, rtc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from navox.ai.configured import build_runtime
from navox.ai.speech_adapters import MAX_SPEECH_AUDIO_BYTES
from navox.core.settings import Settings, get_settings
from navox.db.session import get_database_engine, get_session_factory
from navox.vox.audio_input import AudioInputBuffer
from navox.vox.context_store import DatabaseContextStore
from navox.vox.inference_config import load_inference_config
from navox.vox.inference_http import OpenAICompatiblePipeline, UsageHooks
from navox.vox.inference_realtime import OpenAIRealtimeAdapter
from navox.vox.inference_worker import ConfiguredVoiceWorker
from navox.vox.provider import cloud_config
from navox.vox.schedule_tools import ScheduleTools
from navox.vox.session_context import CallContextKey, VoxCallContext
from navox.vox.web_tools import WEB_TOPIC, PublicWebTools, VoxReadTools
from navox.vox.worker import (
    DatabaseAuthority,
    PolicyVoiceGateway,
    VoiceTurnWorker,
    WorkerAccessLost,
    WorkerAssignment,
    is_voice,
)

MAX_OUTPUT_SAMPLES = 48_000 * 60
# Absorb short provider/DB scheduling stalls; interruption still clears immediately.
OUTPUT_QUEUE_MS = 200


def log_worker_failure(stage: str, error: BaseException) -> None:
    """Log only code location and type, never provider text, payloads or locals."""
    trace = error.__traceback__
    location = "unknown"
    while trace is not None:
        if "/navox/" in trace.tb_frame.f_code.co_filename:
            location = f"{trace.tb_frame.f_code.co_name}:{trace.tb_lineno}"
        trace = trace.tb_next
    logging.getLogger(__name__).warning(
        "Vox worker stopped",
        extra={"stage": stage, "error_type": type(error).__name__, "failure_site": location},
    )


@asynccontextmanager
async def exclusive_epoch(
    engine: AsyncEngine, assignment: WorkerAssignment
) -> AsyncIterator[AsyncConnection]:
    """One physical PG connection, one epoch, while the bounded renewable authority lease survives.

    No transaction or row lock spans RTC/provider I/O. A process crash releases
    the session lock. A duplicate dispatch cannot connect and replace the agent.
    Start with one active job per worker; size the DB pool for this dedicated slot.
    """
    if engine.dialect.name != "postgresql":
        raise WorkerAccessLost("The voice worker requires PostgreSQL fencing")
    key = int.from_bytes(
        hashlib.blake2b(
            b"navox-vox-worker-v1:" + assignment.connection_id.bytes, digest_size=8
        ).digest(),
        "big",
        signed=True,
    )
    async with engine.connect() as connection:
        claimed = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        await connection.commit()
        if claimed is not True:
            raise WorkerAccessLost("This voice epoch already has a worker")
        try:
            yield connection
        finally:
            # Closing a failed connection also releases its session lock; do not
            # return a still-locked healthy physical connection to the pool.
            try:
                if connection.closed or connection.invalidated:
                    await connection.invalidate()
                else:
                    released = await connection.scalar(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                    await connection.commit()
                    if released is not True:
                        await connection.invalidate()
            except BaseException:
                await connection.invalidate()
                raise


class LiveKitAudioOutput:
    def __init__(self, source: rtc.AudioSource):
        self.source = source
        self._pcm_resampler: av.AudioResampler | None = None
        self._pcm_samples = 0
        self._pcm_decoded_samples = 0
        self._output_frames = 0
        self._starvations = 0
        self._starvation_seconds = 0.0
        self._max_proof_seconds = 0.0
        self._queued_until: float | None = None
        self.usage: UsageHooks | None = None

    def played_pcm_ms(self) -> int:
        return max(0, int(self._pcm_samples / 48 - self.source.queued_duration * 1000))

    def _report_playout(self) -> None:
        if self._output_frames:
            logging.getLogger(__name__).info(
                "Vox audio playout totals",
                extra={
                    "output_frames": self._output_frames,
                    "starvations": self._starvations,
                    "starvation_ms": round(self._starvation_seconds * 1000),
                    "max_proof_ms": round(self._max_proof_seconds * 1000),
                },
            )
        self._output_frames = self._starvations = 0
        self._starvation_seconds = self._max_proof_seconds = 0.0
        self._queued_until = None

    async def clear(self) -> None:
        self.source.clear_queue()
        self._report_playout()
        self._pcm_resampler = None
        self._pcm_samples = self._pcm_decoded_samples = 0

    async def _capture_raw(self, raw: bytes, check: Callable[[], Awaitable[None]]) -> None:
        for start in range(0, len(raw), 1920):
            # Each <=20 ms frame still needs fresh authorization and committed
            # usage credit before entering the bounded, interruptible RTC queue.
            began = time.monotonic()
            await check()
            data = raw[start : start + 1920]
            if self.usage:
                await self.usage.note_audio(max(1, len(data) * 1000 // 96000), direction="output")
            now = time.monotonic()
            self._max_proof_seconds = max(self._max_proof_seconds, now - began)
            if self._queued_until is not None and now - self._queued_until > 0.002:
                self._starvations += 1
                self._starvation_seconds += now - self._queued_until
            await self.source.capture_frame(
                rtc.AudioFrame(
                    data=data,
                    sample_rate=48_000,
                    num_channels=1,
                    samples_per_channel=len(data) // 2,
                )
            )
            # Count only submitted samples, not the rest of a provider chunk
            # which may still be waiting for proof when the student interrupts.
            self._pcm_samples += len(data) // 2
            self._output_frames += 1
            self._queued_until = time.monotonic() + getattr(self.source, "queued_duration", 0.0)

    async def play(self, mp3: bytes, check: Callable[[], Awaitable[None]]) -> None:
        if not isinstance(mp3, bytes) or not 0 < len(mp3) <= MAX_SPEECH_AUDIO_BYTES:
            raise ValueError("The voice response is outside the audio bound")
        # A fixed 600-character gateway response yields a bounded MP3. Decode in
        # this job only; never write a media file or launch an unbounded subprocess.
        with av.open(io.BytesIO(mp3), mode="r", format="mp3") as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
            produced = 0
            for decoded in container.decode(audio=0):
                for frame in resampler.resample(decoded):
                    produced += frame.samples
                    if produced > MAX_OUTPUT_SAMPLES:
                        raise ValueError("The voice response exceeds the playback duration bound")
                    raw = bytes(frame.planes[0])[: frame.samples * 2]
                    await self._capture_raw(raw, check)
        await self.source.wait_for_playout()
        self._report_playout()
        self._pcm_samples = 0

    async def _capture_pcm(
        self, frames: list[av.AudioFrame], check: Callable[[], Awaitable[None]]
    ) -> None:
        for frame in frames:
            self._pcm_decoded_samples += frame.samples
            if self._pcm_decoded_samples > MAX_OUTPUT_SAMPLES:
                raise ValueError("The voice response exceeds the playback duration bound")
            raw = bytes(frame.planes[0])[: frame.samples * 2]
            await self._capture_raw(raw, check)

    async def play_pcm(self, pcm: bytes, check: Callable[[], Awaitable[None]]) -> None:
        if not pcm or len(pcm) % 2 or len(pcm) > 1_048_576:
            raise ValueError("The realtime audio frame is outside its bound")
        if self._pcm_resampler is None:
            self._pcm_resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
            self._pcm_samples = self._pcm_decoded_samples = 0
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        frame.sample_rate = 24_000
        frame.planes[0].update(pcm)
        await self._capture_pcm(self._pcm_resampler.resample(frame), check)

    async def finish_pcm(self, check: Callable[[], Awaitable[None]]) -> None:
        if self._pcm_resampler is not None:
            await self._capture_pcm(self._pcm_resampler.resample(None), check)
        await self.source.wait_for_playout()
        await check()
        self._report_playout()
        self._pcm_resampler = None
        self._pcm_samples = self._pcm_decoded_samples = 0


def participant_metadata(value: str, assignment: WorkerAssignment, generation: int) -> bool:
    if not isinstance(value, str) or len(value.encode()) > 1024:
        return False
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError):
        return False
    return (
        isinstance(decoded, dict)
        and set(decoded) == {"connection_id", "generation"}
        and (
            decoded["connection_id"] == str(assignment.connection_id)
            and type(decoded["generation"]) is int
            and decoded["generation"] == generation
        )
    )


async def _confirmed(check: DatabaseAuthority) -> None:
    """Dispatch may arrive before admission commits its post-provider proof."""
    async with asyncio.timeout(10):
        while True:
            _, intent = await check.intent(allow_preparing=True)
            if intent.status == "confirmed":
                return
            await asyncio.sleep(0.05)


@asynccontextmanager
async def _active_epoch(
    check: DatabaseAuthority, assignment: WorkerAssignment
) -> AsyncIterator[DatabaseAuthority]:
    async with exclusive_epoch(get_database_engine(), assignment) as connection:
        bound = DatabaseAuthority(check.factory, assignment, connection)
        try:
            yield bound
        finally:
            await bound.retire()


async def serve_job(ctx: agents.JobContext, settings: Settings) -> None:
    """No subscription, RTC connection or provider request before exact DB proof."""
    assignment = WorkerAssignment.from_metadata(ctx.job.metadata)
    if (
        ctx.job.room.name != assignment.room_name
        or ctx.job.agent_name != settings.student_vox_livekit_agent_name
    ):
        raise WorkerAccessLost("The voice job is outside its assigned room")
    cloud_config(settings)  # Runtime kill switch/config remains authoritative.
    check = DatabaseAuthority(get_session_factory(), assignment)
    await _confirmed(check)
    async with _active_epoch(check, assignment) as check:
        _, intent = await check.intent()
        await ctx.connect(auto_subscribe=agents.AutoSubscribe.SUBSCRIBE_NONE)
        if (ctx.room.name, ctx.room.local_participant.identity) != (
            assignment.room_name,
            assignment.agent_identity,
        ) or not participant_metadata(
            ctx.room.local_participant.metadata, assignment, intent.generation
        ):
            raise WorkerAccessLost("The voice participant binding changed")
        await check()
        source = rtc.AudioSource(48_000, 1, queue_size_ms=OUTPUT_QUEUE_MS)
        output = LiveKitAudioOutput(source)
        inference = load_inference_config(settings)
        worker: VoiceTurnWorker | ConfiguredVoiceWorker
        if inference is None:
            runtime = await build_runtime(settings, get_session_factory())
            worker = VoiceTurnWorker(
                assignment, check, PolicyVoiceGateway(runtime, settings, check), output
            )
        else:
            # New routes are operator-owned and remain behind the same exact DB proof.
            from navox.vox.worker_usage import WorkerUsageHooks

            authority = await check()
            context_key = CallContextKey(
                authority.user_id, authority.workspace_id, intent.reservation_id, intent.generation
            )
            context = await VoxCallContext.load(
                context_key,
                DatabaseContextStore(
                    check,
                    settings,
                    configuration_id=hashlib.sha256(
                        inference.model_dump_json().encode()
                    ).hexdigest(),
                ),
                max_characters=inference.max_context_characters,
            )

            async def inference_check() -> None:
                current = await check()
                if current != authority:
                    raise WorkerAccessLost("The voice call owner changed")

            usage = WorkerUsageHooks(check)
            output.usage = usage

            async def publish_web(result: dict[str, object]) -> None:
                await inference_check()
                await ctx.room.local_participant.publish_data(
                    json.dumps(result, ensure_ascii=False).encode(),
                    reliable=True,
                    destination_identities=[assignment.client_identity],
                    topic=WEB_TOPIC,
                )

            tools = VoxReadTools(
                ScheduleTools(check),
                PublicWebTools(check, inference, settings, usage, publish_web),
            )
            realtime = OpenAIRealtimeAdapter(inference, settings, inference_check, usage, tools)
            pipeline = OpenAICompatiblePipeline(inference, settings, inference_check, usage, tools)
            worker = ConfiguredVoiceWorker(
                assignment, check, output, inference, context, realtime, pipeline, usage=usage
            )
        stopped = asyncio.Event()
        streams: set[asyncio.Task[None]] = set()
        handlers: list[tuple[str, Callable[..., None]]] = []
        subscribed_sid: str | None = None
        seen_client = False

        audio_input = AudioInputBuffer()
        frames = voiced_frames = slow_frames = 0
        receive_seconds = max_receive_seconds = 0.0

        async def receive_frame(pcm: bytes) -> None:
            nonlocal frames, voiced_frames, slow_frames, receive_seconds, max_receive_seconds
            frames += 1
            voiced_frames += int(is_voice(pcm))
            started = time.monotonic()
            await worker.receive(assignment.client_identity, pcm)
            elapsed = time.monotonic() - started
            receive_seconds += elapsed
            max_receive_seconds = max(max_receive_seconds, elapsed)
            slow_frames += int(elapsed > 0.1)

        async def interrupt_input() -> None:
            audio_input.discard()
            async with audio_input.lock:
                audio_input.discard()
                await worker.interrupt()

        async def pump(
            track: rtc.Track, participant: rtc.RemoteParticipant, track_sid: str
        ) -> None:
            stream = rtc.AudioStream(
                track, capacity=5, sample_rate=16_000, num_channels=1, frame_size_ms=20
            )
            try:
                async for event in stream:
                    if stopped.is_set() or subscribed_sid != track_sid:
                        break
                    # Drain RTC promptly; DB and provider admission run separately.
                    audio_input.put(bytes(event.frame.data))
            except Exception as error:
                log_worker_failure("audio_receive", error)
                if subscribed_sid == track_sid:
                    stopped.set()
            finally:
                await stream.aclose()

        def completed(task: asyncio.Task[None]) -> None:
            streams.discard(task)
            if task.cancelled():
                return
            error = task.exception()
            if error is not None:
                log_worker_failure("background", error)
                stopped.set()

        def spawn(coroutine: Awaitable[None]) -> None:
            task = asyncio.ensure_future(coroutine)
            streams.add(task)
            task.add_done_callback(completed)

        def subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            nonlocal subscribed_sid
            if subscribed_sid == publication.sid:
                return
            if (
                participant.identity != assignment.client_identity
                or track.kind != rtc.TrackKind.KIND_AUDIO
                or publication.source != rtc.TrackSource.SOURCE_MICROPHONE
                or subscribed_sid is not None
                or not participant_metadata(participant.metadata, assignment, intent.generation)
            ):
                stopped.set()
                return
            subscribed_sid = publication.sid
            spawn(pump(track, participant, publication.sid))

        async def subscribe(participant: rtc.RemoteParticipant) -> None:
            nonlocal seen_client
            try:
                await check()
                if participant.identity != assignment.client_identity or not participant_metadata(
                    participant.metadata, assignment, intent.generation
                ):
                    raise WorkerAccessLost("The voice participant is outside its assignment")
                seen_client = True
                for publication in participant.track_publications.values():
                    if (
                        publication.source != rtc.TrackSource.SOURCE_MICROPHONE
                        or publication.kind != rtc.TrackKind.KIND_AUDIO
                    ):
                        raise WorkerAccessLost("The voice track is outside its assignment")
                    publication.set_subscribed(True)
            except Exception as error:
                log_worker_failure("subscribe", error)
                stopped.set()

        def participant_connected(participant: rtc.RemoteParticipant) -> None:
            spawn(subscribe(participant))

        def track_published(
            publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
        ) -> None:
            participant_connected(participant)

        def participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            nonlocal subscribed_sid
            if participant.identity != assignment.client_identity:
                stopped.set()
                return
            subscribed_sid = None
            # Same-call SDK reconnect may resume only while fresh DB authority survives.
            spawn(interrupt_input())

        def reconnecting(*args: object) -> None:
            spawn(interrupt_input())

        def metadata_changed(previous: str, participant: rtc.Participant) -> None:
            stopped.set()

        def disconnected(*args: object) -> None:
            stopped.set()

        async def watch() -> None:
            try:
                while not stopped.is_set():
                    if worker.failed:
                        stopped.set()
                        return
                    cloud_config(get_settings())
                    await check()
                    await audio_input.flush_idle(worker.flush_idle)
                    if isinstance(worker, ConfiguredVoiceWorker):
                        await worker.maintain()
                        from navox.vox.commands import take_wake_commands

                        # Serialize accepted commands; never overwrite a prior accepted question.
                        if worker._task is None:
                            commands = await take_wake_commands(check, settings)
                            for command in commands:
                                async with audio_input.lock:
                                    await worker.text_turn(command.text)
                    await asyncio.sleep(0.25)
            except Exception as error:
                log_worker_failure("watch", error)
                stopped.set()

        def on(event: str, callback: Callable[..., None]) -> None:
            ctx.room.on(event, callback)  # type: ignore[arg-type]
            handlers.append((event, callback))

        try:
            if isinstance(worker, ConfiguredVoiceWorker):
                await worker.warm()
            on("track_subscribed", subscribed)
            on("participant_connected", participant_connected)
            on("track_published", track_published)
            on("participant_disconnected", participant_disconnected)
            on("participant_metadata_changed", metadata_changed)
            on("reconnecting", reconnecting)
            on("disconnected", disconnected)
            track = rtc.LocalAudioTrack.create_audio_track("navox-vox", source)
            await check()
            await ctx.room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
            for participant in ctx.room.remote_participants.values():
                participant_connected(participant)
            spawn(audio_input.consume(receive_frame))
            spawn(watch())
            if isinstance(worker, ConfiguredVoiceWorker):
                spawn(usage.heartbeat())
            # An unused dispatch must not occupy the single worker indefinitely.
            try:
                await asyncio.wait_for(stopped.wait(), timeout=20)
            except TimeoutError:
                if not seen_client:
                    stopped.set()
            await stopped.wait()
        finally:
            stopped.set()
            for event, callback in handlers:
                ctx.room.off(event, callback)  # type: ignore[arg-type]
            for task in tuple(streams):
                task.cancel()
            await asyncio.gather(*streams, return_exceptions=True)
            audio_input.discard()
            logging.getLogger(__name__).info(
                "Vox input processing totals",
                extra={
                    "frames": frames,
                    "voiced_frames": voiced_frames,
                    "slow_frames": slow_frames,
                    "receive_ms": round(receive_seconds * 1000),
                    "max_receive_ms": round(max_receive_seconds * 1000),
                },
            )
            try:
                await worker.close()
                if isinstance(worker, ConfiguredVoiceWorker):
                    await usage.close()
            finally:
                for task in tuple(streams):
                    task.cancel()
                await asyncio.gather(*streams, return_exceptions=True)
                await source.aclose()
                await ctx.room.disconnect()


async def job_entrypoint(ctx: agents.JobContext) -> None:
    """Importable entry point for LiveKit's spawned/forkserver job processes."""
    try:
        # Resolve private configuration in the child; do not pickle credentials.
        await serve_job(ctx, get_settings())
    except Exception as error:
        log_worker_failure("job", error)
        # SDK logs must not receive provider exception bodies or media data.
        await ctx.room.disconnect()
    finally:
        ctx.shutdown("The bounded NavoX voice session ended")


def build_server(settings: Settings) -> agents.AgentServer:
    config = cloud_config(settings)
    inference = load_inference_config(settings)
    server = agents.AgentServer(
        ws_url=config.server_url,
        api_key=config.api_key,
        api_secret=config.api_secret,
        num_idle_processes=0,
        job_memory_warn_mb=512,
        job_memory_limit_mb=768,
        drain_timeout=130,
        session_end_timeout=10,
        host="127.0.0.1",
        port=8081,
        permissions=agents.WorkerPermissions(
            can_publish=True,
            can_subscribe=True,
            can_publish_data=inference is not None and inference.web_search is not None,
            can_update_metadata=False,
            can_publish_sources=[rtc.TrackSource.SOURCE_MICROPHONE],  # type: ignore[list-item]
        ),
    )
    server.load_fnc = lambda current: float(bool(current.active_jobs))

    async def request(req: agents.JobRequest) -> None:
        try:
            assignment = WorkerAssignment.from_metadata(req.job.metadata)
            if req.room.name != assignment.room_name or req.agent_name != config.agent_name:
                raise WorkerAccessLost("The voice job is outside its assignment")
            check = DatabaseAuthority(get_session_factory(), assignment)
            await _confirmed(check)
            _, intent = await check.intent()
            await req.accept(
                identity=assignment.agent_identity,
                metadata=json.dumps(
                    {
                        "connection_id": str(assignment.connection_id),
                        "generation": intent.generation,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        except Exception:
            await req.reject()

    server.rtc_session(job_entrypoint, agent_name=config.agent_name, on_request=request)

    return server


if __name__ == "__main__":
    agents.cli.run_app(build_server(get_settings()))
