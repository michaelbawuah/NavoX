import asyncio
import io
import json
import shutil
import struct
import subprocess
import sys
import zlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw, ImageFont

from navox.api import student_screenshot
from navox.api.auth import get_current_account
from navox.db.session import get_database_session
from navox.student import screenshot


def image_bytes(format="PNG", *, width=100, height=100):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format=format)
    return buffer.getvalue()


def screenshot_app(*, personal=True):
    app = FastAPI()
    app.include_router(student_screenshot.router, prefix="/api/v1")
    app.dependency_overrides[get_current_account] = lambda: SimpleNamespace(
        workspace=SimpleNamespace(workspace_type="personal" if personal else "team")
    )
    return app


def test_row_parser_keeps_explicit_day_time_and_does_not_infer_term_or_zone():
    result = screenshot.parse_screenshot_text(
        "CS 2110 Object Oriented Programming MWF 09:05 AM - 09:55 AM\n"
        "ECE 3140 LAB Tu Th 1:25 PM - 2:15 PM\n"
        "CS 4410 Operating Systems Monday 13:25 - 14:15"
    )
    assert result.requires_review is True
    assert len(result.candidates) == 3
    first, lab, afternoon = result.candidates
    assert first.course_name == "Object Oriented Programming"
    assert first.course_code == "CS 2110"
    assert first.weekdays == [0, 2, 4]
    assert (first.start_time, first.end_time) == ("09:05", "09:55")
    assert lab.kind == "lab"
    assert lab.weekdays == [1, 3]
    assert (lab.start_time, lab.end_time) == ("13:25", "14:15")
    assert (afternoon.start_time, afternoon.end_time) == ("13:25", "14:15")
    for row in result.candidates:
        assert row.timezone is row.building is row.room is row.instructor is None
        assert row.uncertainties
    assert any("term dates and timezone" in warning for warning in result.warnings)


@pytest.mark.parametrize("times", ["9:00-10:00", "9-10AM", "13:00-12:00", "13:00AM-14:00PM"])
def test_ambiguous_or_invalid_times_remain_blank_for_review(times):
    result = screenshot.parse_screenshot_text(f"CS 2110 Monday {times}")
    assert len(result.candidates) == 1
    assert result.candidates[0].start_time is None
    assert result.candidates[0].end_time is None
    assert len(result.candidates[0].uncertainties) > 1


def test_grid_headers_are_not_fabricated_into_class_meetings():
    result = screenshot.parse_screenshot_text(
        "Monday Tuesday Wednesday Thursday Friday\n09:00 10:00\nCS 2110 ECE 3140\nFall 2026"
    )
    assert result.candidates == []
    assert result.extracted_text.endswith("Fall 2026")
    assert any("manually" in warning for warning in result.warnings)


def test_multi_course_grid_row_and_duplicate_rows_are_not_repeated():
    result = screenshot.parse_screenshot_text(
        "CS 2110 ECE 3140 Monday 9:00 AM-10:00 AM\n"
        "CS 2110 Monday 9:00 AM-10:00 AM\n"
        "CS 2110 Monday 9:00 AM-10:00 AM"
    )
    assert len(result.candidates) == 1


@pytest.mark.asyncio
async def test_empty_and_oversize_never_start_ocr(monkeypatch):
    async def forbidden(_data):
        pytest.fail("Invalid size must not start a child process")

    monkeypatch.setattr(screenshot, "_run_worker", forbidden)
    for data in (b"", b"x" * (screenshot.MAX_IMAGE_BYTES + 1)):
        with pytest.raises(screenshot.ScreenshotImportError):
            await screenshot.preview_screenshot(data)


@pytest.mark.asyncio
async def test_busy_ocr_rejects_a_second_image_and_recovers(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def waiting(_data):
        entered.set()
        await release.wait()
        return "CS 2110 Monday 9:00 AM-10:00 AM"

    monkeypatch.setattr(screenshot, "_run_worker", waiting)
    first = asyncio.create_task(screenshot.preview_screenshot(b"image one"))
    await entered.wait()
    with pytest.raises(screenshot.ScreenshotImportError) as caught:
        await screenshot.preview_screenshot(b"image two")
    assert caught.value.status_code == 429
    release.set()
    assert (await first).requires_review is True
    assert (await screenshot.preview_screenshot(b"third image")).requires_review is True


@pytest.mark.asyncio
async def test_worker_output_is_bounded():
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * (screenshot.MAX_WORKER_OUTPUT_BYTES + 1))
    reader.feed_eof()
    with pytest.raises(screenshot.ScreenshotImportError, match="too much text"):
        await screenshot._read_worker_output(reader)


def test_tesseract_output_limit_kills_child_before_collecting_unbounded_text(monkeypatch):
    original_popen = subprocess.Popen
    children = []

    def flood(*_args, **kwargs):
        process = original_popen(
            [sys.executable, "-I", "-c", "import os,time;os.write(1,b'x'*1000000);time.sleep(60)"],
            **kwargs,
        )
        children.append(process)
        return process

    monkeypatch.setattr(screenshot.subprocess, "Popen", flood)
    result = screenshot._tesseract_text(b"not retained")
    assert result == {"error": "ocr_output_limit"}
    assert len(children) == 1
    assert children[0].poll() is not None


def test_tesseract_deadline_kills_a_stalled_child(monkeypatch):
    original_popen = subprocess.Popen
    children = []

    def stall(*_args, **kwargs):
        process = original_popen(
            [sys.executable, "-I", "-c", "import time;time.sleep(60)"],
            **kwargs,
        )
        children.append(process)
        return process

    monkeypatch.setattr(screenshot.subprocess, "Popen", stall)
    monkeypatch.setattr(screenshot, "OCR_TIMEOUT_SECONDS", 0.05)
    assert screenshot._tesseract_text(b"image") == {"error": "ocr_timeout"}
    assert children[0].poll() is not None


@pytest.mark.asyncio
async def test_worker_group_is_cleaned_up_when_reading_is_cancelled(monkeypatch):
    waiting = asyncio.Event()
    killed = []

    class Input:
        def write(self, _data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

    class Output:
        async def read(self, _size):
            waiting.set()
            await asyncio.Event().wait()

    class Process:
        pid = 999_123
        returncode = None
        stdin = Input()
        stdout = Output()

        async def wait(self):
            self.returncode = -9
            return -9

    async def start(*argv, **kwargs):
        assert argv[-1] == "--ocr-worker"
        assert "private-image-text" not in repr(argv)
        assert kwargs["start_new_session"] is True
        assert set(kwargs["env"]) == {"PATH", "LANG", "OMP_THREAD_LIMIT", "OPENBLAS_NUM_THREADS"}
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    monkeypatch.setattr(screenshot.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    task = asyncio.create_task(screenshot._run_worker(b"private-image-text"))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed == [(999_123, screenshot.signal.SIGKILL)]


@pytest.mark.asyncio
async def test_endpoint_requires_auth_before_reading_image(monkeypatch):
    async def forbidden(_data):
        pytest.fail("Unauthenticated requests must not decode images")

    async def no_database():
        yield None

    monkeypatch.setattr(student_screenshot, "preview_screenshot", forbidden)
    app = FastAPI()
    app.include_router(student_screenshot.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = no_database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(
            "/api/v1/student/imports/screenshot/preview",
            content=b"not read",
            headers={"Content-Type": "image/png"},
        )
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_raw_endpoint_is_readonly_and_requires_explicit_review(monkeypatch):
    received = []

    async def preview(data):
        received.append(data)
        return screenshot.parse_screenshot_text("CS 2110 Monday 9:00 AM-10:00 AM")

    monkeypatch.setattr(student_screenshot, "preview_screenshot", preview)
    async with AsyncClient(
        transport=ASGITransport(app=screenshot_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/student/imports/screenshot/preview",
            content=b"just the image bytes",
            headers={"Content-Type": "image/png"},
        )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["requires_review"] is True
    assert response.json()["candidates"][0]["timezone"] is None
    assert "preview_token" not in response.json()
    assert received == [b"just the image bytes"]


@pytest.mark.asyncio
async def test_non_personal_and_non_image_uploads_do_not_start_ocr(monkeypatch):
    async def forbidden(_data):
        pytest.fail("Rejected requests must not decode images")

    monkeypatch.setattr(student_screenshot, "preview_screenshot", forbidden)
    for app, mime, status in (
        (screenshot_app(personal=False), "image/png", 403),
        (screenshot_app(), "application/pdf", 415),
        (screenshot_app(), "multipart/form-data", 415),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post(
                "/api/v1/student/imports/screenshot/preview",
                content=b"x",
                headers={"Content-Type": mime},
            )
        assert result.status_code == status


@pytest.mark.asyncio
async def test_chunked_upload_is_size_limited_without_content_length(monkeypatch):
    async def forbidden(_data):
        pytest.fail("Oversized stream must not decode images")

    async def chunks():
        for _ in range(6):
            yield b"x" * 1024 * 1024

    monkeypatch.setattr(student_screenshot, "preview_screenshot", forbidden)
    async with AsyncClient(
        transport=ASGITransport(app=screenshot_app()), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/v1/student/imports/screenshot/preview",
            content=chunks(),
            headers={"Content-Type": "image/png"},
        )
    assert result.status_code == 413


@pytest.mark.asyncio
async def test_busy_upload_rejects_before_buffering_another_image(monkeypatch):
    slots = asyncio.Semaphore(1)
    await slots.acquire()
    monkeypatch.setattr(student_screenshot, "_UPLOAD_SLOTS", slots)
    consumed = False

    async def chunks():
        nonlocal consumed
        consumed = True
        yield b"another screenshot"

    try:
        async with AsyncClient(
            transport=ASGITransport(app=screenshot_app()), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/student/imports/screenshot/preview",
                content=chunks(),
                headers={"Content-Type": "image/png"},
            )
        assert result.status_code == 429
        assert not consumed
    finally:
        slots.release()


@pytest.mark.asyncio
async def test_stalled_upload_has_a_deadline_before_ocr(monkeypatch):
    async def forbidden(_data):
        pytest.fail("Timed out uploads must not decode images")

    async def chunks():
        yield b"start of image"
        await asyncio.Event().wait()
        yield b"never reached"

    monkeypatch.setattr(student_screenshot, "preview_screenshot", forbidden)
    monkeypatch.setattr(student_screenshot, "_UPLOAD_TIMEOUT_SECONDS", 0.01)
    async with AsyncClient(
        transport=ASGITransport(app=screenshot_app()), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/v1/student/imports/screenshot/preview",
            content=chunks(),
            headers={"Content-Type": "image/png"},
        )
    assert result.status_code == 408


@pytest.mark.asyncio
async def test_unavailable_ocr_does_not_claim_a_completed_import(monkeypatch):
    async def unavailable(_data):
        raise screenshot.ScreenshotImportError("ocr_unavailable", "Enter classes manually.", 503)

    monkeypatch.setattr(student_screenshot, "preview_screenshot", unavailable)
    async with AsyncClient(
        transport=ASGITransport(app=screenshot_app()), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/v1/student/imports/screenshot/preview",
            content=b"x",
            headers={"Content-Type": "image/png"},
        )
    assert result.status_code == 503
    assert "candidates" not in result.json()


@pytest.mark.asyncio
async def test_invalid_and_compressed_oversize_images_are_rejected_by_real_decoder():
    # Rewrite only the PNG dimensions and checksum: the huge header is rejected
    # before pixel allocation, without constructing a huge test bitmap.
    encoded = bytearray(image_bytes())
    encoded[16:24] = struct.pack(">II", 4000, 4000)
    encoded[29:33] = struct.pack(">I", zlib.crc32(encoded[12:29]))
    for data, code in ((b"not a screenshot", "invalid_image"), (bytes(encoded), "image_limit")):
        with pytest.raises(screenshot.ScreenshotImportError) as caught:
            await screenshot.preview_screenshot(data)
        assert caught.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
async def test_supported_still_image_formats_use_real_local_ocr(format):
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is installed by the production API Dockerfile")
    result = await screenshot.preview_screenshot(image_bytes(format))
    assert result.extracted_text == ""
    assert result.candidates == []
    assert any("No readable text" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_animated_webp_is_not_silently_read_as_a_single_frame():
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(
        buffer,
        "WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (100, 100), "black")],
        duration=100,
        loop=0,
    )
    with pytest.raises(screenshot.ScreenshotImportError) as caught:
        await screenshot.preview_screenshot(buffer.getvalue())
    assert caught.value.code == "animated_image"


@pytest.mark.asyncio
async def test_real_english_timetable_ocr_produces_reviewable_rows():
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is installed by the production API Dockerfile")
    image = Image.new("RGB", (1900, 200), "white")
    ImageDraw.Draw(image).text(
        (30, 40),
        "CS 2110 Programming Monday 09:05 AM - 09:55 AM",
        font=ImageFont.load_default(size=38),
        fill="black",
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    result = await screenshot.preview_screenshot(buffer.getvalue())
    assert "CS 2110" in result.extracted_text
    assert len(result.candidates) == 1, json.dumps(result.extracted_text)
    row = result.candidates[0]
    assert row.weekdays == [0]
    assert (row.start_time, row.end_time) == ("09:05", "09:55")
    assert row.timezone is None
    assert result.requires_review is True
