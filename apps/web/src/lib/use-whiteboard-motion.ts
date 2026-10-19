"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  whiteboardMotionSpeed,
  whiteboardRevealDuration,
} from "./whiteboard-motion";
import {
  needsAnswer,
  type PlaybackState,
  playbackDelay,
} from "./whiteboard-playback";
import type { Lesson } from "./whiteboard-tutor";

export type WhiteboardMotionReveal = {
  actionId: string;
  progress: number;
};

type Motion = {
  lesson: Lesson;
  resetKey: string | number;
  stepIndex: number;
  revealed: number;
  actionId: string | null;
  phase: "waiting" | "drawing" | "done";
  wait: number;
  duration: number;
  elapsed: number;
  lastTime: number | null;
  speed: number;
};

type WhiteboardMotionOptions = {
  lesson: Lesson;
  playback: PlaybackState;
  speed: number;
  suspended: boolean;
  onTick: () => void;
  resetKey: string | number;
};

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", update);
      return () => query.removeEventListener("change", update);
    }
    query.addListener(update);
    return () => query.removeListener(update);
  }, []);
  return reduced;
}

function accrue(motion: Motion) {
  const now = performance.now();
  if (motion.lastTime !== null) {
    motion.elapsed += Math.max(0, now - motion.lastTime) * motion.speed;
  }
  motion.lastTime = now;
}

function currentReveal(motion: Motion): WhiteboardMotionReveal | null {
  return motion.phase === "drawing" && motion.actionId
    ? {
        actionId: motion.actionId,
        progress: Math.min(1, Math.max(0, motion.elapsed / motion.duration)),
      }
    : null;
}

/**
 * Only completed actions enter playback/saved progress. The active reveal is
 * disposable presentation state; its completion is the sole drawing tick.
 */
export function useWhiteboardMotion({
  lesson,
  playback,
  speed,
  suspended,
  onTick,
  resetKey,
}: WhiteboardMotionOptions): {
  reveal: WhiteboardMotionReveal | null;
  cancel: () => void;
} {
  const reducedMotion = useReducedMotion();
  const [reveal, setReveal] = useState<WhiteboardMotionReveal | null>(null);
  const motionRef = useRef<Motion | null>(null);
  const epochRef = useRef(0);
  const stopRef = useRef<(accountElapsed?: boolean) => void>(() => {});
  const tickRef = useRef(onTick);
  const waitingForAnswer = needsAnswer(lesson, playback);
  const { stepIndex, revealed, playing, complete } = playback;
  const wait = playbackDelay(lesson, playback);
  const permissionRef = useRef({
    lesson,
    resetKey,
    stepIndex,
    revealed,
    playing,
    complete,
    suspended,
    waitingForAnswer,
    speed: whiteboardMotionSpeed(speed),
    reducedMotion,
  });

  // Invalidate dequeued callbacks at unmount commit, before passive cleanup.
  useLayoutEffect(
    () => () => {
      epochRef.current += 1;
      permissionRef.current = {
        ...permissionRef.current,
        playing: false,
        suspended: true,
      };
      stopRef.current(false);
    },
    [],
  );

  // A committed pause/account mask must block queued work before passive cleanup.
  useLayoutEffect(() => {
    const nextSpeed = whiteboardMotionSpeed(speed);
    const changedPace =
      permissionRef.current.speed !== nextSpeed ||
      permissionRef.current.reducedMotion !== reducedMotion;
    permissionRef.current = {
      lesson,
      resetKey,
      stepIndex,
      revealed,
      playing,
      complete,
      suspended,
      waitingForAnswer,
      speed: nextSpeed,
      reducedMotion,
    };
    if (!playing || complete || suspended || waitingForAnswer || changedPace) {
      stopRef.current(document.visibilityState === "visible");
    }
  }, [
    lesson,
    resetKey,
    stepIndex,
    revealed,
    playing,
    complete,
    suspended,
    waitingForAnswer,
    speed,
    reducedMotion,
  ]);

  useLayoutEffect(() => {
    tickRef.current = onTick;
  }, [onTick]);

  const cancel = useCallback(() => {
    epochRef.current += 1;
    stopRef.current();
    motionRef.current = null;
    setReveal(null);
  }, []);

  useEffect(() => {
    let motion = motionRef.current;
    if (
      !motion ||
      motion.lesson !== lesson ||
      !Object.is(motion.resetKey, resetKey) ||
      motion.stepIndex !== stepIndex ||
      motion.revealed !== revealed
    ) {
      const action = lesson.steps[stepIndex].actions[revealed];
      motion = {
        lesson,
        resetKey,
        stepIndex,
        revealed,
        actionId: action?.id ?? null,
        phase: "waiting",
        wait,
        duration: action ? whiteboardRevealDuration(action) : 0,
        elapsed: 0,
        lastTime: null,
        speed: whiteboardMotionSpeed(speed),
      };
      motionRef.current = motion;
    }
    const current = motion;
    const epoch = ++epochRef.current;
    let scheduled: { kind: "timeout" | "frame"; id: number } | null = null;

    const stop = (accountElapsed = true) => {
      if (scheduled?.kind === "timeout") window.clearTimeout(scheduled.id);
      if (scheduled?.kind === "frame")
        window.cancelAnimationFrame(scheduled.id);
      scheduled = null;
      if (current.lastTime !== null) {
        if (accountElapsed) accrue(current);
        current.lastTime = null;
      }
      if (epochRef.current === epoch) epochRef.current += 1;
    };
    stopRef.current = stop;
    setReveal(currentReveal(current));

    const valid = () => {
      if (epochRef.current !== epoch || motionRef.current !== current) {
        return false;
      }
      const permission = permissionRef.current;
      if (
        permission.lesson !== current.lesson ||
        !Object.is(permission.resetKey, current.resetKey) ||
        permission.stepIndex !== current.stepIndex ||
        permission.revealed !== current.revealed ||
        !permission.playing ||
        permission.complete ||
        permission.suspended ||
        permission.waitingForAnswer ||
        permission.speed !== current.speed ||
        permission.reducedMotion !== reducedMotion ||
        document.visibilityState !== "visible"
      ) {
        // If a hide event was missed/delayed, its elapsed interval is unknown.
        stop(false);
        return false;
      }
      return true;
    };

    const visibilityChanged = () => {
      if (
        epochRef.current !== epoch ||
        motionRef.current !== current ||
        document.visibilityState === "visible"
      ) {
        return;
      }
      stop();
      setReveal(currentReveal(current));
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    const cleanup = () => {
      document.removeEventListener("visibilitychange", visibilityChanged);
      stop(document.visibilityState === "visible");
    };

    if (
      !playing ||
      complete ||
      suspended ||
      waitingForAnswer ||
      current.phase === "done" ||
      document.visibilityState !== "visible"
    ) {
      return cleanup;
    }

    current.speed = whiteboardMotionSpeed(speed);
    current.lastTime = performance.now();

    const finish = () => {
      if (!valid() || current.phase === "done") return;
      current.phase = "done";
      current.lastTime = null;
      setReveal(
        current.actionId ? { actionId: current.actionId, progress: 1 } : null,
      );
      tickRef.current();
    };

    const frame = () => {
      if (!valid()) return;
      scheduled = null;
      accrue(current);
      if (current.elapsed >= current.duration) {
        finish();
        return;
      }
      setReveal(currentReveal(current));
      scheduled = { kind: "frame", id: window.requestAnimationFrame(frame) };
    };

    const draw = () => {
      if (!valid()) return;
      scheduled = null;
      current.phase = "drawing";
      current.elapsed = 0;
      current.lastTime = performance.now();
      if (!current.actionId || reducedMotion) {
        finish();
        return;
      }
      setReveal(currentReveal(current));
      scheduled = { kind: "frame", id: window.requestAnimationFrame(frame) };
    };

    if (current.phase === "waiting") {
      scheduled = {
        kind: "timeout",
        id: window.setTimeout(
          draw,
          Math.max(0, current.wait - current.elapsed) / current.speed,
        ),
      };
    } else if (reducedMotion || current.elapsed >= current.duration) {
      finish();
    } else {
      scheduled = { kind: "frame", id: window.requestAnimationFrame(frame) };
    }
    return cleanup;
  }, [
    lesson,
    stepIndex,
    revealed,
    playing,
    complete,
    wait,
    speed,
    suspended,
    resetKey,
    reducedMotion,
    waitingForAnswer,
  ]);

  return { reveal, cancel };
}
