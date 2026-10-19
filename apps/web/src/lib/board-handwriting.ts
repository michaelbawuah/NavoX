import glyphAsset from "../assets/board-handwriting/glyphs.json";
import { wrapBoardText } from "./whiteboard-tutor";

type GlyphCommand =
  | ["M" | "L", number, number]
  | ["C", number, number, number, number, number, number]
  | ["Z"];

type SourceGlyph = {
  advance: number;
  bounds: [number, number, number, number];
  commands: GlyphCommand[];
};

type GlyphAsset = {
  version: number;
  unitsPerEm: number;
  ascender: number;
  descender: number;
  glyphs: Record<string, SourceGlyph>;
};

export type HandwritingBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type PreparedBoardHandwriting = {
  glyphs: {
    /** Closed outlines in absolute board coordinates; spaces have no path. */
    d: string;
    weight: number;
    /** Normalized presentation interval, including a short pause for spaces. */
    start: number;
    end: number;
    bounds: HandwritingBounds;
  }[];
  fullPath: string;
  bounds: HandwritingBounds;
};

export type BoardHandwritingInput = HandwritingBounds & {
  text: string;
  fontSize?: number;
  maxCharacters?: number;
  centered?: boolean;
};

const asset = glyphAsset as unknown as GlyphAsset;
const MAX_CHARACTERS = 240;
const MAX_GLYPH_COMMANDS = 400;
const MAX_TEXT_COMMANDS = 32_000;
const MAX_PREFIXES = 8;
const prefixCaches = new WeakMap<
  PreparedBoardHandwriting,
  Map<number, string>
>();

function validGlyph(glyph: SourceGlyph | undefined): glyph is SourceGlyph {
  return (
    !!glyph &&
    Number.isFinite(glyph.advance) &&
    glyph.advance >= 0 &&
    glyph.advance <= asset.unitsPerEm * 4 &&
    glyph.bounds.length === 4 &&
    glyph.bounds.every(
      (value) =>
        Number.isFinite(value) && Math.abs(value) <= asset.unitsPerEm * 8,
    ) &&
    glyph.bounds[0] <= glyph.bounds[2] &&
    glyph.bounds[1] <= glyph.bounds[3] &&
    glyph.commands.length <= MAX_GLYPH_COMMANDS &&
    glyph.commands.every((command) => {
      const validLength =
        (command[0] === "M" || command[0] === "L") && command.length === 3;
      if (
        !validLength &&
        !(command[0] === "C" && command.length === 7) &&
        !(command[0] === "Z" && command.length === 1)
      ) {
        return false;
      }
      return command
        .slice(1)
        .every(
          (value) =>
            typeof value === "number" &&
            Number.isFinite(value) &&
            Math.abs(value) <= asset.unitsPerEm * 8,
        );
    })
  );
}

function number(value: number): string {
  // Fixed precision keeps the local, precompiled outline paths compact and deterministic.
  return String(Math.round(value * 1000) / 1000);
}

function path(
  commands: GlyphCommand[],
  x: number,
  y: number,
  scaleX: number,
  scaleY: number,
): string {
  return commands
    .map((command) => {
      if (command[0] === "Z") return "Z";
      const coordinates = command.slice(1) as number[];
      return `${command[0]} ${coordinates
        .map((value, index) =>
          number(index % 2 === 0 ? x + value * scaleX : y + value * scaleY),
        )
        .join(" ")}`;
    })
    .join(" ");
}

function union(bounds: HandwritingBounds[]): HandwritingBounds | undefined {
  if (!bounds.length) return undefined;
  const x = Math.min(...bounds.map((item) => item.x));
  const y = Math.min(...bounds.map((item) => item.y));
  return {
    x,
    y,
    width: Math.max(...bounds.map((item) => item.x + item.width)) - x,
    height: Math.max(...bounds.map((item) => item.y + item.height)) - y,
  };
}

/**
 * Lay out the pinned local glyph subset once per text action. This does not parse
 * fonts, fetch assets, accept SVG, or change the lesson's established layout boxes.
 * Font outlines describe glyph contours, rather than a person's pen-stroke order.
 */
export function prepareBoardHandwriting({
  text,
  x,
  y,
  width,
  height,
  fontSize = 23,
  maxCharacters,
  centered = false,
}: BoardHandwritingInput): PreparedBoardHandwriting | null {
  if (
    text.length > MAX_CHARACTERS ||
    /\p{Mark}/u.test(text) ||
    ![x, y, width, height, fontSize].every(Number.isFinite) ||
    Math.abs(x) > 1_000_000 ||
    Math.abs(y) > 1_000_000 ||
    width <= 0 ||
    width > 12_000 ||
    height <= 0 ||
    height > 12_000 ||
    fontSize <= 0 ||
    fontSize > 240 ||
    (maxCharacters !== undefined &&
      (!Number.isInteger(maxCharacters) ||
        maxCharacters < 1 ||
        maxCharacters > MAX_CHARACTERS)) ||
    asset.version !== 1 ||
    !Number.isFinite(asset.unitsPerEm) ||
    asset.unitsPerEm <= 0 ||
    asset.unitsPerEm > 65_536
  ) {
    return null;
  }

  // Reject the entire shaped text instead of silently dropping unsupported glyphs,
  // lone surrogates, combining sequences, or scripts needing contextual shaping.
  const checked = new Set<string>();
  for (const character of text) {
    if (/^[ \t\r\n]$/u.test(character) || checked.has(character)) continue;
    if (!validGlyph(asset.glyphs[character])) return null;
    checked.add(character);
  }
  if (!validGlyph(asset.glyphs[" "])) return null;

  const lines = wrapBoardText(
    text,
    maxCharacters ?? Math.max(4, Math.floor(width / (fontSize * 0.57))),
  );
  const nominalLineHeight = fontSize * 1.3;
  // Narrow multi-line labels may have less space than the nominal line bands.
  // Compress the presentation inside their existing box, without moving anchors.
  const lineHeight = Math.min(nominalLineHeight, height / lines.length);
  const rowsTop = centered ? y + (height - lines.length * lineHeight) / 2 : y;
  const scale = fontSize / asset.unitsPerEm;
  const glyphs: PreparedBoardHandwriting["glyphs"] = [];
  let commandCount = 0;

  for (const [rowIndex, line] of lines.entries()) {
    let advance = 0;
    const row = [...line].map((character) => {
      const glyph = asset.glyphs[character];
      const positioned = { glyph, advance };
      advance += glyph.advance;
      commandCount += glyph.commands.length;
      return positioned;
    });
    if (commandCount > MAX_TEXT_COMMANDS) return null;
    const ink = row.filter(({ glyph }) => glyph.commands.length > 0);
    if (!ink.length) continue;
    const minX = Math.min(
      ...ink.map(({ glyph, advance }) => advance + glyph.bounds[0]),
    );
    const maxX = Math.max(
      ...ink.map(({ glyph, advance }) => advance + glyph.bounds[2]),
    );
    const minY = Math.min(...ink.map(({ glyph }) => glyph.bounds[1]));
    const maxY = Math.max(...ink.map(({ glyph }) => glyph.bounds[3]));
    const scaleX = Math.min(scale, width / Math.max(1, maxX - minX));
    const bandTop = rowsTop + rowIndex * lineHeight;
    const scaleY = Math.min(scale, lineHeight / Math.max(1, maxY - minY));
    const originX = centered
      ? x + (width - (maxX - minX) * scaleX) / 2 - minX * scaleX
      : x - minX * scaleX;
    // Keep the former baseline where it fits, while accommodating tall accents
    // and descenders inside the exact same row band.
    const baseline = Math.max(
      bandTop - minY * scaleY,
      Math.min(bandTop + fontSize, bandTop + lineHeight - maxY * scaleY),
    );

    for (const { glyph, advance } of row) {
      const origin = originX + advance * scaleX;
      glyphs.push({
        d: path(glyph.commands, origin, baseline, scaleX, scaleY),
        weight: glyph.commands.length
          ? Math.max(0.3, glyph.advance / asset.unitsPerEm)
          : 0.16,
        start: 0,
        end: 0,
        bounds: glyph.commands.length
          ? {
              x: origin + glyph.bounds[0] * scaleX,
              y: baseline + glyph.bounds[1] * scaleY,
              width: (glyph.bounds[2] - glyph.bounds[0]) * scaleX,
              height: (glyph.bounds[3] - glyph.bounds[1]) * scaleY,
            }
          : { x: origin, y: baseline, width: 0, height: 0 },
      });
    }
  }

  const weight = glyphs.reduce((sum, glyph) => sum + glyph.weight, 0);
  let elapsed = 0;
  for (const glyph of glyphs) {
    glyph.start = elapsed / weight;
    elapsed += glyph.weight;
    glyph.end = elapsed / weight;
  }
  // Avoid an arithmetic epsilon leaving the final glyph unfinished at progress=1.
  if (glyphs.length) glyphs[glyphs.length - 1].end = 1;
  return {
    glyphs,
    fullPath: glyphs
      .map((glyph) => glyph.d)
      .filter(Boolean)
      .join(" "),
    bounds: union(
      glyphs.filter((glyph) => glyph.d).map((glyph) => glyph.bounds),
    ) ?? {
      x,
      y,
      width: 0,
      height: 0,
    },
  };
}

/** Reuse the current completed prefix between animation frames; retain at most eight. */
export function completedHandwritingPath(
  prepared: PreparedBoardHandwriting,
  completedCount: number,
): string {
  const count = Number.isFinite(completedCount)
    ? Math.min(prepared.glyphs.length, Math.max(0, Math.floor(completedCount)))
    : 0;
  if (count === 0) return "";
  if (count === prepared.glyphs.length) return prepared.fullPath;
  let cache = prefixCaches.get(prepared);
  if (!cache) {
    cache = new Map();
    prefixCaches.set(prepared, cache);
  }
  const existing = cache.get(count);
  if (existing !== undefined) {
    cache.delete(count);
    cache.set(count, existing);
    return existing;
  }
  const result = prepared.glyphs
    .slice(0, count)
    .map((glyph) => glyph.d)
    .filter(Boolean)
    .join(" ");
  cache.set(count, result);
  if (cache.size > MAX_PREFIXES) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
  return result;
}
