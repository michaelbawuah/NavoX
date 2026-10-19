// @vitest-environment jsdom

import { act, createElement, useLayoutEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { restoredBoardPlayback, snapshotForLesson } from "./saved-boards";
import { useWhiteboardMotion } from "./use-whiteboard-motion";
import { whiteboardLessons } from "./whiteboard-lessons";
import {
  initialPlayback,
  type PlaybackState,
  playbackDelay,
} from "./whiteboard-playback";

type Input = Parameters<typeof useWhiteboardMotion>[0];
type Motion = ReturnType<typeof useWhiteboardMotion>;

let host: HTMLDivElement;
let root: Root;
let now: number;
let nextFrame: number;
let frames: Map<number, FrameRequestCallback>;
let mediaMatches: boolean;
let visibility: DocumentVisibilityState;
let mediaListeners: Set<(event: MediaQueryListEvent) => void>;
let motion: Motion;
let input: Input;
let onTick: ReturnType<typeof vi.fn<() => void>>;
let commitProbe: (() => void) | null;
let unmountProbe: (() => void) | null;

function Harness(props: Input) {
  motion = useWhiteboardMotion(props);
  // Deliver a callback after the hook's layout effects but before its passive
  // effects. A fully flushed act() alone cannot expose this commit boundary.
  useLayoutEffect(() => {
    const probe = commitProbe;
    commitProbe = null;
    probe?.();
  });
  useLayoutEffect(
    () => () => {
      const probe = unmountProbe;
      unmountProbe = null;
      probe?.();
    },
    [],
  );
  return null;
}

function render(changes: Partial<Input> = {}) {
  input = { ...input, ...changes };
  act(() => root.render(createElement(Harness, input)));
}

function playing(changes: Partial<PlaybackState> = {}) {
  return { ...initialPlayback(), playing: true, ...changes };
}

/** Drive the same monotonic frame time as the hook, without real timers or layout. */
function advance(milliseconds: number) {
  const end = now + milliseconds;
  while (now < end) {
    const elapsed = Math.min(end - now, 25);
    now += elapsed;
    act(() => {
      vi.advanceTimersByTime(elapsed);
      const pending = [...frames.values()];
      frames.clear();
      for (const callback of pending) callback(now);
    });
  }
}

function reducedMotion(matches: boolean) {
  mediaMatches = matches;
  act(() => {
    for (const listener of mediaListeners)
      listener({ matches } as MediaQueryListEvent);
  });
}

function pendingFrame() {
  const callback = [...frames.values()][0];
  if (!callback) throw new Error("Expected an active animation frame");
  return callback;
}

function hideDocument() {
  visibility = "hidden";
  act(() => document.dispatchEvent(new Event("visibilitychange")));
}

function resumeAfterHide() {
  // Match the host's explicit pause and later play; returning to the tab alone
  // must never turn an old queued callback into permission to continue.
  render({ playback: { ...input.playback, playing: false } });
  visibility = "visible";
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  render({ playback: { ...input.playback, playing: true } });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  now = 0;
  nextFrame = 0;
  frames = new Map();
  mediaMatches = false;
  visibility = "visible";
  mediaListeners = new Set();
  commitProbe = null;
  unmountProbe = null;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.spyOn(document, "visibilityState", "get").mockImplementation(
    () => visibility,
  );
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((callback: FrameRequestCallback) => {
      const id = ++nextFrame;
      frames.set(id, callback);
      return id;
    }),
  );
  vi.stubGlobal(
    "cancelAnimationFrame",
    vi.fn((id: number) => frames.delete(id)),
  );
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      get matches() {
        return mediaMatches;
      },
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: (
        _event: string,
        listener: (event: MediaQueryListEvent) => void,
      ) => mediaListeners.add(listener),
      removeEventListener: (
        _event: string,
        listener: (event: MediaQueryListEvent) => void,
      ) => mediaListeners.delete(listener),
    })),
  );
  onTick = vi.fn<() => void>();
  input = {
    lesson: whiteboardLessons[0],
    playback: initialPlayback(),
    speed: 1,
    suspended: false,
    onTick,
    resetKey: 0,
  };
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  render();
});

afterEach(() => {
  act(() => root.unmount());
  expect(frames.size).toBe(0);
  expect(vi.getTimerCount()).toBe(0);
  expect(mediaListeners.size).toBe(0);
  host.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("authored Board motion clock", () => {
  it("starts paused and finishes one action only after its cue wait and visual reveal", () => {
    advance(60_000);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);

    render({ playback: playing() });
    advance(475);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
    advance(125);
    expect(motion.reveal?.actionId).toBe("ece-memory");
    expect(motion.reveal?.progress).toBeGreaterThan(0);
    expect(motion.reveal?.progress).toBeLessThan(1);
    expect(onTick).not.toHaveBeenCalled();
    advance(2_000);
    expect(onTick).toHaveBeenCalledTimes(1);
    advance(60_000);
    expect(onTick).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
  });

  it("resumes the remaining cue wait rather than starting over or counting paused time", () => {
    render({ playback: playing() });
    advance(200);
    render({ playback: { ...input.playback, playing: false } });
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(60_000);
    render({ playback: { ...input.playback, playing: true } });
    advance(250);
    expect(motion.reveal).toBeNull();
    advance(100);
    expect(motion.reveal?.actionId).toBe("ece-memory");
    expect(onTick).not.toHaveBeenCalled();
  });

  it("freezes a partial action on pause and continues its remaining strokes on resume", () => {
    render({ playback: playing() });
    advance(650);
    const partial = motion.reveal;
    expect(partial?.progress).toBeGreaterThan(0);
    expect(partial?.progress).toBeLessThan(1);
    render({ playback: { ...input.playback, playing: false } });
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(60_000);
    expect(motion.reveal).toEqual(partial);
    expect(onTick).not.toHaveBeenCalled();
    render({ playback: { ...input.playback, playing: true } });
    advance(50);
    expect(motion.reveal?.progress).toBeGreaterThan(partial?.progress ?? 0);
    advance(2_000);
    expect(onTick).toHaveBeenCalledTimes(1);
  });

  it("changes the pace of remaining work without restarting the current wait", () => {
    render({ playback: playing() });
    advance(200);
    render({ speed: 1.5 });
    advance(175);
    expect(motion.reveal).toBeNull();
    advance(50);
    expect(motion.reveal?.actionId).toBe("ece-memory");
    expect(onTick).not.toHaveBeenCalled();
  });

  it("retains already drawn progress when changing pace mid-action", () => {
    render({ playback: playing() });
    advance(600);
    const before = motion.reveal?.progress ?? 0;
    render({ speed: 0.65 });
    expect(motion.reveal?.progress).toBe(before);
    advance(100);
    const slower = motion.reveal?.progress ?? 0;
    expect(slower).toBeGreaterThan(before);
    render({ speed: 1.5 });
    expect(motion.reveal?.progress).toBe(slower);
    advance(100);
    const faster = motion.reveal?.progress ?? 0;
    expect(faster - slower).toBeCloseTo(((slower - before) * 1.5) / 0.65, 5);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("ignores a cancelled old frame after replay resets the same lesson position", () => {
    render({ playback: playing() });
    advance(650);
    const stale = pendingFrame();
    act(() => motion.cancel());
    render({ resetKey: 1 });
    expect(motion.reveal).toBeNull();
    act(() => stale(now + 60_000));
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
    advance(475);
    expect(motion.reveal).toBeNull();
    advance(125);
    expect(motion.reveal?.actionId).toBe("ece-memory");
    expect(motion.reveal?.progress).toBeLessThan(1);
  });

  it("invalidates old frames on a timeline jump and leaves reopened work fully drawn and paused", () => {
    render({ playback: playing() });
    advance(650);
    const stale = pendingFrame();
    const lastStep = input.lesson.steps.at(-1);
    if (!lastStep) throw new Error("Missing last lesson step");
    render({
      playback: {
        ...initialPlayback(),
        stepIndex: input.lesson.steps.length - 1,
        revealed: lastStep.actions.length,
      },
    });
    expect(motion.reveal).toBeNull();
    expect(frames.size).toBe(0);
    act(() => stale(now + 60_000));
    advance(60_000);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
  });

  it("stops immediately for private-content suspension and resumes only unspent work", () => {
    render({ playback: playing() });
    advance(650);
    const partial = motion.reveal;
    const stale = pendingFrame();
    render({ suspended: true });
    expect(frames.size).toBe(0);
    act(() => stale(now + 60_000));
    advance(60_000);
    expect(motion.reveal).toEqual(partial);
    expect(onTick).not.toHaveBeenCalled();
    render({ suspended: false });
    advance(50);
    expect(motion.reveal?.progress).toBeGreaterThan(partial?.progress ?? 0);
  });

  it("rejects an already queued drawing frame when the document hides before host pause commits", () => {
    render({ playback: playing() });
    advance(650);
    const partial = motion.reveal;
    const stale = pendingFrame();
    hideDocument();
    // A frame may already be dequeued when cancellation happens. Deliver it
    // with the real clock now far ahead, before the host changes playback.
    frames.clear();
    now += 60_000;
    act(() => stale(now));
    expect(motion.reveal).toEqual(partial);
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    resumeAfterHide();
    advance(25);
    expect(onTick).not.toHaveBeenCalled();
    expect(motion.reveal?.progress).toBeGreaterThan(partial?.progress ?? 0);
    expect(motion.reveal?.progress).toBeLessThan(1);
  });

  it("rejects a cue timeout firing while hidden before host pause commits", () => {
    render({ playback: playing() });
    advance(200);
    hideDocument();
    advance(60_000);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    resumeAfterHide();
    advance(25);
    expect(onTick).not.toHaveBeenCalled();
    expect(motion.reveal?.progress ?? 0).toBeLessThan(1);
  });

  it("checks actual document visibility even when its hide event was missed", () => {
    render({ playback: playing() });
    advance(650);
    const partial = motion.reveal;
    const stale = pendingFrame();
    visibility = "hidden";
    frames.clear();
    now += 60_000;
    act(() => stale(now));
    expect(motion.reveal).toEqual(partial);
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    resumeAfterHide();
    advance(25);
    expect(motion.reveal?.progress).toBeGreaterThan(partial?.progress ?? 0);
    expect(motion.reveal?.progress).toBeLessThan(1);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("rejects a cue timeout if the hide event was missed and discards its unknown interval", () => {
    render({ playback: playing() });
    advance(200);
    visibility = "hidden";
    advance(60_000);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    resumeAfterHide();
    advance(475);
    expect(motion.reveal).toBeNull();
    advance(125);
    expect(motion.reveal?.actionId).toBe("ece-memory");
    expect(motion.reveal?.progress).toBeLessThan(1);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("keeps reading waits for reduced motion while skipping the visual drawing phase", () => {
    reducedMotion(true);
    render({ playback: playing() });
    advance(475);
    expect(onTick).not.toHaveBeenCalled();
    expect(motion.reveal).toBeNull();
    advance(50);
    expect(onTick).toHaveBeenCalledTimes(1);
    expect(motion.reveal).toEqual({ actionId: "ece-memory", progress: 1 });
    expect(frames.size).toBe(0);
  });

  it("applies a live reduced-motion change to the active drawing without an extra tick", () => {
    render({ playback: playing() });
    advance(650);
    expect(motion.reveal?.progress).toBeGreaterThan(0);
    reducedMotion(true);
    advance(25);
    expect(onTick).toHaveBeenCalledTimes(1);
    advance(60_000);
    expect(onTick).toHaveBeenCalledTimes(1);
  });

  it("keeps a paused drawing still when reduced motion changes and completes only after play", () => {
    render({ playback: playing() });
    advance(650);
    const partial = motion.reveal;
    render({ playback: { ...input.playback, playing: false } });
    reducedMotion(true);
    advance(60_000);
    expect(motion.reveal).toEqual(partial);
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    render({ playback: { ...input.playback, playing: true } });
    expect(onTick).toHaveBeenCalledTimes(1);
    advance(60_000);
    expect(onTick).toHaveBeenCalledTimes(1);
  });

  it("waits for the completed step's narration before advancing and draws no extra action", () => {
    const playback = playing({
      revealed: input.lesson.steps[0].actions.length,
    });
    render({ playback });
    const wait = playbackDelay(input.lesson, playback);
    advance(wait - 25);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
    advance(50);
    expect(onTick).toHaveBeenCalledTimes(1);
    expect(motion.reveal).toBeNull();
    advance(60_000);
    expect(onTick).toHaveBeenCalledTimes(1);
  });

  it("does not persist a partly drawn action or resume its animation when reopened", () => {
    render({ playback: playing() });
    advance(650);
    expect(motion.reveal?.progress).toBeGreaterThan(0);
    expect(onTick).not.toHaveBeenCalled();
    const saved = snapshotForLesson(input.lesson, input.playback, [], []);
    expect(saved.revealed).toBe(0);
    expect(saved).not.toHaveProperty("reveal");
    expect(saved).not.toHaveProperty("progress");
    act(() => motion.cancel());
    render({ playback: restoredBoardPlayback(saved), resetKey: 1 });
    advance(60_000);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("refuses to run past an unanswered check even if a caller requests playback", () => {
    const index = input.lesson.steps.findIndex((step) => step.check);
    render({
      playback: playing({
        stepIndex: index,
        revealed: input.lesson.steps[index].actions.length,
      }),
    });
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(60_000);
    expect(motion.reveal).toBeNull();
    expect(onTick).not.toHaveBeenCalled();
  });

  it("cannot complete an action from a stale frame after unmount", () => {
    render({ playback: playing() });
    advance(650);
    const stale = pendingFrame();
    act(() => root.unmount());
    expect(frames.size).toBe(0);
    expect(mediaListeners.size).toBe(0);
    act(() => stale(now + 60_000));
    expect(onTick).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
    root = createRoot(host);
    render();
    advance(475);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("blocks a dequeued frame during layout unmount before passive cleanup runs", () => {
    render({ playback: playing() });
    advance(650);
    const stale = pendingFrame();
    let calledDuringLayout = false;
    unmountProbe = () => {
      calledDuringLayout = true;
      now += 60_000;
      stale(now);
      expect(onTick).not.toHaveBeenCalled();
      expect(frames.size).toBe(0);
      expect(vi.getTimerCount()).toBe(0);
    };
    act(() => root.unmount());
    expect(calledDuringLayout).toBe(true);
    expect(onTick).not.toHaveBeenCalled();
    root = createRoot(host);
    render({ playback: initialPlayback() });
  });

  it("invalidates the old pace's queued frame at layout commit before passive reconfiguration", () => {
    render({ playback: playing() });
    advance(650);
    const partial = motion.reveal;
    const stale = pendingFrame();
    let ticksDuringLayout = -1;
    commitProbe = () => {
      now += 1_000;
      stale(now);
      ticksDuringLayout = onTick.mock.calls.length;
    };
    render({ speed: 0.65 });
    expect(ticksDuringLayout).toBe(0);
    expect(onTick).not.toHaveBeenCalled();
    expect(motion.reveal).toEqual(partial);
    advance(100);
    expect(motion.reveal?.progress).toBeGreaterThan(partial?.progress ?? 0);
    expect(motion.reveal?.progress).toBeLessThan(1);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("invalidates a queued animation during the reduced-motion layout commit", () => {
    render({ playback: playing() });
    advance(650);
    const stale = pendingFrame();
    let ticksDuringLayout = -1;
    commitProbe = () => {
      now += 1_000;
      stale(now);
      ticksDuringLayout = onTick.mock.calls.length;
    };
    reducedMotion(true);
    expect(ticksDuringLayout).toBe(0);
    // The new reduced-motion policy completes in its own effect, once.
    expect(onTick).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(60_000);
    expect(onTick).toHaveBeenCalledTimes(1);
  });

  it("uses the latest completion handler when an allowed frame runs during layout commit", () => {
    render({ playback: playing() });
    advance(650);
    const stale = pendingFrame();
    const latestTick = vi.fn<() => void>();
    commitProbe = () => {
      now += 1_000;
      frames.clear();
      stale(now);
    };
    render({ onTick: latestTick });
    expect(onTick).not.toHaveBeenCalled();
    expect(latestTick).toHaveBeenCalledTimes(1);
    expect(motion.reveal).toEqual({ actionId: "ece-memory", progress: 1 });
  });
});
