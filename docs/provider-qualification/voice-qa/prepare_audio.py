"""Create disposable predefined speech fixtures using macOS local speech only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave


ROOT = Path(__file__).resolve().parent
os.umask(0o077)


def padded_wave(source: Path, destination: Path, leading_ms: int, trailing_ms: int) -> dict:
    with wave.open(str(source), "rb") as handle:
        if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate(), handle.getcomptype()) != (
            1, 2, 16000, "NONE"
        ):
            raise ValueError("Fixture format is not PCM16 mono at 16kHz")
        pcm = handle.readframes(handle.getnframes())
    pcm = bytes(16000 * leading_ms // 1000 * 2) + pcm + bytes(16000 * trailing_ms // 1000 * 2)
    samples = len(pcm) // 2
    if samples < 3200 or samples > 22 * 16000:
        raise ValueError("Fixture duration is outside its supported bound")
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm)
    destination.chmod(0o600)
    return {
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "samples": samples,
        "duration_ms": round(samples * 1000 / 16000),
        "sample_rate": 16000,
        "channels": 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-fixtures-only", action="store_true", required=True)
    parser.parse_args()
    if sys.platform != "darwin":
        raise RuntimeError("This preparation step requires local macOS say and afconvert")
    manifest = json.loads((ROOT / "fixtures.json").read_text())
    if manifest.get("synthetic_only") is not True or manifest.get("sample_rate") != 16000:
        raise ValueError("Synthetic fixture manifest is invalid")
    if manifest.get("voice") != "Samantha" or len(manifest.get("utterances", [])) != 5:
        raise ValueError("Unexpected local speech fixture definition")
    target = ROOT / "fixtures"
    target.mkdir(mode=0o700, exist_ok=True)
    output = []
    for item in manifest["utterances"]:
        if not isinstance(item.get("id"), str) or any(c not in "abcdefghijklmnopqrstuvwxyz-" for c in item["id"]):
            raise ValueError("Invalid synthetic fixture identity")
        text = item["text"]
        if not isinstance(text, str) or not 1 <= len(text) <= 500 or not text.isascii():
            raise ValueError("Synthetic statement is outside its bound")
        with tempfile.TemporaryDirectory(prefix="navox-synthetic-say-") as temporary:
            aiff, raw = Path(temporary) / "source.aiff", Path(temporary) / "source.wav"
            subprocess.run(
                ["/usr/bin/say", "-v", "Samantha", "-r", "200", "-o", str(aiff), text],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            )
            subprocess.run(
                ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(raw)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            )
            audio = padded_wave(raw, target / (item["id"] + ".wav"), manifest["leading_silence_ms"], manifest["trailing_silence_ms"])
        output.append({"id": item["id"], "spoken_text_sha256": hashlib.sha256(text.encode()).hexdigest(), **audio})
    report = {"schema": "navox-synthetic-voice-audio.v1", "synthesis": "local macOS say", "fixture_only": True, "provider_calls": 0, "fixtures": output}
    (ROOT / "audio-metadata.json").write_text(json.dumps(report, indent=2) + "\n")
    (ROOT / "audio-metadata.json").chmod(0o600)
    print(json.dumps({"prepared": True, "fixture_count": len(output), "max_duration_ms": max(item["duration_ms"] for item in output), "provider_calls": 0}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"prepared": False, "failure_class": type(error).__name__, "provider_calls": 0}))
        raise SystemExit(1)
