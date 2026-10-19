// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SavedBoard, SavedBoardsApi } from "../lib/saved-boards";
import { StudentWhiteboard } from "./student-whiteboard";

const accountKey =
  "11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222";
const boardId = "33333333-3333-4333-8333-333333333333";
let host: HTMLDivElement;
let root: Root;
let now: number;
let nextFrame: number;
let frames: Map<number, FrameRequestCallback>;
let fetchCalls: ReturnType<typeof vi.fn>;

function button(name: string) {
  const found = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
    (item) =>
      (item.getAttribute("aria-label") || item.textContent?.trim()) === name,
  );
  if (!found) throw new Error(`Missing button: ${name}`);
  return found;
}

function click(name: string) {
  act(() => button(name).click());
}

function advance(milliseconds: number) {
  act(() => {
    now += milliseconds;
    vi.advanceTimersByTime(milliseconds);
    const pending = [...frames.values()];
    frames.clear();
    for (const frame of pending) frame(now);
  });
}

function action(id: string) {
  return host.querySelector<SVGGElement>(`g[data-action-id="${id}"]`);
}

function progress(id: string) {
  const value = action(id)?.getAttribute("data-reveal-progress");
  if (value === null || value === undefined)
    throw new Error(`Missing active reveal: ${id}`);
  return Number(value);
}

function timeline(index: number) {
  const item = host.querySelectorAll<HTMLButtonElement>(
    'nav[aria-label="Lesson timeline"] button',
  )[index];
  if (!item) throw new Error(`Missing timeline step ${index}`);
  act(() => item.click());
}

function chooseCalculus() {
  const picker = host.querySelector<HTMLSelectElement>("#whiteboard-lesson");
  if (!picker) throw new Error("Missing lesson picker");
  act(() => {
    picker.value = "calculus-parts";
    picker.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function beginPartial(id = "ece-memory") {
  click("Play lesson");
  advance(500);
  expect(action(id)).not.toBeNull();
  advance(100);
  expect(progress(id)).toBeGreaterThan(0);
  expect(progress(id)).toBeLessThan(1);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  now = 0;
  nextFrame = 1;
  frames = new Map();
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    const id = nextFrame++;
    frames.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  fetchCalls = vi.fn(() => {
    throw new Error("Board motion must not request a network resource");
  });
  vi.stubGlobal("fetch", fetchCalls);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root.render(createElement(StudentWhiteboard)));
});

afterEach(() => {
  act(() => root.unmount());
  expect(frames.size).toBe(0);
  expect(vi.getTimerCount()).toBe(0);
  expect(fetchCalls).not.toHaveBeenCalled();
  host.remove();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Board motion through the real lesson controls", () => {
  it("freezes a partial stroke while paused and resumes that same action", () => {
    beginPartial();
    const partial = progress("ece-memory");
    click("Pause lesson");
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(60_000);
    expect(progress("ece-memory")).toBe(partial);
    expect(action("ece-input")).toBeNull();
    click("Play lesson");
    advance(50);
    expect(progress("ece-memory")).toBeGreaterThan(partial);
    advance(2_000);
    expect(action("ece-memory")?.hasAttribute("data-reveal-progress")).toBe(
      false,
    );
    expect(action("ece-input")).toBeNull();
  });

  it("keeps existing student ink stable without reformatting its coordinates on drawing frames", () => {
    click("Pen");
    const ink = host.querySelector<SVGSVGElement>(
      'svg[aria-label="Your pen annotations"]',
    );
    if (!ink) throw new Error("Missing ink layer");
    vi.spyOn(ink, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1120,
      bottom: 592,
      width: 1120,
      height: 592,
      toJSON: () => ({}),
    });
    Object.defineProperty(ink, "setPointerCapture", { value: vi.fn() });
    for (const type of ["pointerdown", "pointerup"]) {
      const event = new MouseEvent(type, {
        bubbles: true,
        button: 0,
        clientX: 50,
        clientY: 50,
      });
      Object.defineProperty(event, "pointerId", { value: 7 });
      act(() => ink.dispatchEvent(event));
    }
    const path = ink.querySelector("path");
    expect(path).not.toBeNull();
    const drawing = path?.getAttribute("d");
    click("Pan");
    beginPartial();
    const formatting = vi.spyOn(Number.prototype, "toFixed");
    advance(75);
    advance(75);
    advance(75);
    expect(formatting).not.toHaveBeenCalled();
    expect(ink.querySelector("path")).toBe(path);
    expect(path?.getAttribute("d")).toBe(drawing);
  });

  it("withholds a quick check until its final action finishes drawing", () => {
    chooseCalculus();
    beginPartial("parts-problem");
    expect(host.querySelector('[aria-label="Quick check"]')).toBeNull();
    click("Pause lesson");
    advance(60_000);
    expect(host.querySelector('[aria-label="Quick check"]')).toBeNull();
    click("Play lesson");
    advance(2_000);
    expect(host.querySelector('[aria-label="Quick check"]')).not.toBeNull();
    expect(button("Play lesson").disabled).toBe(true);
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("applies the selected pace to remaining drawing and freezes for manual scrolling", () => {
    beginPartial();
    const first = progress("ece-memory");
    advance(100);
    const normal = progress("ece-memory");
    const pace = [...host.querySelectorAll("label")]
      .find((label) => label.textContent?.startsWith("Pace"))
      ?.querySelector("select");
    if (!pace) throw new Error("Missing pace control");
    act(() => {
      pace.value = "1.5";
      pace.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(progress("ece-memory")).toBe(normal);
    advance(100);
    const faster = progress("ece-memory");
    expect((faster - normal) / (normal - first)).toBeCloseTo(1.5);
    const viewport = host.querySelector<HTMLElement>('[aria-label^="Board."]');
    if (!viewport) throw new Error("Missing board viewport");
    act(() =>
      viewport.dispatchEvent(
        new WheelEvent("wheel", { bubbles: true, deltaY: 100 }),
      ),
    );
    expect(button("Play lesson").disabled).toBe(false);
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(60_000);
    expect(progress("ece-memory")).toBe(faster);
  });

  it("discards stale frames on timeline jumps, replay, restart and lesson changes", () => {
    beginPartial();
    const stale = [...frames.values()];
    timeline(1);
    expect(action("ece-memory")?.hasAttribute("data-reveal-progress")).toBe(
      false,
    );
    expect(action("ece-rule")?.hasAttribute("data-reveal-progress")).toBe(
      false,
    );
    act(() => {
      for (const frame of stale) frame(now + 10_000);
    });
    expect(action("ece-example-line")).toBeNull();
    click("Replay step");
    expect(action("ece-rule")).toBeNull();
    advance(500);
    advance(100);
    expect(progress("ece-rule")).toBeGreaterThan(0);
    click("Restart lesson");
    expect(host.querySelectorAll("g[data-action-id]")).toHaveLength(0);
    advance(60_000);
    expect(host.querySelectorAll("g[data-action-id]")).toHaveLength(0);
    beginPartial();
    chooseCalculus();
    advance(60_000);
    expect(host.querySelectorAll("g[data-action-id]")).toHaveLength(0);
    expect(button("Play lesson").disabled).toBe(false);
  });

  it("pauses a partial action when hidden and requires an explicit resume", () => {
    beginPartial();
    const partial = progress("ece-memory");
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    advance(60_000);
    expect(progress("ece-memory")).toBe(partial);
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    advance(60_000);
    expect(progress("ece-memory")).toBe(partial);
    click("Play lesson");
    advance(2_000);
    expect(action("ece-memory")?.hasAttribute("data-reveal-progress")).toBe(
      false,
    );
  });

  it("saves only completed actions and restores that prefix instantly while paused", async () => {
    let saved: SavedBoard | undefined;
    const api: SavedBoardsApi = {
      account: vi.fn(async () => accountKey),
      list: vi.fn(async () => ({
        generation: 0,
        revision: saved ? 1 : 0,
        boards: saved ? [saved] : [],
      })),
      open: vi.fn(async () => {
        if (!saved) throw new Error("Missing saved Board");
        return saved;
      }),
      create: vi.fn(async (input) => {
        saved = {
          id: boardId,
          revision: 1,
          course_id: null,
          created_at: "2026-10-19T12:00:00Z",
          updated_at: "2026-10-19T12:00:00Z",
          title: input.snapshot.title,
          lesson_id: input.snapshot.lesson_id,
          lesson_version: input.snapshot.lesson_version,
          step_index: input.snapshot.step_index,
          complete: input.snapshot.complete,
          snapshot: structuredClone(input.snapshot),
        };
        return saved;
      }),
      update: vi.fn(async () => {
        throw new Error("Unexpected update");
      }),
      erase: vi.fn(async () => {}),
    };
    await act(async () =>
      root.render(
        createElement(StudentWhiteboard, {
          storageEnabled: true,
          accountKey,
          api,
        }),
      ),
    );
    beginPartial();
    advance(2_000);
    // The completed first action is followed by the next authored reading cue.
    advance(20_000);
    advance(100);
    expect(progress("ece-input")).toBeGreaterThan(0);
    await act(async () => button("Save this Board").click());
    expect(vi.mocked(api.create).mock.calls[0][0].snapshot.revealed).toBe(1);
    expect(saved?.snapshot.revealed).toBe(1);
    await act(async () =>
      button(`Reopen ${saved?.title ?? "missing"}`).click(),
    );
    expect(action("ece-memory")?.hasAttribute("data-reveal-progress")).toBe(
      false,
    );
    expect(action("ece-input")).toBeNull();
    expect(button("Play lesson").disabled).toBe(false);
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(60_000);
    expect(action("ece-input")).toBeNull();
  });
});
