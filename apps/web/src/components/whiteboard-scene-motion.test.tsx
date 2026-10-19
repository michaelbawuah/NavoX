// @vitest-environment jsdom

import katex from "katex";
import { act, createElement, Fragment } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import rough from "roughjs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type PlacedAction, wrapBoardText } from "../lib/whiteboard-tutor";
import { WhiteboardScene, type WhiteboardSceneProps } from "./whiteboard-scene";

function placed(action: PlacedAction["action"], x = 40): PlacedAction {
  return { action, stepIndex: 0, x, y: 40, width: 200, height: 140 };
}

const writing = placed({
  id: "writing",
  type: "write",
  text: "First we collect the starting terms then compare both sides.",
  place: "top-left",
});
const equation = placed({
  id: "equation",
  type: "math",
  latex: "\\frac{x^2}{2} = 8",
  place: "next-row",
});
const box = placed({
  id: "box",
  type: "shape",
  shape: "box",
  label: "Starting state",
  place: "top-left",
});
const ellipse = placed(
  {
    id: "ellipse",
    type: "shape",
    shape: "ellipse",
    label: "Next state",
    place: "right-of:box",
  },
  340,
);
const arrow = placed({
  id: "arrow",
  type: "arrow",
  from: "box",
  to: "ellipse",
  label: "Continue",
});
const graph = {
  ...placed({
    id: "graph",
    type: "plot",
    kind: "quadratic",
    domain: [-2, 2],
    range: [-2, 4],
    label: "Compare the curve",
    place: "next-row",
  }),
  width: 480,
  height: 280,
};

function scene(items: PlacedAction[]) {
  return { width: 1120, height: 600, items };
}

function render(
  items: PlacedAction[],
  reveal?: WhiteboardSceneProps["reveal"],
  visibleActionIds?: ReadonlySet<string>,
  stepIndex = 0,
) {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(
    createElement(WhiteboardScene, {
      scene: scene(items),
      stepIndex,
      visibleActionIds,
      reveal,
    }),
  );
  return host;
}

function group(host: HTMLElement, id: string) {
  const found = host.querySelector<SVGGElement>(`g[data-action-id="${id}"]`);
  if (!found) throw new Error(`Missing action ${id}`);
  return found;
}

function clipRows(host: Element) {
  return [...host.querySelectorAll<SVGRectElement>("clipPath rect")];
}

function progress(host: Element) {
  return Number(host.getAttribute("data-motion-progress"));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("progressive SVG Board presentation", () => {
  it("includes only the next uncommitted action and never rewinds committed actions", () => {
    const items = [box, ellipse, arrow];
    const committed = new Set(["box"]);
    const partial = render(
      items,
      { actionId: "ellipse", progress: 0.4 },
      committed,
    );
    expect(group(partial, "ellipse").getAttribute("data-reveal-progress")).toBe(
      "0.4",
    );
    expect(group(partial, "box").hasAttribute("data-reveal-progress")).toBe(
      false,
    );
    expect(partial.querySelector('[data-action-id="arrow"]')).toBeNull();
    for (const actionId of ["box", "arrow", "unknown"]) {
      const skipped = render(items, { actionId, progress: 0.4 }, committed);
      expect(skipped.querySelectorAll("g[data-action-id]")).toHaveLength(1);
      expect(group(skipped, "box").hasAttribute("data-reveal-progress")).toBe(
        false,
      );
    }
    const future = render(
      [box, { ...ellipse, stepIndex: 1 }],
      { actionId: "ellipse", progress: 0.4 },
      committed,
    );
    expect(future.querySelector('[data-action-id="ellipse"]')).toBeNull();
  });

  it("keeps the hidden-reference guard for an active arrow or highlight", () => {
    const wash = placed({
      id: "wash",
      type: "highlight",
      target: "ellipse",
      style: "wash",
    });
    for (const item of [arrow, wash]) {
      const host = render(
        [box, item, ellipse],
        { actionId: item.action.id, progress: 0.5 },
        new Set(["box"]),
      );
      expect(host.querySelectorAll("g[data-action-id]")).toHaveLength(1);
      expect(
        host.querySelector(`[data-action-id="${item.action.id}"]`),
      ).toBeNull();
    }
  });

  it.each([
    [-3, 0],
    [3, 1],
    [Number.NaN, 0],
    [Number.POSITIVE_INFINITY, 0],
    [Number.NEGATIVE_INFINITY, 0],
  ])(
    "bounds progress %s to %s without invalid SVG numbers",
    (value, expected) => {
      const host = render(
        [writing],
        { actionId: "writing", progress: value },
        new Set(),
      );
      expect(group(host, "writing").getAttribute("data-reveal-progress")).toBe(
        String(expected),
      );
      for (const rectangle of clipRows(host)) {
        expect(Number(rectangle.getAttribute("width"))).toBeGreaterThanOrEqual(
          0,
        );
        expect(Number(rectangle.getAttribute("width"))).toBeLessThanOrEqual(
          206,
        );
      }
      expect(host.innerHTML).not.toMatch(/(?:NaN|Infinity|undefined)/u);
    },
  );

  it("keeps unsupported writing readable through the existing line reveal", () => {
    const fallbackWriting: PlacedAction = {
      ...writing,
      action: {
        ...writing.action,
        type: "write",
        text: "First β we collect the starting terms then compare both sides.",
        place: "top-left",
      },
    };
    const lines = wrapBoardText(
      fallbackWriting.action.type === "write"
        ? fallbackWriting.action.text
        : "",
      Math.floor(writing.width / (23 * 0.57)),
    );
    expect(lines.length).toBeGreaterThan(2);
    for (const fraction of [0, 1 / lines.length, 0.5, 1]) {
      const host = render(
        [fallbackWriting],
        { actionId: "writing", progress: fraction },
        new Set(),
      );
      const action = group(host, "writing");
      const rows = clipRows(action);
      expect(rows).toHaveLength(lines.length);
      for (const [index, row] of rows.entries()) {
        const expected = Math.max(
          0,
          Math.min(1, fraction * rows.length - index),
        );
        expect(progress(row)).toBeCloseTo(expected, 8);
        expect(Number(row.getAttribute("width"))).toBeCloseTo(
          206 * expected,
          8,
        );
        const next = rows[index + 1];
        if (next) {
          expect(
            Number(row.getAttribute("y")) + Number(row.getAttribute("height")),
          ).toBeCloseTo(Number(next.getAttribute("y")), 8);
        }
      }
      expect(action.querySelector("title")?.textContent).toBe(
        fallbackWriting.action.type === "write"
          ? fallbackWriting.action.text
          : "",
      );
      expect(action.querySelectorAll("tspan")).toHaveLength(lines.length);
      expect(
        action.querySelector("[data-reveal-clip]")?.hasAttribute("clip-path"),
      ).toBe(fraction < 1);
      expect(host.querySelector("[aria-live]")).toBeNull();
    }
  });

  it("clips safe, fully accessible math left to right and restores final overflow", () => {
    for (const fraction of [0, 0.4, 1]) {
      const host = render(
        [equation],
        { actionId: "equation", progress: fraction },
        new Set(),
      );
      const action = group(host, "equation");
      expect(action.querySelector("math mfrac")).not.toBeNull();
      expect(action.querySelector("annotation")?.textContent).toBe(
        "\\frac{x^2}{2} = 8",
      );
      expect(Number(clipRows(action)[0]?.getAttribute("width"))).toBeCloseTo(
        equation.width * fraction,
      );
      expect(
        action.querySelector("[data-reveal-clip]")?.hasAttribute("clip-path"),
      ).toBe(fraction < 1);
      expect(
        action
          .querySelector("foreignObject > div")
          ?.className.includes("mathClipped"),
      ).toBe(fraction < 1);
      expect(action.querySelector("script,a,img,iframe")).toBeNull();
    }
  });

  it("draws a shape outline before the fill and the label", () => {
    for (const fraction of [0, 0.35, 0.8, 0.93, 1]) {
      const action = group(
        render([box], { actionId: "box", progress: fraction }, new Set()),
        "box",
      );
      const outlines = [...action.querySelectorAll("path[pathLength]")];
      const fill = action.querySelector('path[fill="#eef2e9"]')?.parentElement;
      expect(outlines.length).toBeGreaterThan(0);
      expect(fill).not.toBeNull();
      for (const path of outlines) {
        expect(path.getAttribute("pathLength")).toBe("1");
        expect(path.getAttribute("stroke-dasharray")).toBe("1");
        expect(
          Number(path.getAttribute("stroke-dashoffset")),
        ).toBeGreaterThanOrEqual(0);
        expect(
          Number(path.getAttribute("stroke-dashoffset")),
        ).toBeLessThanOrEqual(1);
      }
      expect(Number(fill?.getAttribute("opacity"))).toBeCloseTo(
        Math.max(0, Math.min(1, (fraction - 0.72) / 0.13)),
      );
      const labelProgress = progress(clipRows(action)[0]);
      expect(labelProgress).toBeCloseTo(
        Math.max(0, Math.min(1, (fraction - 0.85) / 0.15)),
      );
      if (fraction >= 0.72) {
        expect(outlines.every((path) => progress(path) === 1)).toBe(true);
      }
    }
  });

  it("completes an arrow stem before drawing its head and label", () => {
    for (const fraction of [0, 0.35, 0.78, 0.93, 1]) {
      const host = render(
        [box, ellipse, arrow],
        { actionId: "arrow", progress: fraction },
        new Set(["box", "ellipse"]),
      );
      const action = group(host, "arrow");
      const paths = [...action.querySelectorAll("path")];
      const head = paths[paths.length - 1];
      expect(progress(head)).toBeCloseTo(
        Math.max(0, Math.min(1, (fraction - 0.7) / 0.15)),
      );
      expect(progress(clipRows(action)[0])).toBeCloseTo(
        Math.max(0, Math.min(1, (fraction - 0.85) / 0.15)),
      );
      if (fraction >= 0.7) {
        expect(paths.slice(0, -1).every((path) => progress(path) === 1)).toBe(
          true,
        );
      }
    }
  });

  it.each(["circle", "underline", "wash"] as const)(
    "progressively reveals %s emphasis without changing its target",
    (style) => {
      const highlight = placed({
        id: "emphasis",
        type: "highlight",
        target: "box",
        style,
      });
      const host = render(
        [box, highlight],
        { actionId: "emphasis", progress: 0.5 },
        new Set(["box"]),
      );
      const action = group(host, "emphasis");
      expect(action.querySelector("title")?.textContent).toBe(
        `${style} emphasis on box`,
      );
      if (style === "wash") {
        expect(Number(clipRows(action)[0]?.getAttribute("width"))).toBe(100);
        expect(
          action.querySelector("[data-reveal-clip]")?.getAttribute("clip-path"),
        ).toMatch(/^url\(#.+\)$/u);
      } else {
        const paths = [...action.querySelectorAll("path[pathLength]")];
        expect(paths.length).toBeGreaterThan(0);
        expect(paths.some((path) => progress(path) < 1)).toBe(true);
        expect(
          paths.every(
            (path) => Number(path.getAttribute("stroke-dashoffset")) <= 1,
          ),
        ).toBe(true);
      }
    },
  );

  it("draws the graph axes before its clipped curve and retains its bounds", () => {
    for (const fraction of [0, 0.3, 0.6, 1]) {
      const host = render(
        [graph],
        { actionId: "graph", progress: fraction },
        new Set(),
      );
      const action = group(host, "graph");
      const curve = action.querySelector("[data-plot-kind]");
      if (!curve) throw new Error("Missing curve");
      expect(progress(curve)).toBeCloseTo(
        Math.max(0, Math.min(1, (fraction - 0.4) / 0.45)),
      );
      const bounds = action.querySelector("clipPath rect");
      expect(bounds?.getAttribute("width")).toBe("416");
      expect(bounds?.getAttribute("height")).toBe("212");
      expect(curve.getAttribute("clip-path")).toBe(
        `url(#${bounds?.parentElement?.id})`,
      );
      expect((curve.getAttribute("d")?.match(/L /gu) || []).length).toBe(180);
      expect(action.querySelector("title")?.textContent).toContain(
        "x from -2 to 2",
      );
    }
  });

  it("uses unique clip IDs for multiple scenes containing the same actions", () => {
    const props: WhiteboardSceneProps = {
      scene: scene([writing, equation, graph]),
      stepIndex: 0,
      reveal: { actionId: "equation", progress: 0.5 },
    };
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(
      createElement(
        Fragment,
        null,
        createElement(WhiteboardScene, props),
        createElement(WhiteboardScene, props),
      ),
    );
    const ids = [...host.querySelectorAll("[id]")].map((item) => item.id);
    expect(ids.length).toBeGreaterThan(8);
    expect(new Set(ids).size).toBe(ids.length);
    for (const clipped of host.querySelectorAll("[clip-path]")) {
      const id = clipped.getAttribute("clip-path")?.slice(5, -1);
      expect(
        [...host.querySelectorAll("clipPath")].some((clip) => clip.id === id),
      ).toBe(true);
    }
  });

  it("leaves completed actions unchanged while another action progresses", () => {
    const items = [box, ellipse, arrow];
    const committed = new Set(["box", "ellipse"]);
    const before = render(
      items,
      { actionId: "arrow", progress: 0.1 },
      committed,
    );
    const after = render(
      items,
      { actionId: "arrow", progress: 0.8 },
      committed,
    );
    expect(group(before, "box").outerHTML).toBe(group(after, "box").outerHTML);
    expect(group(before, "ellipse").outerHTML).toBe(
      group(after, "ellipse").outerHTML,
    );
    expect(group(before, "arrow").outerHTML).not.toBe(
      group(after, "arrow").outerHTML,
    );
  });

  it("keeps KaTeX generation and seeded rough geometry outside per-frame work", () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const math = vi.spyOn(katex, "renderToString");
    const prototype = Object.getPrototypeOf(rough.generator()) as ReturnType<
      typeof rough.generator
    >;
    const rectangle = vi.spyOn(prototype, "rectangle");
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const stableScene = scene([equation, box]);
    const draw = (actionId: string, value: number, committed: string[]) => {
      act(() =>
        root.render(
          createElement(WhiteboardScene, {
            scene: stableScene,
            stepIndex: 0,
            visibleActionIds: new Set(committed),
            reveal: { actionId, progress: value },
          }),
        ),
      );
    };
    try {
      for (const fraction of [0, 0.1, 0.5, 0.9, 1]) {
        draw("equation", fraction, []);
      }
      expect(math).toHaveBeenCalledTimes(1);
      expect(rectangle).not.toHaveBeenCalled();
      for (const fraction of [0, 0.2, 0.5, 0.8, 1]) {
        draw("box", fraction, ["equation"]);
      }
      expect(math).toHaveBeenCalledTimes(1);
      expect(rectangle).toHaveBeenCalledTimes(1);
      expect(group(host, "box").getAttribute("data-reveal-progress")).toBe("1");
    } finally {
      act(() => root.unmount());
      host.remove();
    }
  });
});
