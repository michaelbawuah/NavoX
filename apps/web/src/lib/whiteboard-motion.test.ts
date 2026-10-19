import { describe, expect, it } from "vitest";
import { whiteboardLessons } from "./whiteboard-lessons";
import {
  whiteboardMotionSpeed,
  whiteboardRevealDuration,
} from "./whiteboard-motion";

describe("bounded Board presentation timing", () => {
  it("keeps every authored element finite without forcing long writing into a short wipe", () => {
    const actions = whiteboardLessons.flatMap((lesson) =>
      lesson.steps.flatMap((step) => step.actions),
    );
    for (const action of actions) {
      const duration = whiteboardRevealDuration(action);
      expect(Number.isFinite(duration)).toBe(true);
      expect(duration).toBeGreaterThanOrEqual(
        action.type === "write" ? 600 : 350,
      );
      expect(duration).toBeLessThanOrEqual(
        action.type === "write" ? 4800 : 1600,
      );
    }
    expect(
      whiteboardRevealDuration({
        id: "long-writing",
        type: "write",
        text: "W".repeat(240),
        place: "top-left",
      }),
    ).toBeGreaterThan(1600);
  });

  it.each([
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    0,
    -1,
  ])("an invalid pace %s cannot stall or skip the clock", (pace) => {
    expect(whiteboardMotionSpeed(pace)).toBe(1);
  });

  it("bounds extreme positive pace inputs and retains all offered paces", () => {
    expect(whiteboardMotionSpeed(Number.MIN_VALUE)).toBeGreaterThan(0);
    expect(whiteboardMotionSpeed(Number.MAX_VALUE)).toBeLessThanOrEqual(4);
    for (const pace of [0.65, 1, 1.5])
      expect(whiteboardMotionSpeed(pace)).toBe(pace);
  });
});
