#!/usr/bin/env python3
"""Build the NavoX public-example walkthrough from unmodified UI captures.

Requires Python 3.10+, Pillow, and ffmpeg. No application account is used.
Run from any directory: python docs/media/build-walkthrough.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT, FPS = 1600, 900, 20
BG = "#070C14"
FG = "#E8EFF4"
MUTED = "#A2B2C1"
ACCENT = "#AAD8E5"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
CHAPTERS = [
    ("today", 9, "Your next move.", "See the day at a glance.",
     "The example workspace groups messages, meetings, and commitments into a short Today list.",
     (318, 104, 1016, 821), "Today"),
    ("evidence", 9, "Keep the context.", "Expand an item to understand why it matters.",
     "The sample email item reveals the original request and its exact deadline next to the task.",
     (318, 54, 1016, 871), "Source context"),
    ("upcoming", 8, "Look ahead.", "See what comes next.",
     "Upcoming separates future meetings and commitments from today's priorities.",
     (318, 151, 1016, 775), "Upcoming"),
    ("waiting", 7, "Track the handoff.", "Remember what is waiting on someone else.",
     "Waiting gives pending replies their own view so follow-ups stay visible.",
     (318, 199, 1016, 727), "Waiting"),
    ("search-results", 9, "Find your signal.", "Search across the example workspace.",
     "Searching for feedback brings together matching items from Today and Waiting.",
     (318, 209, 1016, 717), "Search"),
    ("home", 6, "Explore NavoX.", "Your day. Reimagined.",
     "Visit navox.net and open the public example to try this interface yourself.",
     (24, 12, 1312, 912), "navox.net"),
]
TOTAL = sum(chapter[1] for chapter in CHAPTERS)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    location = FONT_DIR / filename
    if not location.exists():
        raise FileNotFoundError(
            f"Install DejaVu fonts or change FONT_DIR; missing {location}"
        )
    return ImageFont.truetype(str(location), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, face, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=face) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def paragraph(draw, text, position, face, color, width, gap):
    x, y = position
    for line in wrap(draw, text, face, width):
        draw.text((x, y), line, font=face, fill=color)
        y += gap
    return y


def slide(index: int) -> Image.Image:
    key, _, title, subtitle, body, crop, label = CHAPTERS[index]
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 42), "NavoX", fill=FG, font=font(30, True))
    draw.text((202, 50), "INTERFACE WALKTHROUGH", fill=MUTED, font=font(15))
    draw.rounded_rectangle((1173, 34, 1536, 79), radius=22, fill="#13232C")
    draw.text((1193, 46), "Public example • Sample data", font=font(18), fill=ACCENT)
    draw.line((64, 103, 1536, 103), fill="#263442", width=1)
    draw.text((64, 155), f"0{index + 1} / {label.upper()}", fill=ACCENT, font=font(17))
    y = paragraph(draw, title, (64, 220), font(49, True), FG, 424, 62)
    y = paragraph(draw, subtitle, (64, y + 34), font(25), FG, 424, 37)
    paragraph(draw, body, (64, y + 31), font(22), MUTED, 424, 34)
    if key == "home":
        draw.rounded_rectangle((64, 658, 335, 722), radius=12, fill=ACCENT)
        draw.text((102, 674), "navox.net ↗", fill=BG, font=font(27, True))
    else:
        draw.text((64, 718), "Captured from navox.net", fill=MUTED, font=font(17))
    capture = Image.open(ROOT / "captures" / f"navox-{key}.jpg").convert("RGB")
    capture = capture.crop(crop)
    capture.thumbnail((970, 682), Image.Resampling.LANCZOS)
    x = 560 + (970 - capture.width) // 2
    y = 127 + (682 - capture.height) // 2
    canvas.paste(capture, (x, y))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (x - 1, y - 1, x + capture.width, y + capture.height),
        radius=3,
        outline="#314250",
        width=1,
    )
    draw.text((64, 825), "Actual interface captures • Guided sequence • No live account actions", fill=MUTED, font=font(17))
    draw.text((1465, 825), f"{index + 1:02d}/06", fill=MUTED, font=font(17))
    return canvas


def build() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")
    output = ROOT / "navox-walkthrough.mp4"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    completed = 0
    previous = None
    for index, chapter in enumerate(CHAPTERS):
        base = slide(index)
        base.save(ROOT / f"preview-{index + 1:02d}.jpg", quality=92)
        frames = chapter[1] * FPS
        for frame in range(frames):
            # A brief crossfade joins actual captures without inventing UI motion.
            image = base.copy()
            if previous is not None and frame < 8:
                image = Image.blend(previous, image, (frame + 1) / 8)
            draw = ImageDraw.Draw(image)
            draw.rectangle((64, 865, 1536, 869), fill="#23313D")
            progress = (completed + frame + 1) / (TOTAL * FPS)
            draw.rectangle((64, 865, 64 + round(1472 * progress), 869), fill=ACCENT)
            process.stdin.write(image.tobytes())
        completed += frames
        previous = base
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("ffmpeg encoding failed")
    # A complete but compact six-chapter GIF, sampled at one frame per second.
    # The MP4 above is the higher-resolution, smooth-transition version.
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(output),
        "-filter_complex",
        "fps=1,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=full[p];[b][p]paletteuse=dither=bayer:bayer_scale=5",
        "-loop", "0", str(ROOT / "navox-walkthrough.gif"),
    ], check=True)
    for path in [output, ROOT / "navox-walkthrough.gif"]:
        print(f"{path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    build()
