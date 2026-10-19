import type { BoardAction } from "./whiteboard-tutor";

/** Bounded visual reveal time, separate from authored reading/voice timing. */
export function whiteboardRevealDuration(action: BoardAction): number {
  let duration: number;
  switch (action.type) {
    case "write":
      // Letter contours need more reading room than a whole-line wipe. Pace
      // still scales the remainder, and even the longest writing stays bounded.
      return Math.min(4800, Math.max(600, 300 + [...action.text].length * 32));
    case "math":
      duration = 500 + action.latex.length * 8;
      break;
    case "shape":
      duration = 600 + action.label.length * 8;
      break;
    case "arrow":
      duration = 450 + (action.label?.length ?? 0) * 8;
      break;
    case "highlight":
      duration = 450;
      break;
    case "plot":
      duration = 1100 + action.label.length * 3;
      break;
  }
  return Math.min(1600, Math.max(350, duration));
}

/** Invalid pace input must never create an immediate or indefinitely delayed clock. */
export function whiteboardMotionSpeed(speed: number): number {
  return Number.isFinite(speed) && speed > 0
    ? Math.min(4, Math.max(0.25, speed))
    : 1;
}
