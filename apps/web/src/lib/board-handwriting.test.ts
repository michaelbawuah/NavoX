import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import glyphAsset from "../assets/board-handwriting/glyphs.json";
import {
  type BoardHandwritingInput,
  completedHandwritingPath,
  type PreparedBoardHandwriting,
  prepareBoardHandwriting,
} from "./board-handwriting";
import { whiteboardLessons } from "./whiteboard-lessons";
import { layoutLesson, wrapBoardText } from "./whiteboard-tutor";

const box = { x: 40, y: 70, width: 480, height: 280 };

function prepare(text: string, input: Partial<BoardHandwritingInput> = {}) {
  const result = prepareBoardHandwriting({ ...box, text, ...input });
  if (!result) throw new Error(`Expected local glyph support: ${text}`);
  return result;
}

/** Independent cubic extrema check of the SVG output, rather than its metadata. */
function outlineExtrema(d: string) {
  const tokens = d.match(/[MLCZ]|-?(?:\d+\.?\d*|\.\d+)/gu) ?? [];
  const points: [number, number][] = [];
  let point: [number, number] = [0, 0];
  let index = 0;
  const coordinate = () => Number(tokens[index++]);
  const evaluate = (values: number[], t: number) =>
    values[0] * (1 - t) ** 3 +
    3 * values[1] * (1 - t) ** 2 * t +
    3 * values[2] * (1 - t) * t ** 2 +
    values[3] * t ** 3;
  const turningPoints = ([p0, p1, p2, p3]: number[]) => {
    const a = -p0 + 3 * p1 - 3 * p2 + p3;
    const b = 2 * (p0 - 2 * p1 + p2);
    const c = p1 - p0;
    if (Math.abs(a) < 1e-12) return Math.abs(b) < 1e-12 ? [] : [-c / b];
    const discriminant = b * b - 4 * a * c;
    if (discriminant < 0) return [];
    return [
      (-b + Math.sqrt(discriminant)) / (2 * a),
      (-b - Math.sqrt(discriminant)) / (2 * a),
    ];
  };
  while (index < tokens.length) {
    const command = tokens[index++];
    if (command === "M" || command === "L") {
      point = [coordinate(), coordinate()];
      points.push(point);
    } else if (command === "C") {
      const xs = [point[0], coordinate()];
      const ys = [point[1], coordinate()];
      xs.push(coordinate());
      ys.push(coordinate());
      xs.push(coordinate());
      ys.push(coordinate());
      for (const t of [0, 1, ...turningPoints(xs), ...turningPoints(ys)]) {
        if (t >= 0 && t <= 1) points.push([evaluate(xs, t), evaluate(ys, t)]);
      }
      point = [xs[3], ys[3]];
    } else if (command !== "Z") {
      throw new Error(`Unexpected local path command ${command}`);
    }
  }
  return {
    minX: Math.min(...points.map(([x]) => x)),
    minY: Math.min(...points.map(([, y]) => y)),
    maxX: Math.max(...points.map(([x]) => x)),
    maxY: Math.max(...points.map(([, y]) => y)),
  };
}

function expectInside(
  prepared: PreparedBoardHandwriting,
  bounds: typeof box = box,
) {
  expect(prepared.bounds.x).toBeGreaterThanOrEqual(bounds.x - 0.00001);
  expect(prepared.bounds.y).toBeGreaterThanOrEqual(bounds.y - 0.00001);
  expect(prepared.bounds.x + prepared.bounds.width).toBeLessThanOrEqual(
    bounds.x + bounds.width + 0.00001,
  );
  expect(prepared.bounds.y + prepared.bounds.height).toBeLessThanOrEqual(
    bounds.y + bounds.height + 0.00001,
  );
  if (prepared.fullPath) {
    const actual = outlineExtrema(prepared.fullPath);
    expect(actual.minX).toBeGreaterThanOrEqual(bounds.x - 0.001);
    expect(actual.minY).toBeGreaterThanOrEqual(bounds.y - 0.001);
    expect(actual.maxX).toBeLessThanOrEqual(bounds.x + bounds.width + 0.001);
    expect(actual.maxY).toBeLessThanOrEqual(bounds.y + bounds.height + 0.001);
    expect(actual.minX).toBeCloseTo(prepared.bounds.x, 2);
    expect(actual.minY).toBeCloseTo(prepared.bounds.y, 2);
    expect(actual.maxX).toBeCloseTo(
      prepared.bounds.x + prepared.bounds.width,
      2,
    );
    expect(actual.maxY).toBeCloseTo(
      prepared.bounds.y + prepared.bounds.height,
      2,
    );
  }
  for (const glyph of prepared.glyphs) {
    expect(Object.values(glyph.bounds).every(Number.isFinite)).toBe(true);
    expect(glyph.end).toBeGreaterThan(glyph.start);
    expect(glyph.start).toBeGreaterThanOrEqual(0);
    expect(glyph.end).toBeLessThanOrEqual(1);
    expect(glyph.d).not.toMatch(/NaN|Infinity|undefined|[<>]/u);
    expect(glyph.d.replace(/[MLCZ\d.,\s-]/gu, "")).toBe("");
  }
}

describe("pinned Board glyph geometry", () => {
  it("ships a finite local outline asset with source and license provenance", () => {
    expect(glyphAsset.version).toBe(1);
    expect(glyphAsset.subsetName).toBe("NavoX Board Glyphs");
    expect(glyphAsset.source.family).toBe("Kalam");
    expect(glyphAsset.source.license).toBe("OFL-1.1");
    expect(glyphAsset.source.commit).toMatch(/^[a-f\d]{40}$/u);
    expect(glyphAsset.source.sha256).toMatch(/^[a-f\d]{64}$/u);
    expect(glyphAsset.source.licenseSha256).toMatch(/^[a-f\d]{64}$/u);
    const font = readFileSync(
      new URL("../assets/board-handwriting/Kalam-Regular.ttf", import.meta.url),
    );
    const license = readFileSync(
      new URL("../assets/board-handwriting/OFL.txt", import.meta.url),
    );
    expect(createHash("sha256").update(font).digest("hex")).toBe(
      glyphAsset.source.sha256,
    );
    expect(createHash("sha256").update(license).digest("hex")).toBe(
      glyphAsset.source.licenseSha256,
    );
    expect(
      readFileSync(
        new URL("../../public/whiteboard-notices.txt", import.meta.url),
        "utf8",
      ),
    ).toContain(license.toString("utf8").trim());
    expect(glyphAsset.unitsPerEm).toBeGreaterThan(0);
    for (const glyph of Object.values(glyphAsset.glyphs)) {
      expect(glyph.commands.length).toBeLessThanOrEqual(400);
      expect(glyph.bounds).toHaveLength(4);
      expect(glyph.bounds.every(Number.isFinite)).toBe(true);
      for (const command of glyph.commands) {
        expect(["M", "L", "C", "Z"]).toContain(command[0]);
        expect(command.slice(1).every(Number.isFinite)).toBe(true);
      }
    }
  });

  it.each(
    Array.from({ length: 95 }, (_, index) => String.fromCharCode(index + 32)),
  )(
    "supports printable ASCII %j without a font request or parser",
    (character) => {
      const result = prepare(character);
      expectInside(result);
      if (character !== " ") {
        expect(result.glyphs).toHaveLength(1);
        expect(result.fullPath).toMatch(/^M /u);
      }
    },
  );

  it.each([
    "W".repeat(36),
    "m".repeat(36),
    "gjpqy".repeat(7),
    "W".repeat(240),
    "0123456789 !@#$%^&*()_+-=[]{};:',.<>/?\\|\"`~",
    "A\n\nWgjpqy\nAnother row",
  ])(
    "keeps wide Latin, descenders, and symbols inside the fixed box: %j",
    (text) => {
      expectInside(prepare(text));
    },
  );

  it("fits a very narrow multi-line label inside its existing anchor box", () => {
    const bounds = { x: 30, y: 900, width: 100, height: 28 };
    const result = prepare("Wide descenders WWW gyp and qj", {
      ...bounds,
      fontSize: 16,
      centered: true,
      maxCharacters: 8,
    });
    expectInside(result, bounds);
    expect(result.glyphs.length).toBeGreaterThan(20);
  });

  it("preserves the renderer's wrapping, including explicit blank lines", () => {
    const text = "A\n\nA";
    expect(wrapBoardText(text)).toEqual(["A", "", "A"]);
    const result = prepare(text);
    expect(result.glyphs).toHaveLength(2);
    expect(result.glyphs[1].bounds.y - result.glyphs[0].bounds.y).toBeCloseTo(
      23 * 1.3 * 2,
    );
    const long = "W".repeat(80);
    expect(wrapBoardText(long)).toHaveLength(3);
    const wrapped = prepare(long);
    expect(wrapped.glyphs).toHaveLength(80);
    expect(
      wrapped.glyphs[36].bounds.y - wrapped.glyphs[0].bounds.y,
    ).toBeCloseTo(23 * 1.3);
  });

  it("uses the existing whitespace normalization without making missing glyphs", () => {
    expect(prepare("A\t \r B")).toEqual(prepare("A B"));
    expect(prepare("\n \t\r\n")).toEqual({
      glyphs: [],
      fullPath: "",
      bounds: { x: box.x, y: box.y, width: 0, height: 0 },
    });
  });

  it("keeps spaces in the ordered timeline, without adding outline nodes", () => {
    const result = prepare("A B");
    expect(result.glyphs).toHaveLength(3);
    expect(result.glyphs[1].d).toBe("");
    expect(result.glyphs[1].weight).toBeGreaterThan(0);
    expect(result.glyphs[0].start).toBe(0);
    expect(result.glyphs[2].end).toBe(1);
    expect(result.glyphs[0].end).toBe(result.glyphs[1].start);
    expect(result.glyphs[1].end).toBe(result.glyphs[2].start);
  });

  it("centers actual ink bounds and fits negative sidebearings", () => {
    for (const text of ["A", "j", "WWW", "(gyp)"]) {
      const result = prepare(text, { centered: true });
      expectInside(result);
      expect(result.bounds.x + result.bounds.width / 2).toBeCloseTo(
        box.x + box.width / 2,
      );
      expect(prepare(text).bounds.x).toBeCloseTo(box.x);
    }
  });

  it("treats markup-looking input as literal finite glyph geometry", () => {
    const text = '<img src="x" onerror="alert(1)">';
    const result = prepare(text);
    expect(result.glyphs).toHaveLength(text.length);
    expect(result.fullPath).not.toMatch(/img|src|onerror|alert|[<>]/u);
    expectInside(result);
  });

  it.each([
    "A🙂B",
    "e\u0301",
    "\u0301",
    "中文",
    "العربية",
    "A\ud800B",
    "A\udc00B",
    "hidden\u0000text",
    "A\u200dB",
    "A\u202eB",
  ])("falls back as a whole for unsupported or shaped text %j", (text) => {
    expect(prepareBoardHandwriting({ ...box, text })).toBeNull();
  });

  it.each([
    { text: "A".repeat(241) },
    { x: Number.NaN },
    { y: Number.POSITIVE_INFINITY },
    { width: 0 },
    { width: -10 },
    { height: 0 },
    { height: Number.NEGATIVE_INFINITY },
    { fontSize: 0 },
    { fontSize: 241 },
    { maxCharacters: 0 },
    { maxCharacters: 1.5 },
    { maxCharacters: 241 },
  ])("rejects invalid geometry or exceeded text budgets %j", (input) => {
    expect(prepareBoardHandwriting({ ...box, text: "A", ...input })).toBeNull();
  });

  it("generates deterministic paths without changing authored lesson geometry", () => {
    for (const lesson of whiteboardLessons) {
      const before = layoutLesson(lesson);
      for (const item of before.items) {
        if (item.action.type !== "write") continue;
        const input = { ...item, text: item.action.text };
        const first = prepareBoardHandwriting(input);
        expect(first).not.toBeNull();
        expect(prepareBoardHandwriting(input)).toEqual(first);
        expectInside(first as PreparedBoardHandwriting, item);
      }
      expect(layoutLesson(lesson)).toEqual(before);
    }
  });
});

describe("bounded completed glyph prefixes", () => {
  it("joins only completed outlines, preserving the full completed path", () => {
    const prepared = prepare("A B W");
    expect(completedHandwritingPath(prepared, 0)).toBe("");
    expect(completedHandwritingPath(prepared, 1)).toBe(prepared.glyphs[0].d);
    expect(completedHandwritingPath(prepared, 2)).toBe(prepared.glyphs[0].d);
    expect(completedHandwritingPath(prepared, 3)).toBe(
      `${prepared.glyphs[0].d} ${prepared.glyphs[2].d}`,
    );
    expect(completedHandwritingPath(prepared, prepared.glyphs.length)).toBe(
      prepared.fullPath,
    );
    expect(completedHandwritingPath(prepared, 100)).toBe(prepared.fullPath);
    for (const value of [-1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(completedHandwritingPath(prepared, value)).toBe("");
    }
    expect(completedHandwritingPath(prepared, 1.9)).toBe(prepared.glyphs[0].d);
  });

  it("reuses the same partial prefix while the current glyph is tracing", () => {
    const prepared = prepare("Glyph tracing reuses geometry");
    const first = completedHandwritingPath(prepared, 5);
    const original = prepared.glyphs.slice;
    prepared.glyphs.slice = () => {
      throw new Error("Prefix was needlessly joined again");
    };
    try {
      expect(completedHandwritingPath(prepared, 5)).toBe(first);
    } finally {
      prepared.glyphs.slice = original;
    }
  });

  it("evicts old prefix strings instead of storing every text prefix", () => {
    const prepared = prepare("W".repeat(240));
    const first = completedHandwritingPath(prepared, 1);
    for (let index = 2; index <= 20; index += 1) {
      completedHandwritingPath(prepared, index);
    }
    const original = prepared.glyphs.slice;
    let joins = 0;
    prepared.glyphs.slice = (...args) => {
      joins += 1;
      return original.apply(prepared.glyphs, args);
    };
    try {
      expect(completedHandwritingPath(prepared, 1)).toBe(first);
      expect(joins).toBe(1);
      expect(completedHandwritingPath(prepared, 20)).toBe(
        prepared.glyphs
          .slice(0, 20)
          .map((glyph) => glyph.d)
          .join(" "),
      );
      expect(joins).toBe(2);
    } finally {
      prepared.glyphs.slice = original;
    }
  });
});
