# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools==4.61.1"]
# ///
"""Compile the pinned OFL font to a finite Board glyph asset; never fetch at runtime.

Regenerate: uv run scripts/build-board-handwriting.py
Verify:     uv run scripts/build-board-handwriting.py --check
The font and generated subset remain OFL-1.1; application code is separate.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "apps/web/src/assets/board-handwriting"
FONT_SHA = "57cecb63d4608019371954274ae1d8c397764debd5b19d4a33c1efa4dc923c0b"
LICENSE_SHA = "f62ef357d3a1c3d27edd35a6e1ba350e8a8d13499797964eeadefbf0b3b15d1f"
SOURCE_COMMIT = "47d54ca1ebb14fd864695f1d39dc734f39017c33"
EXTRA = "‘’“”–—…←↑→↓↔−αβγδεζηθικλμνξοπρστυφχψωΩ"


def number(value):
    if not math.isfinite(value) or abs(value) > 10000:
        raise ValueError("Font coordinates must be finite and bounded")
    rounded = round(value, 3)
    return int(rounded) if rounded == int(rounded) else rounded


class CubicContours(BasePen):
    """BasePen resolves implicit quadratic points and converts each segment to cubic."""

    def __init__(self, glyph_set):
        super().__init__(glyph_set)
        self.commands = []

    def _moveTo(self, point):
        self.commands.append(["M", number(point[0]), number(-point[1])])

    def _lineTo(self, point):
        self.commands.append(["L", number(point[0]), number(-point[1])])

    def _curveToOne(self, first, second, end):
        self.commands.append(
            [
                "C",
                number(first[0]),
                number(-first[1]),
                number(second[0]),
                number(-second[1]),
                number(end[0]),
                number(-end[1]),
            ]
        )

    def _closePath(self):
        self.commands.append(["Z"])

    def _endPath(self):
        raise ValueError("Font contours must be closed")


def glyph(font, name):
    glyph_set = font.getGlyphSet()
    contours = CubicContours(glyph_set)
    glyph_set[name].draw(contours)
    commands = contours.commands
    if len(commands) > 400:
        raise ValueError("Glyph exceeds the finite command budget")
    bounds = BoundsPen(glyph_set)
    glyph_set[name].draw(bounds)
    box = bounds.bounds
    return {
        "advance": number(font["hmtx"][name][0]),
        "bounds": [number(box[0]), number(-box[3]), number(box[2]), number(-box[1])]
        if box
        else [0, 0, 0, 0],
        "commands": commands,
    }


def compile_asset():
    font_path = ASSETS / "Kalam-Regular.ttf"
    assert hashlib.sha256(font_path.read_bytes()).hexdigest() == FONT_SHA
    assert hashlib.sha256((ASSETS / "OFL.txt").read_bytes()).hexdigest() == LICENSE_SHA
    font = TTFont(font_path, lazy=False)
    cmap = font.getBestCmap()
    characters = {chr(code) for code in range(32, 127)}
    characters.update(chr(code) for code in range(160, 256))
    characters.update(EXTRA)
    glyphs = {
        char: glyph(font, cmap[ord(char)])
        for char in sorted(characters)
        if ord(char) in cmap
    }
    assert all(chr(code) in glyphs for code in range(32, 127))
    asset = {
        "version": 1,
        "subsetName": "NavoX Board Glyphs",
        "unitsPerEm": font["head"].unitsPerEm,
        "ascender": font["hhea"].ascent,
        "descender": font["hhea"].descent,
        "source": {
            "family": "Kalam",
            "commit": SOURCE_COMMIT,
            "sha256": FONT_SHA,
            "license": "OFL-1.1",
            "licenseSha256": LICENSE_SHA,
            "generator": "fonttools==4.61.1",
            "coordinates": "Y-down at the font baseline; cubic contours, not pen stroke order",
        },
        "glyphs": glyphs,
    }
    font.close()
    return (
        json.dumps(asset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = compile_asset()
    target = ASSETS / "glyphs.json"
    if args.check:
        if not target.exists() or target.read_text() != content:
            raise SystemExit(
                "Board glyph asset is stale; regenerate it with the pinned compiler"
            )
    else:
        target.write_text(content)
    print(
        f"Board glyph asset {'verified' if args.check else 'generated'}: {len(content.encode())} bytes"
    )


if __name__ == "__main__":
    main()
