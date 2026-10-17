"""Local, disposable screenshot OCR; every extracted meeting needs human review.

Image decoding and OCR run in a resource-limited child, outside the API process.
Only PNG/JPEG/WebP bytes cross stdin; neither the image nor OCR diagnostics are
written to disk, logged, or sent to a model/provider. This is deliberately a
conservative row parser, not a claim that a grid's column alignment is known.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 8_000_000
MAX_IMAGE_SIDE = 8192
MAX_EXTRACTED_TEXT = 16_000
MAX_WORKER_OUTPUT_BYTES = 96_000
MAX_CANDIDATES = 40
OCR_TIMEOUT_SECONDS = 15
WORKER_TIMEOUT_SECONDS = 20
_OCR_SLOT = asyncio.Semaphore(1)
_TESSERACT = "/usr/bin/tesseract"
_ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")
_WORKER_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "OMP_THREAD_LIMIT": "1",
    "OPENBLAS_NUM_THREADS": "1",
}

SectionKind = Literal["lecture", "lab", "discussion", "seminar", "other"]


class ScreenshotImportError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ScreenshotCandidate:
    course_name: str
    course_code: str | None
    kind: SectionKind
    weekdays: list[int]
    start_time: str | None
    end_time: str | None
    timezone: None = None
    building: None = None
    room: None = None
    instructor: None = None
    uncertainties: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScreenshotPreview:
    extracted_text: str
    candidates: list[ScreenshotCandidate]
    warnings: list[str]
    requires_review: Literal[True] = True


_COURSE_CODE = re.compile(r"\b([A-Z]{2,8})\s*([0-9]{2,5}[A-Z]?)\b(?!:)")
_DAY = re.compile(
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue(?:s)?|Wed(?:s)?|Thu(?:rs)?|Fri|Sat|Sun|Mo|Tu|We|Th|Fr|Sa|Su|"
    r"MWF|MTWRF|TTh|TR|MW)\b",
    re.IGNORECASE,
)
_DAY_INDEX = {
    "monday": 0,
    "mon": 0,
    "mo": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "tu": 1,
    "wednesday": 2,
    "wed": 2,
    "weds": 2,
    "we": 2,
    "thursday": 3,
    "thu": 3,
    "thurs": 3,
    "th": 3,
    "friday": 4,
    "fri": 4,
    "fr": 4,
    "saturday": 5,
    "sat": 5,
    "sa": 5,
    "sunday": 6,
    "sun": 6,
    "su": 6,
}
_DAY_GROUPS = {
    "mwf": [0, 2, 4],
    "mtwrf": [0, 1, 2, 3, 4],
    "tth": [1, 3],
    "tr": [1, 3],
    "mw": [0, 2],
}
_CLOCK = r"(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:[ap]\.?m\.?)?"
_TIME_RANGE = re.compile(
    rf"(?<![\d:])({_CLOCK})\s*(?:[-–—]|\bto\b)\s*({_CLOCK})(?![\d:])", re.IGNORECASE
)


def _clock(value: str) -> str | None:
    normalized = re.sub(r"[\s.]", "", value).casefold()
    suffix = normalized[-2:] if normalized.endswith(("am", "pm")) else None
    digits = normalized[:-2] if suffix else normalized
    pieces = digits.split(":")
    hour = int(pieces[0])
    minute = int(pieces[1]) if len(pieces) == 2 else 0
    if suffix:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    elif ":" not in digits or 1 <= hour <= 12:
        # Bare 09:00 may mean AM or PM. Keep the form blank until reviewed.
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_screenshot_text(text: str) -> ScreenshotPreview:
    """Suggest only same-line meetings; never infer dates, zones, or grid columns."""
    cleaned = "".join(char for char in text if char in "\n\t" or char.isprintable()).strip()
    warnings = [
        "Review every course, day and time, then choose the term dates and timezone before saving.",
        "Text recognition currently reads English. Correct any missing or misread text below.",
    ]
    if len(cleaned) > MAX_EXTRACTED_TEXT:
        cleaned = cleaned[:MAX_EXTRACTED_TEXT]
        warnings.append(
            "Only the first part of this long screenshot was read. Check the full image."
        )
    candidates: list[ScreenshotCandidate] = []
    seen: set[tuple[str, tuple[int, ...], str | None, str | None]] = set()
    for line in cleaned.splitlines():
        code = _COURSE_CODE.search(line)
        days = list(_DAY.finditer(line))
        times = list(_TIME_RANGE.finditer(line))
        if code is None or not days or len(times) != 1:
            continue
        # More than one course on a line is likely a grid, not a row meeting.
        if len(_COURSE_CODE.findall(line)) != 1:
            continue
        time_match = times[0]
        weekdays = sorted(
            {
                day
                for item in days
                for day in _DAY_GROUPS.get(
                    item.group().casefold(), [_DAY_INDEX.get(item.group().casefold(), -1)]
                )
            }
            - {-1}
        )
        start = _clock(time_match.group(1))
        end = _clock(time_match.group(2))
        uncertainties = [
            "Check the course name, meeting type, days and location against the image."
        ]
        if start is None or end is None:
            start = end = None
            uncertainties.append("The time is ambiguous. Enter both times and check AM or PM.")
        elif end <= start:
            start = end = None
            uncertainties.append("The end time was not after the start. Enter both times again.")
        course_code = f"{code.group(1)} {code.group(2)}"
        title_end = min(days[0].start(), time_match.start())
        name = line[code.end() : title_end].strip(" |,:;\t–—-")[:160]
        name = re.sub(r"\b(?:LEC|LAB|DIS|SEM)\s*\d*\b", "", name, flags=re.IGNORECASE).strip(
            " |,:;\t–—-"
        )
        kind: SectionKind = "other"
        if re.search(r"\b(?:lec|lecture)\b", line, re.IGNORECASE):
            kind = "lecture"
        if re.search(r"\b(?:lab|laboratory)\b", line, re.IGNORECASE):
            kind = "lab"
        elif re.search(r"\b(?:dis|discussion|recitation)\b", line, re.IGNORECASE):
            kind = "discussion"
        elif re.search(r"\b(?:sem|seminar)\b", line, re.IGNORECASE):
            kind = "seminar"
        key = (course_code, tuple(weekdays), start, end)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            ScreenshotCandidate(
                course_name=name or course_code,
                course_code=course_code,
                kind=kind,
                weekdays=weekdays,
                start_time=start,
                end_time=end,
                uncertainties=uncertainties,
            )
        )
        if len(candidates) == MAX_CANDIDATES:
            warnings.append("Review these classes first, then add any remaining classes manually.")
            break
    if not candidates:
        warnings.append(
            "The layout could not be matched to class rows. "
            "Use the extracted text to enter classes manually."
        )
    return ScreenshotPreview(cleaned, candidates, warnings)


async def _read_worker_output(reader: asyncio.StreamReader) -> bytes:
    result = bytearray()
    while chunk := await reader.read(min(8192, MAX_WORKER_OUTPUT_BYTES + 1 - len(result))):
        result.extend(chunk)
        if len(result) > MAX_WORKER_OUTPUT_BYTES:
            raise ScreenshotImportError(
                "ocr_output_limit", "This screenshot contains too much text."
            )
    return bytes(result)


async def _feed_worker(writer: asyncio.StreamWriter, image_bytes: bytes) -> None:
    try:
        writer.write(image_bytes)
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        writer.close()


async def _run_worker(image_bytes: bytes) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--ocr-worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_WORKER_ENV,
            start_new_session=True,
        )
    except OSError as error:
        raise ScreenshotImportError(
            "ocr_unavailable", "Screenshot reading is unavailable. Enter classes manually.", 503
        ) from error
    try:
        assert process.stdin is not None and process.stdout is not None
        async with asyncio.timeout(WORKER_TIMEOUT_SECONDS):
            _, output = await asyncio.gather(
                _feed_worker(process.stdin, image_bytes), _read_worker_output(process.stdout)
            )
            await process.wait()
        if process.returncode != 0:
            raise ScreenshotImportError(
                "ocr_failed", "This image could not be read. Try a clearer screenshot."
            )
        payload = json.loads(output)
        if not isinstance(payload, dict):
            raise ValueError("Invalid OCR result")
        if payload.get("error"):
            errors = {
                "invalid_image": (422, "Choose a valid PNG, JPEG or WebP screenshot."),
                "image_limit": (413, "Choose a screenshot with at most 8 million pixels."),
                "animated_image": (422, "Choose a still screenshot, not an animated image."),
                "ocr_unavailable": (
                    503,
                    "Screenshot reading is unavailable. Enter classes manually.",
                ),
                "ocr_timeout": (
                    504,
                    "Reading took too long. Try a smaller screenshot or enter classes manually.",
                ),
                "ocr_output_limit": (
                    422,
                    "This screenshot contains too much text. Crop it to the class list.",
                ),
            }
            code = str(payload["error"])
            status, message = errors.get(
                code, (422, "This image could not be read. Try a clearer screenshot.")
            )
            raise ScreenshotImportError(code, message, status)
        text = payload.get("text")
        if not isinstance(text, str) or len(text) > MAX_EXTRACTED_TEXT:
            raise ValueError("Invalid OCR text")
        return text
    except TimeoutError as error:
        raise ScreenshotImportError(
            "ocr_timeout",
            "Reading took too long. Try a smaller screenshot or enter classes manually.",
            504,
        ) from error
    except (ValueError, UnicodeError) as error:
        raise ScreenshotImportError(
            "ocr_failed", "This image could not be read. Try a clearer screenshot."
        ) from error
    finally:
        # The worker and Tesseract share a fresh group; cancellation also cleans both.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


async def preview_screenshot(image_bytes: bytes) -> ScreenshotPreview:
    if not image_bytes:
        raise ScreenshotImportError("empty_image", "Choose a PNG, JPEG or WebP screenshot.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ScreenshotImportError("image_limit", "Choose a screenshot smaller than 5 MB.", 413)
    # Reject extra work immediately rather than retaining queued images in memory.
    if _OCR_SLOT.locked():
        raise ScreenshotImportError(
            "ocr_busy", "Another screenshot is being read. Try again shortly.", 429
        )
    async with _OCR_SLOT:
        text = await _run_worker(image_bytes)
    result = parse_screenshot_text(text)
    if not result.extracted_text:
        return ScreenshotPreview(
            "",
            [],
            [
                "No readable text was found. "
                "Try a clearer English screenshot or enter classes manually.",
                *result.warnings,
            ],
        )
    return result


def _worker_limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _tesseract_text(image_bytes: bytes) -> dict[str, str]:
    """Bound both pipe directions; reject excessive output before retaining it."""
    with subprocess.Popen(
        [
            _TESSERACT,
            "stdin",
            "stdout",
            "-l",
            "eng",
            "--psm",
            "6",
            "-c",
            "debug_file=/dev/null",
            "-c",
            "tessedit_write_images=0",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_WORKER_ENV,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
        output = bytearray()
        sent = 0
        try:
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdin.fileno(), False)
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "input")
                selector.register(process.stdout, selectors.EVENT_READ, "output")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {"error": "ocr_timeout"}
                    for key, _ in selector.select(remaining):
                        if key.data == "input":
                            try:
                                sent += os.write(key.fd, image_bytes[sent : sent + 65536])
                            except BrokenPipeError:
                                sent = len(image_bytes)
                            if sent == len(image_bytes):
                                selector.unregister(process.stdin)
                                process.stdin.close()
                        else:
                            chunk = os.read(key.fd, min(8192, MAX_EXTRACTED_TEXT + 1 - len(output)))
                            if not chunk:
                                selector.unregister(process.stdout)
                            output.extend(chunk)
                            if len(output) > MAX_EXTRACTED_TEXT:
                                return {"error": "ocr_output_limit"}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"error": "ocr_timeout"}
            if process.wait(timeout=remaining) != 0:
                return {"error": "ocr_failed"}
            return {"text": output.decode("utf-8", errors="replace")}
        except subprocess.TimeoutExpired:
            return {"error": "ocr_timeout"}
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _worker_result(image_bytes: bytes) -> dict[str, str]:
    import warnings

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes), formats=_ALLOWED_FORMATS) as image:
                if (
                    image.width * image.height > MAX_IMAGE_PIXELS
                    or max(image.size) > MAX_IMAGE_SIDE
                ):
                    return {"error": "image_limit"}
                if getattr(image, "n_frames", 1) != 1:
                    return {"error": "animated_image"}
                image.load()
                # A new plain grayscale PNG strips metadata and bounds Tesseract's input formats.
                transposed = ImageOps.exif_transpose(image).convert("RGBA")
                background = Image.new("RGBA", transposed.size, "white")
                normalized = Image.alpha_composite(background, transposed).convert("L")
                normalized.info.clear()
                buffer = io.BytesIO()
                normalized.save(buffer, format="PNG")
        return _tesseract_text(buffer.getvalue())
    except FileNotFoundError:
        return {"error": "ocr_unavailable"}
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return {"error": "invalid_image"}
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError):
        return {"error": "image_limit"}


def _worker_main() -> None:
    try:
        _worker_limits()
        data = sys.stdin.buffer.read(MAX_IMAGE_BYTES + 1)
        result = (
            _worker_result(data) if 0 < len(data) <= MAX_IMAGE_BYTES else {"error": "image_limit"}
        )
        sys.stdout.write(json.dumps(result, ensure_ascii=True))
    except ImportError:
        sys.stdout.write('{"error":"ocr_unavailable"}')
    except Exception:
        # Never print a decoder traceback or OCR stderr containing uploaded text.
        sys.stdout.write('{"error":"ocr_failed"}')


if __name__ == "__main__" and sys.argv[1:] == ["--ocr-worker"]:
    _worker_main()
