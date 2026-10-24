"""Owned microphone replacement keeps one call without accepting overlapping input."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from livekit import rtc

from navox.vox.audio_input import AudioInputBuffer
from navox.vox.worker import WorkerAccessLost, WorkerAssignment
from navox.vox.worker_livekit import InputTrackBinding


def setup():
    assignment = WorkerAssignment(uuid4(), "room", "student", "agent")
    audio = AudioInputBuffer()
    binding = InputTrackBinding(assignment, 4, audio)
    participant = SimpleNamespace(
        identity="student",
        metadata=json.dumps({"connection_id": str(assignment.connection_id), "generation": 4}),
    )
    track = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
    first = SimpleNamespace(sid="first", source=rtc.TrackSource.SOURCE_MICROPHONE)
    second = SimpleNamespace(sid="second", source=rtc.TrackSource.SOURCE_MICROPHONE)
    return binding, audio, participant, track, first, second


@pytest.mark.asyncio
async def test_retired_mic_backlog_drops_but_new_mic_and_late_old_event_survive():
    binding, audio, participant, track, first, second = setup()
    assert binding.subscribe(track, first, participant)
    assert not binding.subscribe(track, first, participant)
    audio.put(bytes(640))
    binding.release(first, participant)
    assert binding.sid is None
    assert binding.subscribe(track, second, participant)
    fresh_frame = bytes([1]) * 640
    audio.put(fresh_frame)
    binding.release(first, participant)  # Delayed unsubscribe after unpublish.
    assert binding.sid == "second"
    received = []
    done = asyncio.Event()

    async def receive(frame):
        received.append(frame)
        done.set()

    task = asyncio.create_task(audio.consume(receive))
    try:
        await asyncio.wait_for(done.wait(), 1)
        assert received == [fresh_frame]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_overlapping_microphones_require_confirmed_retirement():
    binding, _, participant, track, first, second = setup()
    binding.subscribe(track, first, participant)
    with pytest.raises(WorkerAccessLost, match="still active"):
        binding.subscribe(track, second, participant)
    assert binding.sid == "first"


@pytest.mark.parametrize("invalid", ["owner", "epoch", "generation", "video", "source"])
def test_invalid_track_or_owner_never_replaces_current_input(invalid):
    binding, _, participant, track, first, second = setup()
    binding.subscribe(track, first, participant)
    binding.release(first, participant)
    if invalid == "owner":
        participant.identity = "another-student"
    elif invalid == "epoch":
        participant.metadata = json.dumps({"connection_id": str(uuid4()), "generation": 4})
    elif invalid == "generation":
        participant.metadata = json.dumps(
            {"connection_id": str(binding.assignment.connection_id), "generation": 5}
        )
    elif invalid == "video":
        track.kind = rtc.TrackKind.KIND_VIDEO
    else:
        second.source = rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO
    with pytest.raises(WorkerAccessLost):
        binding.subscribe(track, second, participant)
    assert binding.sid is None


def test_foreign_retirement_cannot_clear_the_owned_microphone():
    binding, _, participant, track, first, _ = setup()
    binding.subscribe(track, first, participant)
    participant.identity = "another-student"
    with pytest.raises(WorkerAccessLost):
        binding.release(first, participant)
    assert binding.sid == "first"
