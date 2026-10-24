"""Bounded speech SSE decoding. Never log audio, provider content or errors."""

import base64
import binascii
import json
from typing import Any

MAX_AUDIO_BYTES = 2_097_152
MAX_WIRE_BYTES = MAX_AUDIO_BYTES * 2
MAX_EVENT_BYTES = 1_048_576


def decode_speech_events(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    if len(raw) > MAX_WIRE_BYTES:
        raise ValueError("Speech stream exceeds its bound")
    audio = bytearray()
    usage: dict[str, Any] = {}
    data: list[str] = []
    done = False
    terminal = False

    def event() -> None:
        nonlocal done, terminal, usage
        if not data:
            return
        payload = "\n".join(data)
        data.clear()
        if len(payload.encode()) > MAX_EVENT_BYTES:
            raise ValueError("Speech event exceeds its bound")
        if payload == "[DONE]":
            if not done or terminal:
                raise ValueError("Speech stream ended without completion")
            terminal = True
            return
        if done or terminal:
            raise ValueError("Speech event follows completion")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Invalid speech event")
        kind = value.get("type")
        if kind == "speech.audio.delta":
            encoded = value.get("audio")
            if not isinstance(encoded, str):
                raise ValueError("Invalid speech audio")
            try:
                chunk = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("Invalid speech audio") from None
            if not chunk or len(audio) + len(chunk) > MAX_AUDIO_BYTES:
                raise ValueError("Speech audio exceeds its bound")
            audio.extend(chunk)
        elif kind == "speech.audio.done":
            done = True
            candidate = value.get("usage")
            usage = candidate if isinstance(candidate, dict) else {}
        else:
            raise ValueError("Unsupported speech event")

    for line in raw.decode("utf-8").splitlines():
        if not line:
            event()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif line.startswith((":", "event:", "id:", "retry:")):
            continue
        else:
            raise ValueError("Invalid speech stream")
    event()
    if not done or not audio:
        raise ValueError("Incomplete speech stream")
    return bytes(audio), usage
