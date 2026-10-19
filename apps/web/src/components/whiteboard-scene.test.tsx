import { JSDOM } from "jsdom";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { whiteboardLessons } from "../lib/whiteboard-lessons";
import { layoutLesson, type PlacedAction } from "../lib/whiteboard-tutor";
import { WhiteboardScene, type WhiteboardSceneProps } from "./whiteboard-scene";

function render(props: WhiteboardSceneProps) {
  const html = renderToStaticMarkup(createElement(WhiteboardScene, props));
  return { html, document: new JSDOM(html).window.document };
}

function scene(items: PlacedAction[]) {
  return { width: 1120, height: 600, items };
}

const anchors: PlacedAction[] = [
  {
    action: {
      id: "first-box",
      type: "shape",
      shape: "box",
      label: "Starting state",
      place: "top-left",
    },
    stepIndex: 0,
    x: 40,
    y: 40,
    width: 200,
    height: 100,
  },
  {
    action: {
      id: "next-ellipse",
      type: "shape",
      shape: "ellipse",
      label: "Next state",
      place: "right-of:first-box",
    },
    stepIndex: 1,
    x: 340,
    y: 40,
    width: 200,
    height: 100,
  },
  {
    action: {
      id: "state-arrow",
      type: "arrow",
      from: "first-box",
      to: "next-ellipse",
      label: "Continue",
    },
    stepIndex: 1,
    x: 240,
    y: 90,
    width: 100,
    height: 0,
  },
];

describe("whiteboard SVG scene", () => {
  it.each(whiteboardLessons)(
    "renders the complete $title lesson with accessible action descriptions",
    (lesson) => {
      const laidOut = layoutLesson(lesson);
      const { document, html } = render({
        scene: laidOut,
        stepIndex: lesson.steps.length - 1,
      });
      const svg = document.querySelector("svg");
      expect(svg?.getAttribute("role")).toBe("img");
      expect(svg?.getAttribute("viewBox")).toBe(
        `0 0 ${laidOut.width} ${laidOut.height}`,
      );
      const titleId = svg?.getAttribute("aria-labelledby");
      const descriptionId = svg?.getAttribute("aria-describedby");
      expect(document.getElementById(titleId || "")?.textContent).toBe(
        "Lesson whiteboard",
      );
      expect(
        document.getElementById(descriptionId || "")?.textContent?.length,
      ).toBeGreaterThan(20);
      expect(document.querySelectorAll("g[data-action-id]")).toHaveLength(
        laidOut.items.length,
      );
      for (const item of laidOut.items) {
        const action = document.querySelector(
          `[data-action-id="${item.action.id}"]`,
        );
        expect(action?.querySelector("title")?.textContent).toBeTruthy();
      }
      expect(html).not.toMatch(/(?:NaN|Infinity|undefined)/u);
      expect(document.querySelector("script,img,iframe,a,canvas")).toBeNull();
    },
  );

  it("retains earlier steps while revealing only the selected current-step prefix", () => {
    const { document } = render({
      scene: scene(anchors),
      stepIndex: 1,
      visibleActionIds: new Set(["first-box", "next-ellipse"]),
    });
    expect(
      document.querySelector('[data-action-id="first-box"]'),
    ).not.toBeNull();
    expect(
      document
        .querySelector('[data-action-id="next-ellipse"]')
        ?.getAttribute("data-active-step"),
    ).toBe("true");
    expect(
      document
        .querySelector('[data-action-id="first-box"]')
        ?.hasAttribute("data-active-step"),
    ).toBe(false);
    expect(document.querySelector('[data-action-id="state-arrow"]')).toBeNull();
    const earlier = render({ scene: scene(anchors), stepIndex: 0 }).document;
    expect(earlier.querySelector('[data-action-id="next-ellipse"]')).toBeNull();
    expect(earlier.querySelector('[data-action-id="state-arrow"]')).toBeNull();
  });

  it("never reveals an arrow or highlight whose referenced content is still hidden", () => {
    const highlight: PlacedAction = {
      action: {
        id: "future-highlight",
        type: "highlight",
        target: "next-ellipse",
        style: "wash",
      },
      stepIndex: 1,
      x: 330,
      y: 30,
      width: 220,
      height: 120,
    };
    const { document } = render({
      scene: scene([...anchors, highlight]),
      stepIndex: 1,
      visibleActionIds: new Set([
        "first-box",
        "state-arrow",
        "future-highlight",
      ]),
    });
    expect(document.querySelectorAll("g[data-action-id]")).toHaveLength(1);
    expect(document.querySelector('[data-action-id="state-arrow"]')).toBeNull();
    expect(
      document.querySelector('[data-action-id="future-highlight"]'),
    ).toBeNull();
  });

  it("uses repeatable seeded sketch paths and connects arrows to anchor edges", () => {
    const first = render({ scene: scene(anchors), stepIndex: 1 }).document;
    const second = render({ scene: scene(anchors), stepIndex: 1 }).document;
    const paths = (document: Document) =>
      Array.from(document.querySelectorAll("path"), (path) =>
        path.getAttribute("d"),
      );
    expect(paths(first)).toEqual(paths(second));
    const arrowPaths = first.querySelectorAll(
      '[data-action-id="state-arrow"] path',
    );
    expect(arrowPaths.length).toBeGreaterThan(1);
    expect(arrowPaths[arrowPaths.length - 1]?.getAttribute("d")).toContain(
      "L 340 90 L",
    );
    // The line starts at the edge of the 200px box, rather than its center.
    const sketch = arrowPaths[0]?.getAttribute("d") || "";
    const firstX = Number(/M\s*([\d.]+)/u.exec(sketch)?.[1]);
    expect(firstX).toBeGreaterThan(235);
    expect(firstX).toBeLessThan(245);
  });

  it("ends diagonal arrows on an ellipse perimeter rather than its bounding box", () => {
    const items: PlacedAction[] = anchors.map((item) => ({ ...item }));
    items[0] = { ...items[0], x: 40, y: 300 };
    const { document } = render({ scene: scene(items), stepIndex: 1 });
    const paths = document.querySelectorAll(
      '[data-action-id="state-arrow"] path',
    );
    const head = paths[paths.length - 1]?.getAttribute("d") || "";
    const tip = /L ([\d.e+-]+) ([\d.e+-]+) L/u.exec(head);
    expect(tip).not.toBeNull();
    const x = Number(tip?.[1]);
    const y = Number(tip?.[2]);
    expect(((x - 440) / 100) ** 2 + ((y - 90) / 50) ** 2).toBeCloseTo(1, 8);
  });

  it.each(["circle", "underline", "wash"] as const)(
    "draws %s emphasis from resolved target bounds",
    (style) => {
      const highlight: PlacedAction = {
        action: {
          id: "target-emphasis",
          type: "highlight",
          target: "first-box",
          style,
        },
        stepIndex: 1,
        x: 30,
        y: 30,
        width: 220,
        height: 120,
      };
      const { document } = render({
        scene: scene([anchors[0], highlight]),
        stepIndex: 1,
      });
      const group = document.querySelector(
        '[data-action-id="target-emphasis"]',
      );
      expect(group?.querySelector("title")?.textContent).toContain(
        `${style} emphasis`,
      );
      if (style === "wash") {
        const rectangle = group?.querySelector("rect");
        expect(rectangle?.getAttribute("x")).toBe("30");
        expect(rectangle?.getAttribute("width")).toBe("220");
        expect(Number(rectangle?.getAttribute("fill-opacity"))).toBeLessThan(
          0.5,
        );
      } else {
        expect(group?.querySelector("path")?.getAttribute("d")).toBeTruthy();
      }
    },
  );

  it.each(["linear", "quadratic", "sine"] as const)(
    "samples a bounded %s plot behind a unique clip path",
    (kind) => {
      const plot: PlacedAction = {
        action: {
          id: "function-plot",
          type: "plot",
          kind,
          domain: [-2, 2],
          range: [-2, 4],
          label: `A ${kind} function`,
          place: "next-row",
        },
        stepIndex: 0,
        x: 40,
        y: 40,
        width: 480,
        height: 280,
      };
      const { document, html } = render({ scene: scene([plot]), stepIndex: 0 });
      const path = document.querySelector(`[data-plot-kind="${kind}"]`);
      const clip = document.querySelector("clipPath");
      expect(path?.getAttribute("clip-path")).toBe(`url(#${clip?.id})`);
      expect((path?.getAttribute("d")?.match(/L /gu) || []).length).toBe(180);
      expect(clip?.querySelector("rect")?.getAttribute("width")).toBe("416");
      expect(html).not.toMatch(/(?:NaN|Infinity)/u);
      const groupText = document.querySelector(
        '[data-action-id="function-plot"]',
      )?.textContent;
      expect(groupText).toContain("x from -2 to 2");
    },
  );

  it("renders fractions as actual accessible math, with no remote asset or HTML input", () => {
    const math: PlacedAction = {
      action: {
        id: "fraction",
        type: "math",
        latex: "\\frac{x^2}{2} = 8",
        place: "next-row",
      },
      stepIndex: 0,
      x: 40,
      y: 40,
      width: 800,
      height: 128,
    };
    const { document } = render({ scene: scene([math]), stepIndex: 0 });
    expect(document.querySelector("foreignObject math mfrac")).not.toBeNull();
    expect(document.querySelector("annotation")?.textContent).toBe(
      "\\frac{x^2}{2} = 8",
    );
    expect(
      document.querySelector("foreignObject img,foreignObject a,script"),
    ).toBeNull();
  });

  it("escapes plain writing and handles malformed or untrusted math without active HTML", () => {
    const write: PlacedAction = {
      action: {
        id: "literal-text",
        type: "write",
        text: '<img src="x" onerror="alert(1)">',
        place: "top-left",
      },
      stepIndex: 0,
      x: 40,
      y: 40,
      width: 480,
      height: 100,
    };
    const math = (latex: string): PlacedAction => ({
      action: { id: "untrusted-math", type: "math", latex, place: "next-row" },
      stepIndex: 0,
      x: 40,
      y: 160,
      width: 800,
      height: 128,
    });
    for (const latex of [
      "\\frac{",
      "\\href{javascript:alert(1)}{Click}",
      "\\htmlClass{bad}{x}",
    ]) {
      const { document } = render({
        scene: scene([write, math(latex)]),
        stepIndex: 0,
      });
      expect(document.querySelector("img,script,a,iframe,object")).toBeNull();
      expect(
        document.querySelector('[data-action-id="literal-text"] text')
          ?.textContent,
      ).toContain("onerror");
    }
    const fallback = render({
      scene: scene([math("\\frac{")]),
      stepIndex: 0,
    }).document;
    expect(fallback.querySelector("foreignObject")).toBeNull();
    expect(fallback.querySelector("text")?.textContent).toContain("Equation: ");
  });

  it("gives an unrevealed board an accessible empty state and performs no network work", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const { document } = render({ scene: scene(anchors), stepIndex: -1 });
      expect(document.querySelectorAll("g[data-action-id]")).toHaveLength(0);
      expect(document.querySelector("desc")?.textContent).toContain(
        "Play the lesson",
      );
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
});
