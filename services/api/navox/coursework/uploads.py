"""Bounded local PDF/text extraction; never writes an uploaded file to disk."""

import asyncio
import contextlib
import os
import shutil
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from navox.coursework.schemas import MAX_PAGES, MAX_TEXT_CHARS, MAX_UPLOAD_BYTES

UPLOAD_SLOTS = asyncio.Semaphore(2)
MAX_OUTPUT_BYTES = MAX_TEXT_CHARS * 4 + MAX_PAGES + 8
READ_TIMEOUT_SECONDS = 10
EXTRACT_TIMEOUT_SECONDS = 20
_WORKER = """import os,resource,sys
resource.setrlimit(resource.RLIMIT_CPU,(12,12))
resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
os.execv(sys.argv[1],[sys.argv[1],'-enc','UTF-8','-layout','-f','1','-l','101','-','-'])
"""


class UploadError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ExtractedMaterial:
    original: bytes
    media_type: str
    pages: list[str]


@asynccontextmanager
async def upload_slot() -> AsyncIterator[None]:
    if UPLOAD_SLOTS.locked():
        raise UploadError("Two materials are already being processed. Try again shortly.", 429)
    await UPLOAD_SLOTS.acquire()
    try:
        yield
    finally:
        UPLOAD_SLOTS.release()


async def read_upload(stream: AsyncIterator[bytes]) -> bytes:
    content = bytearray()
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            async for chunk in stream:
                if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
                    raise UploadError("Choose a file no larger than 5 MiB", 413)
                content.extend(chunk)
    except TimeoutError:
        raise UploadError("The upload took too long. Try a smaller file.", 408) from None
    if not content:
        raise UploadError("Choose a nonempty file")
    return bytes(content)


async def extract_material(raw: bytes, media_type: str) -> ExtractedMaterial:
    if not 0 < len(raw) <= MAX_UPLOAD_BYTES:
        raise UploadError("Choose a nonempty file no larger than 5 MiB", 413)
    if media_type not in {"application/pdf", "text/plain", "text/markdown"}:
        raise UploadError("Choose PDF, UTF-8 text, or Markdown", 415)
    if media_type != "application/pdf":
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise UploadError("Save this text file as UTF-8") from None
        pages = [text]
    else:
        executable = shutil.which("pdftotext", path="/usr/bin:/bin")
        if executable is None:
            raise UploadError("PDF text extraction is unavailable. Upload UTF-8 text instead.", 503)
        if not raw.startswith(b"%PDF-"):
            raise UploadError("This file is not a readable PDF")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            _WORKER,
            executable,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        assert process.stdin is not None and process.stdout is not None

        async def write() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(raw)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        writer = asyncio.create_task(write())
        output = bytearray()
        try:
            async with asyncio.timeout(EXTRACT_TIMEOUT_SECONDS):
                while chunk := await process.stdout.read(8192):
                    if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                        raise UploadError("Extracted text exceeds 200,000 characters")
                    output.extend(chunk)
                await writer
                if await process.wait() != 0:
                    raise UploadError(
                        "This PDF could not be read. Remove encryption or upload text."
                    )
            try:
                text = output.decode("utf-8")
            except UnicodeError:
                raise UploadError("This PDF did not produce readable UTF-8 text") from None
            pages = text.split("\f")
            if pages and not pages[-1].strip():
                pages.pop()
        except TimeoutError:
            raise UploadError(
                "PDF extraction took too long. Upload a smaller PDF or UTF-8 text.", 408
            ) from None
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            await process.wait()
    if not 1 <= len(pages) <= MAX_PAGES:
        raise UploadError("Choose a PDF with at most 100 pages")
    if sum(len(page) for page in pages) > MAX_TEXT_CHARS:
        raise UploadError("Extracted text exceeds 200,000 characters")
    if not any(page.strip() for page in pages):
        raise UploadError(
            "This file has no readable text. Image-only PDFs need a text export; no OCR was run."
        )
    if any("\x00" in page for page in pages):
        raise UploadError("This file contains unsupported binary text")
    return ExtractedMaterial(raw, media_type, pages)
