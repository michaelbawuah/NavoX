# Board glyph asset

`Kalam-Regular.ttf` is the unchanged Google Fonts Kalam Regular font from commit
`47d54ca1ebb14fd864695f1d39dc734f39017c33`. Its SHA-256 is
`57cecb63d4608019371954274ae1d8c397764debd5b19d4a33c1efa4dc923c0b`.
The original `OFL.txt` and the compiled `NavoX Board Glyphs` subset remain under
the SIL Open Font License 1.1. The public Board notices include the full license.

Regenerate or check from the repository root:

```sh
uv run scripts/build-board-handwriting.py
uv run scripts/build-board-handwriting.py --check
```

The script pins fontTools 4.61.1, checks the original font and license hashes,
decomposes glyph components, resolves implicit quadratic points and emits finite
closed cubic contours. It performs no download. The generated JSON records the
source revision, hashes, metrics and allowed `M`, `L`, `C`, `Z` command arrays.
The application neither serves/parses the TTF nor fetches a font during a lesson.

The 200-character subset covers printable ASCII and supported Latin punctuation,
symbols and selected additional characters. Unsupported scripts, combining marks
and over-budget text use the existing accessible SVG-text fallback. Layout keeps
the authored row breaks and board bounds; letter geometry is prepared once.
Completed letters share one filled path, with only the current outline animated.

These are font contours, not a recording of human pen strokes or natural stroke
order. They are not a source of real handwriting-photo evaluation evidence.
