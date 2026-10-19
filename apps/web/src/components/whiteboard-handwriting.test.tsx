// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as handwriting from "../lib/board-handwriting";
import type { PlacedAction } from "../lib/whiteboard-tutor";
import { WhiteboardScene } from "./whiteboard-scene";

function placed(text = "AB"): PlacedAction {
  return {
    action: { id: "writing", type: "write", text, place: "top-left" },
    stepIndex: 0,
    x: 40,
    y: 40,
    width: 200,
    height: 140,
  };
}

function geometry(item: PlacedAction) {
  const prepared = handwriting.prepareBoardHandwriting({
    ...item,
    text: item.action.type === "write" ? item.action.text : "",
    fontSize: 23,
  });
  if (!prepared) throw new Error("Expected supported handwriting");
  return prepared;
}

function scene(items: PlacedAction[]) {
  return { width: 1120, height: 600, items };
}

function render(item: PlacedAction, progress = 1) {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(
    createElement(WhiteboardScene, {
      scene: scene([item]),
      stepIndex: 0,
      visibleActionIds: new Set<string>(),
      reveal: { actionId: item.action.id, progress },
    }),
  );
  const action = host.querySelector('[data-action-id="writing"]');
  if (!action) throw new Error("Missing writing action");
  return { host, action };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Board font-contour handwriting", () => {
  it("draws only the current glyph outline before filling it late in its interval", () => {
    const item = placed();
    const prepared = geometry(item);
    const glyph = prepared.glyphs[0];
    for (const fraction of [0, 0.5, 0.9]) {
      const { action } = render(
        item,
        glyph.start + (glyph.end - glyph.start) * fraction,
      );
      const active = action.querySelector("[data-handwriting-active]");
      expect(active?.getAttribute("d")).toBe(glyph.d);
      expect(active?.getAttribute("pathLength")).toBe("1");
      expect(active?.getAttribute("stroke-dasharray")).toBe("1");
      expect(Number(active?.getAttribute("stroke-dashoffset"))).toBeCloseTo(
        1 - fraction,
      );
      expect(Number(active?.getAttribute("fill-opacity"))).toBeCloseTo(
        Math.max(0, (fraction - 0.8) / 0.2),
      );
      expect(active?.getAttribute("visibility")).toBe(
        fraction === 0 ? "hidden" : "visible",
      );
      expect(action.querySelector("[data-handwriting-prefix]")).toBeNull();
      expect(action.querySelector("text,tspan,clipPath")).toBeNull();
      expect(action.querySelectorAll("path")).toHaveLength(2);
    }
  });

  it("uses one filled prefix and one active glyph without a path node for every character", () => {
    const item = placed("ABC");
    const prepared = geometry(item);
    const glyph = prepared.glyphs[1];
    const { action } = render(item, (glyph.start + glyph.end) / 2);
    const prefix = action.querySelector("[data-handwriting-prefix]");
    expect(prefix?.getAttribute("d")).toBe(prepared.glyphs[0].d);
    expect(prefix?.getAttribute("fill")).toBe("#22374a");
    expect(prefix?.hasAttribute("pathLength")).toBe(false);
    expect(
      action.querySelector("[data-handwriting-active]")?.getAttribute("d"),
    ).toBe(glyph.d);
    expect(action.querySelectorAll("path")).toHaveLength(3);
    expect(
      action
        .querySelector("[data-handwriting]")
        ?.getAttribute("data-completed-glyphs"),
    ).toBe("1");
  });

  it("renders completed writing as one sharp aggregate fill with the exact accessible text", () => {
    const item = placed("Collect both terms.");
    const prepared = geometry(item);
    const { host, action } = render(item);
    expect(action.querySelectorAll("path")).toHaveLength(1);
    const full = action.querySelector("[data-handwriting-full]");
    expect(full?.getAttribute("d")).toBe(prepared.fullPath);
    expect(full?.getAttribute("fill")).toBe("#22374a");
    expect(full?.hasAttribute("stroke")).toBe(false);
    expect(full?.getAttribute("class")).toContain("handwritingComplete");
    expect(action.querySelector("title")?.textContent).toBe(
      "Collect both terms.",
    );
    expect(host.querySelector("desc")?.textContent).toBe("Collect both terms.");
    expect(
      action.querySelector(
        "text,tspan,[data-handwriting-active],[data-handwriting-prefix]",
      ),
    ).toBeNull();
    expect(host.querySelector("[aria-live]")).toBeNull();
  });

  it("keeps compiled wrapping and absolute geometry inside a narrow writing box", () => {
    const item = {
      ...placed("Compare every term before collecting both sides."),
      width: 110,
      height: 240,
    };
    const prepared = geometry(item);
    const { action } = render(item);
    expect(prepared.bounds.x).toBeGreaterThanOrEqual(item.x);
    expect(prepared.bounds.y).toBeGreaterThanOrEqual(item.y);
    expect(prepared.bounds.x + prepared.bounds.width).toBeLessThanOrEqual(
      item.x + item.width,
    );
    expect(prepared.bounds.y + prepared.bounds.height).toBeLessThanOrEqual(
      item.y + item.height,
    );
    expect(
      new Set(
        prepared.glyphs
          .filter((glyph) => glyph.d)
          .map((glyph) => glyph.bounds.y),
      ).size,
    ).toBeGreaterThan(2);
    expect(
      action.querySelector("[data-handwriting-full]")?.getAttribute("d"),
    ).toBe(prepared.fullPath);
    expect(action.innerHTML).not.toMatch(/(?:NaN|Infinity|undefined)/u);
  });

  it("has an immediate CSS reduced-motion fallback independent of preference listeners", () => {
    const { action } = render(placed(), 0.2);
    const full = action.querySelector("[data-handwriting-full]");
    expect(full?.getAttribute("class")).toContain("handwritingReduced");
    expect(
      action
        .querySelector("[data-handwriting-active]")
        ?.parentElement?.getAttribute("class"),
    ).toContain("handwritingPartial");
    const css = readFileSync(
      resolve(process.cwd(), "src/components/whiteboard-scene.module.css"),
      "utf8",
    );
    const reduced = css.slice(
      css.indexOf("@media (prefers-reduced-motion: reduce)"),
    );
    expect(css).toMatch(/\.handwritingReduced\s*\{\s*display: none;/u);
    expect(reduced).toMatch(
      /\.handwritingReduced,\s*\.handwritingComplete\s*\{\s*display: inline;/u,
    );
    expect(reduced).toMatch(/\.handwritingPartial\s*\{\s*display: none;/u);
  });

  it.each(["A β step", "Read this 🙂", "A".repeat(241)])(
    "preserves unsupported or oversized writing through readable text fallback: %s",
    (text) => {
      const { host, action } = render(placed(text), 0.5);
      expect(action.querySelector("[data-handwriting]")).toBeNull();
      expect(action.querySelector("text")).not.toBeNull();
      expect(action.querySelector("title")?.textContent).toBe(text);
      expect(host.querySelector("desc")?.textContent).toBe(text);
      expect(action.querySelector("[data-reveal-clip]")).not.toBeNull();
    },
  );

  it("falls back when a writing box cannot hold safe contours", () => {
    const { action } = render({ ...placed(), width: -1 });
    expect(action.querySelector("[data-handwriting]")).toBeNull();
    expect(action.querySelector("text")?.textContent).toBe("AB");
  });

  it("treats markup as literal glyphs and performs no network or HTML insertion", () => {
    const fetch = vi.fn(() => {
      throw new Error("Unexpected network request");
    });
    vi.stubGlobal("fetch", fetch);
    const text = '<script>alert("x")</script>';
    const { host, action } = render(placed(text), 0.6);
    expect(action.querySelector("[data-handwriting]")).not.toBeNull();
    expect(action.querySelector("title")?.textContent).toBe(text);
    expect(host.querySelector("desc")?.textContent).toBe(text);
    expect(
      host.querySelector("script,img,a,iframe,object,[onerror],[onclick]"),
    ).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("prepares geometry once across frames and keeps completed prefix nodes stable", () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const prepare = vi.spyOn(handwriting, "prepareBoardHandwriting");
    const item = placed("ABC");
    const prepared = geometry(item);
    prepare.mockClear();
    const stableScene = scene([item]);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const draw = (progress: number) => {
      act(() =>
        root.render(
          createElement(WhiteboardScene, {
            scene: stableScene,
            stepIndex: 0,
            visibleActionIds: new Set<string>(),
            reveal: { actionId: "writing", progress },
          }),
        ),
      );
    };
    try {
      const glyph = prepared.glyphs[1];
      draw(glyph.start + (glyph.end - glyph.start) * 0.1);
      const prefix = host.querySelector("[data-handwriting-prefix]");
      const fullPath = host
        .querySelector("[data-handwriting-full]")
        ?.getAttribute("d");
      for (const fraction of [0.2, 0.4, 0.6, 0.9]) {
        draw(glyph.start + (glyph.end - glyph.start) * fraction);
        expect(host.querySelector("[data-handwriting-prefix]")).toBe(prefix);
        expect(prefix?.getAttribute("d")).toBe(prepared.glyphs[0].d);
        expect(
          host.querySelector("[data-handwriting-full]")?.getAttribute("d"),
        ).toBe(fullPath);
      }
      draw(1);
      expect(prepare).toHaveBeenCalledTimes(1);
      expect(
        host.querySelectorAll('[data-action-id="writing"] path'),
      ).toHaveLength(1);
    } finally {
      act(() => root.unmount());
      host.remove();
    }
  });
});
