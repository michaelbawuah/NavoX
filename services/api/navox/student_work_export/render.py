"""Deterministic plain-text layout using ReportLab; no HTML, URL or image renderer."""

import hashlib
import io
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import reportlab  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from navox.student_work_export.schemas import MAX_PAGES, MAX_PDF_BYTES, ExportInput

FONT_FILES = {
    "DejaVuSans.ttf": "ae7b7855e115a5966d8b1b3f80f254ccc117ec86f9965e202ee2940453837280",
    "DejaVuSans-Bold.ttf": "5c1247acef7f2b8522a31742c76d6adcb5569bacc0be7ceaa4dc39dd252ce895",
}
REGULAR, BOLD = "NavoXWorkRegular", "NavoXWorkBold"
WIDTH, HEIGHT = A4
MARGIN = 54.0
CONTENT_WIDTH = WIDTH - 2 * MARGIN
BODY_SIZE, LEADING = 11.0, 16.0
BOTTOM = 64.0


class RenderError(ValueError):
    """A content-free allowlisted reason; never contains the student's text."""


class LimitedBuffer(io.BytesIO):
    def write(self, data: bytes) -> int:  # type: ignore[override]
        if max(self.tell() + len(data), self.getbuffer().nbytes) > MAX_PDF_BYTES:
            raise RenderError("size_limit")
        return super().write(data)


@dataclass(frozen=True)
class Layout:
    header: list[str]
    cover: list[tuple[str, float, bool]]
    pages: list[list[str]]
    body_top: float


def fonts() -> None:
    if reportlab.Version != "5.0.1":
        raise RenderError("renderer_unavailable")
    for filename, checksum in FONT_FILES.items():
        source = Path(__file__).with_name("fonts") / filename
        if hashlib.sha256(source.read_bytes()).hexdigest() != checksum:
            raise RenderError("renderer_unavailable")
        name = BOLD if "Bold" in filename else REGULAR
        pdfmetrics.registerFont(TTFont(name, str(source)))


def supported(value: str, font_name: str) -> None:
    font = pdfmetrics.getFont(font_name)
    for char in value:
        if char in "\r\n":
            continue
        # The initial font profile promises straightforward LTR text. Reject
        # unavailable glyphs and shaping that this renderer has not verified.
        if (
            unicodedata.category(char) in {"Mn", "Mc", "Me", "Cf", "Cs"}
            or unicodedata.bidirectional(char) in {"R", "AL", "AN"}
            or ord(char) not in font.face.charToGlyph
            or font.face.charToGlyph[ord(char)] == 0
        ):
            raise RenderError("unsupported_text")


def wrap_line(value: str, font_name: str, size: float) -> list[str]:
    """Wrap at spaces, preserving every original character and its order."""
    if not value:
        return [""]
    lines: list[str] = []
    pending = ""
    for char in value:
        candidate = pending + char
        if pdfmetrics.stringWidth(candidate, font_name, size) <= CONTENT_WIDTH:
            pending = candidate
            continue
        split = pending.rfind(" ") + 1
        if split == 0:
            split = len(pending)
        if split == 0:
            raise RenderError("unsupported_text")
        lines.append(pending[:split])
        pending = pending[split:] + char
        while pdfmetrics.stringWidth(pending, font_name, size) > CONTENT_WIDTH:
            fit = 0
            for index in range(1, len(pending) + 1):
                if pdfmetrics.stringWidth(pending[:index], font_name, size) > CONTENT_WIDTH:
                    break
                fit = index
            if fit == 0:
                raise RenderError("unsupported_text")
            lines.append(pending[:fit])
            pending = pending[fit:]
    lines.append(pending)
    return lines


def layout(payload: ExportInput) -> Layout:
    fonts()
    supported(payload.title, BOLD)
    supported(payload.text, REGULAR)
    for value in (payload.author, payload.course_label):
        if value is not None:
            supported(value, REGULAR)
    header = wrap_line(payload.title, BOLD, 9)
    body_top = HEIGHT - MARGIN - len(header) * 12 - 24
    lines_per_page = int((body_top - BOTTOM) // LEADING) + 1
    text = payload.text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        line for original in text.split("\n") for line in wrap_line(original, REGULAR, BODY_SIZE)
    ]
    page_count = 1 + (len(lines) + lines_per_page - 1) // lines_per_page
    if not 2 <= page_count <= MAX_PAGES:
        raise RenderError("page_limit")
    cover = [(line, 24.0, True) for line in wrap_line(payload.title, BOLD, 24)]
    for value in (payload.author, payload.course_label):
        if value is not None:
            cover.append(("", 12.0, False))
            cover.extend((line, 12.0, False) for line in wrap_line(value, REGULAR, 12))
    if sum(size * 1.4 for _, size, _ in cover) > HEIGHT - 180 - 96:
        raise RenderError("metadata_limit")
    return Layout(
        header,
        cover,
        [
            lines[offset : offset + lines_per_page]
            for offset in range(0, len(lines), lines_per_page)
        ],
        body_top,
    )


def render(payload: ExportInput) -> bytes:
    document = layout(payload)
    target = LimitedBuffer()
    canvas = Canvas(target, pagesize=A4, pageCompression=1, invariant=1)
    canvas.setTitle(payload.title)
    canvas.setAuthor(payload.author or "")
    canvas.setCreator("NavoX own-work export")
    canvas.setSubject("")
    total = 1 + len(document.pages)

    def footer(number: int) -> None:
        canvas.setFont(REGULAR, 9)
        canvas.setFillColorRGB(0.30, 0.34, 0.38)
        canvas.drawRightString(WIDTH - MARGIN, 36, f"Page {number} of {total}")

    y = HEIGHT - 180
    for line, size, bold in document.cover:
        canvas.setFont(BOLD if bold else REGULAR, size)
        canvas.setFillColorRGB(0.10, 0.16, 0.23)
        canvas.drawString(MARGIN, y, line)
        y -= size * 1.4
    footer(1)
    canvas.showPage()
    for number, lines in enumerate(document.pages, start=2):
        canvas.setFont(BOLD, 9)
        canvas.setFillColorRGB(0.30, 0.34, 0.38)
        y = HEIGHT - MARGIN
        for line in document.header:
            canvas.drawString(MARGIN, y, line)
            y -= 12
        canvas.setStrokeColorRGB(0.82, 0.85, 0.88)
        canvas.line(MARGIN, y - 6, WIDTH - MARGIN, y - 6)
        canvas.setFont(REGULAR, BODY_SIZE)
        canvas.setFillColorRGB(0.10, 0.12, 0.15)
        y = document.body_top
        for line in lines:
            canvas.drawString(MARGIN, y, line)
            y -= LEADING
        footer(number)
        canvas.showPage()
    canvas.save()
    value = target.getvalue()
    if not value.startswith(b"%PDF-") or not 0 < len(value) <= MAX_PDF_BYTES:
        raise RenderError("size_limit")
    return value
