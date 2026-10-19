// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { whiteboardLessons } from "../lib/whiteboard-lessons";
import { layoutLesson } from "../lib/whiteboard-tutor";
import { StudentWhiteboard } from "./student-whiteboard";

let host: HTMLDivElement;
let root: Root;
let fetchCalls: ReturnType<typeof vi.fn>;

function button(name: string): HTMLButtonElement {
  const found = Array.from(host.querySelectorAll("button")).find(
    (item) =>
      (item.getAttribute("aria-label") || item.textContent?.trim()) === name,
  );
  if (!found) throw new Error(`Missing button: ${name}`);
  return found;
}

function click(element: HTMLElement) {
  act(() => element.click());
}

function tick() {
  // Commit one pending playback event so its next effect schedules independently.
  act(() => vi.runOnlyPendingTimers());
}

function actions() {
  return Array.from(host.querySelectorAll("g[data-action-id]"), (item) =>
    item.getAttribute("data-action-id"),
  );
}

function timeline(index: number) {
  const item = host.querySelectorAll<HTMLButtonElement>(
    'nav[aria-label="Lesson timeline"] button',
  )[index];
  if (!item) throw new Error(`Missing timeline step: ${index}`);
  return item;
}

function chooseLesson(id: string) {
  const picker = host.querySelector<HTMLSelectElement>("#whiteboard-lesson");
  if (!picker) throw new Error("Missing lesson picker");
  act(() => {
    picker.value = id;
    picker.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function zoom() {
  return host.querySelector('output[aria-label="Board zoom"]')?.textContent;
}

function ink() {
  const svg = host.querySelector<SVGSVGElement>(
    'svg[aria-label="Your pen annotations"]',
  );
  if (!svg) throw new Error("Missing annotation layer");
  return svg;
}

function pointer(
  element: Element,
  type: string,
  clientX: number,
  clientY: number,
  pointerId = 7,
) {
  // jsdom has no real pointer capture/layout; supply geometry and dispatch the
  // same coordinates/id the browser passes to React's pointer handlers.
  const event = new MouseEvent(type, {
    bubbles: true,
    button: 0,
    clientX,
    clientY,
  });
  Object.defineProperties(event, {
    pointerId: { value: pointerId },
    pointerType: { value: "pen" },
  });
  act(() => element.dispatchEvent(event));
}

function prepareInk() {
  const svg = ink();
  const [, , width, height] = (svg.getAttribute("viewBox") || "")
    .split(" ")
    .map(Number);
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON: () => ({}),
  });
  Object.defineProperty(svg, "setPointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
  return svg;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  fetchCalls = vi.fn(() => {
    throw new Error(
      "Guided whiteboard lessons must not request a network resource",
    );
  });
  vi.stubGlobal("fetch", fetchCalls);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root.render(createElement(StudentWhiteboard)));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  expect(fetchCalls).not.toHaveBeenCalled();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("interactive guided whiteboard", () => {
  it("starts paused, reveals actions progressively and preserves the pause", () => {
    expect(host.textContent).toContain("One bit, one clock edge");
    expect(actions()).toEqual([]);
    expect(button("Previous step").disabled).toBe(true);
    click(button("Play lesson"));
    expect(button("Pause lesson").disabled).toBe(false);
    tick();
    expect(actions()).toEqual(["ece-memory"]);
    click(button("Pause lesson"));
    act(() => vi.advanceTimersByTime(60_000));
    expect(actions()).toEqual(["ece-memory"]);
    expect(timeline(0).getAttribute("aria-current")).toBe("step");
    click(button("Play lesson"));
    tick();
    expect(actions()).toEqual(["ece-memory", "ece-input"]);
    tick();
    expect(actions()).toEqual(["ece-memory", "ece-input", "ece-wire"]);
    tick();
    expect(timeline(1).getAttribute("aria-current")).toBe("step");
    expect(actions()).toEqual(["ece-memory", "ece-input", "ece-wire"]);
  });

  it("waits at a quick check until an explicit answer, then requires play to continue", () => {
    chooseLesson("calculus-parts");
    click(button("Start this lesson"));
    tick();
    expect(actions()).toEqual(["parts-problem"]);
    expect(host.querySelector('[aria-label="Quick check"]')).not.toBeNull();
    expect(host.textContent).toContain("Take your time. Choose an answer");
    expect(button("Play lesson").disabled).toBe(true);
    click(button("Play lesson"));
    act(() => vi.advanceTimersByTime(60_000));
    expect(timeline(0).getAttribute("aria-current")).toBe("step");
    expect(host.textContent).not.toContain("Let’s look at that.");
    click(button("u = e^x"));
    expect(button("u = e^x").getAttribute("aria-pressed")).toBe("true");
    expect(host.textContent).toContain("Let’s look at that.");
    expect(host.textContent).toContain("Differentiating x gives 1");
    expect(button("Play lesson").disabled).toBe(false);
    act(() => vi.advanceTimersByTime(60_000));
    expect(timeline(0).getAttribute("aria-current")).toBe("step");
    click(button("Play lesson"));
    tick();
    expect(timeline(1).getAttribute("aria-current")).toBe("step");
    expect(actions()).toEqual(["parts-problem"]);
  });

  it("replays the current step while keeping earlier work and supports timeline jumps", () => {
    click(timeline(1));
    expect(actions()).toEqual([
      "ece-memory",
      "ece-input",
      "ece-wire",
      "ece-rule",
      "ece-hold",
      "ece-emphasis",
    ]);
    expect(button("Play lesson").disabled).toBe(true);
    click(button("Replay step"));
    expect(actions()).toEqual(["ece-memory", "ece-input", "ece-wire"]);
    expect(button("Pause lesson").disabled).toBe(false);
    tick();
    expect(actions()).toContain("ece-rule");
    expect(actions()).not.toContain("ece-hold");
    click(timeline(2));
    expect(actions()).toContain("ece-example-line");
    expect(timeline(2).getAttribute("aria-current")).toBe("step");
    expect(button("Next step").disabled).toBe(true);
    click(button("Previous step"));
    expect(actions()).not.toContain("ece-example-line");
    click(timeline(0));
    expect(actions()).toEqual(["ece-memory", "ece-input", "ece-wire"]);
    expect(timeline(0).getAttribute("aria-current")).toBe("step");
  });

  it("completes only after playback, and restart clears answers and step markers", () => {
    chooseLesson("calculus-parts");
    click(timeline(0));
    click(button("u = x"));
    expect(host.textContent).toContain("That’s it.");
    click(button("Mark this step for another look"));
    expect(button("Marked for another look").getAttribute("aria-pressed")).toBe(
      "true",
    );
    click(timeline(2));
    expect(host.textContent).not.toContain("Walkthrough complete.");
    click(button("Play lesson"));
    tick();
    expect(button("Lesson complete").disabled).toBe(true);
    expect(host.textContent).toContain("Walkthrough complete.");
    click(button("Restart lesson"));
    expect(actions()).toEqual([]);
    expect(timeline(0).getAttribute("aria-current")).toBe("step");
    expect(host.textContent).not.toContain("Walkthrough complete.");
    expect(host.textContent).not.toContain("That’s it.");
    expect(
      button("Mark this step for another look").getAttribute("aria-pressed"),
    ).toBe("false");
    click(button("Play lesson"));
    tick();
    expect(button("Play lesson").disabled).toBe(true);
    expect(button("u = x").getAttribute("aria-pressed")).toBe("false");
  });

  it("bounds zoom, fits the board and resets lesson-local controls when selecting another lesson", () => {
    expect(zoom()).toBe("100%");
    expect(button("Zoom out").disabled).toBe(true);
    for (let index = 0; index < 8; index += 1) click(button("Zoom in"));
    expect(zoom()).toBe("250%");
    expect(button("Zoom in").disabled).toBe(true);
    click(button("Zoom out"));
    expect(zoom()).toBe("225%");
    const viewport = host.querySelector<HTMLElement>('[aria-label^="Board."]');
    if (!viewport) throw new Error("Missing viewport");
    viewport.scrollLeft = 80;
    viewport.scrollTop = 120;
    click(button("Fit board"));
    expect(zoom()).toBe("100%");
    expect(viewport.scrollLeft).toBe(0);
    expect(viewport.scrollTop).toBe(0);
    click(button("Zoom in"));
    click(button("Pen"));
    click(button("Mark this step for another look"));
    chooseLesson("calculus-parts");
    expect(zoom()).toBe("100%");
    expect(button("Pan").getAttribute("aria-pressed")).toBe("true");
    expect(button("Pen").getAttribute("aria-pressed")).toBe("false");
    expect(actions()).toEqual([]);
    expect(timeline(0).getAttribute("aria-current")).toBe("step");
    expect(
      button("Mark this step for another look").getAttribute("aria-pressed"),
    ).toBe("false");
    chooseLesson("ece-flip-flop");
    expect(actions()).toEqual([]);
    expect(
      button("Mark this step for another look").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("pauses for pen input and supports undo, clear and lesson-local annotation cleanup", () => {
    expect(button("Undo stroke").disabled).toBe(true);
    expect(button("Clear drawings").disabled).toBe(true);
    click(button("Play lesson"));
    tick();
    click(button("Pen"));
    expect(button("Play lesson").disabled).toBe(false);
    const svg = prepareInk();
    pointer(svg, "pointerdown", 40, 60);
    pointer(svg, "pointermove", 100, 120);
    pointer(svg, "pointerup", 100, 120);
    expect(svg.querySelectorAll("path")).toHaveLength(1);
    expect(svg.querySelector("path")?.getAttribute("d")).toContain(
      "L100.0,120.0",
    );
    pointer(svg, "pointerdown", 80, 90);
    pointer(svg, "pointermove", 120, 140);
    pointer(svg, "pointerup", 120, 140);
    expect(svg.querySelectorAll("path")).toHaveLength(2);
    click(button("Undo stroke"));
    expect(svg.querySelectorAll("path")).toHaveLength(1);
    click(button("Clear drawings"));
    expect(svg.querySelectorAll("path")).toHaveLength(0);
    expect(button("Undo stroke").disabled).toBe(true);
    pointer(svg, "pointerdown", 50, 60);
    pointer(svg, "pointerup", 50, 60);
    expect(svg.querySelectorAll("path")).toHaveLength(1);
    click(button("Restart lesson"));
    expect(svg.querySelectorAll("path")).toHaveLength(0);
    pointer(svg, "pointerdown", 50, 60);
    pointer(svg, "pointerup", 50, 60);
    chooseLesson("calculus-parts");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
  });

  it("keeps a pen stroke owned by its starting pointer until that pointer ends", () => {
    click(button("Pen"));
    const svg = prepareInk();
    pointer(svg, "pointerdown", 40, 60, 7);
    const original = svg.querySelector("path")?.getAttribute("d");
    pointer(svg, "pointerdown", 200, 220, 8);
    pointer(svg, "pointermove", 220, 240, 8);
    pointer(svg, "pointerup", 220, 240, 8);
    expect(svg.querySelectorAll("path")).toHaveLength(1);
    expect(svg.querySelector("path")?.getAttribute("d")).toBe(original);
    pointer(svg, "pointermove", 100, 120, 7);
    expect(svg.querySelector("path")?.getAttribute("d")).toContain(
      "L100.0,120.0",
    );
    pointer(svg, "pointercancel", 100, 120, 7);
    const ended = svg.querySelector("path")?.getAttribute("d");
    pointer(svg, "pointermove", 300, 320, 7);
    expect(svg.querySelector("path")?.getAttribute("d")).toBe(ended);
  });

  it("maps pen positions from a scaled, offset board into the same SVG coordinates", () => {
    click(button("Zoom in"));
    click(button("Pen"));
    const svg = prepareInk();
    const [, , width, height] = (svg.getAttribute("viewBox") || "")
      .split(" ")
      .map(Number);
    const left = 75;
    const top = 90;
    const screenWidth = width / 2;
    const screenHeight = height / 2;
    vi.mocked(svg.getBoundingClientRect).mockReturnValue({
      x: left,
      y: top,
      left,
      top,
      right: left + screenWidth,
      bottom: top + screenHeight,
      width: screenWidth,
      height: screenHeight,
      toJSON: () => ({}),
    });
    pointer(
      svg,
      "pointerdown",
      left + screenWidth / 4,
      top + screenHeight * 0.3,
    );
    pointer(
      svg,
      "pointermove",
      left + screenWidth * 0.75,
      top + screenHeight * 0.65,
    );
    pointer(svg, "pointerup", left + screenWidth, top + screenHeight);
    expect(svg.querySelector("path")?.getAttribute("d")).toBe(
      `M${(width / 4).toFixed(1)},${(height * 0.3).toFixed(1)} L${(width / 4).toFixed(1)},${(height * 0.3).toFixed(1)} L${(width * 0.75).toFixed(1)},${(height * 0.65).toFixed(1)}`,
    );
  });

  it("pauses playback for wheel, manual pan and keyboard scrolling without fighting navigation", () => {
    const viewport = host.querySelector<HTMLElement>('[aria-label^="Board."]');
    if (!viewport) throw new Error("Missing viewport");
    Object.defineProperty(viewport, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    for (const navigation of [
      "wheel",
      "drag",
      "ArrowUp",
      "ArrowDown",
      "ArrowLeft",
      "ArrowRight",
      "PageUp",
      "PageDown",
      "Home",
      "End",
    ]) {
      click(button("Restart lesson"));
      click(button("Play lesson"));
      tick();
      const revealed = actions();
      expect(vi.getTimerCount()).toBe(1);
      viewport.scrollTop = 3500;
      if (navigation === "drag") {
        pointer(viewport, "pointerdown", 200, 200);
        pointer(viewport, "pointermove", 180, 150);
        pointer(viewport, "pointerup", 180, 150);
      } else if (navigation === "wheel") {
        act(() =>
          viewport.dispatchEvent(
            new WheelEvent("wheel", { bubbles: true, deltaY: 100 }),
          ),
        );
      } else {
        act(() =>
          viewport.dispatchEvent(
            new KeyboardEvent("keydown", { bubbles: true, key: navigation }),
          ),
        );
      }
      expect(button("Play lesson").disabled).toBe(false);
      expect(vi.getTimerCount()).toBe(0);
      const chosenScroll = viewport.scrollTop;
      expect(chosenScroll).toBeGreaterThanOrEqual(3500);
      act(() => vi.advanceTimersByTime(60_000));
      expect(actions()).toEqual(revealed);
      expect(viewport.scrollTop).toBe(chosenScroll);
    }
  });

  it("starts a newly mounted narrow viewport at a readable zoom", () => {
    // This verifies the responsive default, not real-phone layout/performance.
    vi.stubGlobal("innerWidth", 390);
    act(() => {
      root.unmount();
      root = createRoot(host);
      root.render(createElement(StudentWhiteboard));
    });
    expect(zoom()).toBe("250%");
    expect(button("Zoom in").disabled).toBe(true);
    expect(button("Zoom out").disabled).toBe(false);
    click(button("Fit board"));
    expect(zoom()).toBe("100%");
  });

  it("follows a newly revealed right-hand action at enlarged zoom, then yields to manual navigation", () => {
    const viewport = host.querySelector<HTMLElement>('[aria-label^="Board."]');
    const paper = viewport?.firstElementChild;
    if (!viewport || !paper) throw new Error("Missing board viewport or paper");
    Object.defineProperties(viewport, {
      clientWidth: { configurable: true, value: 320 },
      clientHeight: { configurable: true, value: 300 },
      setPointerCapture: { configurable: true, value: vi.fn() },
    });
    Object.defineProperty(paper, "clientWidth", {
      configurable: true,
      value: 800,
    });
    for (let index = 0; index < 6; index += 1) click(button("Zoom in"));
    expect(zoom()).toBe("250%");
    const scene = layoutLesson(whiteboardLessons[0]);
    const input = scene.items.find((item) => item.action.id === "ece-input");
    if (!input) throw new Error("Missing right-hand ECE input action");
    const scale = 800 / scene.width;
    click(button("Play lesson"));
    tick();
    expect(actions()).toEqual(["ece-memory"]);
    const beforeInput = viewport.scrollLeft;
    expect((input.x + input.width) * scale).toBeGreaterThan(
      beforeInput + viewport.clientWidth,
    );
    tick();
    expect(actions()).toEqual(["ece-memory", "ece-input"]);
    expect(viewport.scrollLeft).toBeGreaterThan(0);
    expect(input.x * scale).toBeGreaterThanOrEqual(viewport.scrollLeft);
    expect((input.x + input.width) * scale).toBeLessThanOrEqual(
      viewport.scrollLeft + viewport.clientWidth,
    );
    viewport.scrollLeft = 0;
    act(() =>
      viewport.dispatchEvent(
        new WheelEvent("wheel", { bubbles: true, deltaX: -200 }),
      ),
    );
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(60_000));
    expect(actions()).toEqual(["ece-memory", "ece-input"]);
    expect(viewport.scrollLeft).toBe(0);
    click(button("Play lesson"));
    pointer(viewport, "pointerdown", 200, 200);
    pointer(viewport, "pointermove", 150, 200);
    pointer(viewport, "pointerup", 150, 200);
    expect(viewport.scrollLeft).toBe(50);
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(60_000));
    expect(actions()).toEqual(["ece-memory", "ece-input"]);
    expect(viewport.scrollLeft).toBe(50);
  });

  it("pans the scrollable board only for an active drag and leaves annotations untouched", () => {
    const viewport = host.querySelector<HTMLElement>('[aria-label^="Board."]');
    if (!viewport) throw new Error("Missing viewport");
    Object.defineProperty(viewport, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    viewport.scrollLeft = 80;
    viewport.scrollTop = 120;
    pointer(viewport, "pointerdown", 200, 200);
    pointer(viewport, "pointermove", 150, 180);
    expect(viewport.scrollLeft).toBe(130);
    expect(viewport.scrollTop).toBe(140);
    pointer(viewport, "pointerup", 150, 180);
    pointer(viewport, "pointermove", 100, 100);
    expect(viewport.scrollLeft).toBe(130);
    expect(viewport.scrollTop).toBe(140);
    expect(ink().querySelectorAll("path")).toHaveLength(0);
    click(button("Pen"));
    pointer(viewport, "pointerdown", 200, 200);
    pointer(viewport, "pointermove", 100, 100);
    expect(viewport.scrollLeft).toBe(130);
    expect(viewport.scrollTop).toBe(140);
  });

  it("stops pending playback when the document becomes hidden", () => {
    click(button("Play lesson"));
    tick();
    const revealed = actions();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(button("Play lesson").disabled).toBe(false);
    act(() => vi.advanceTimersByTime(60_000));
    expect(actions()).toEqual(revealed);
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(button("Play lesson").disabled).toBe(false);
    act(() => vi.advanceTimersByTime(60_000));
    expect(actions()).toEqual(revealed);
  });
});
