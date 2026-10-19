import { describe, expect, it } from "vitest";
import { whiteboardLessons } from "./whiteboard-lessons";
import {
  advancePlayback,
  initialPlayback,
  needsAnswer,
  playbackDelay,
  visibleBoardActions,
} from "./whiteboard-playback";

const lesson = whiteboardLessons[0];

describe("whiteboard playback boundaries", () => {
  it("does not advance before play or after pause, including a queued stale tick", () => {
    const initial = initialPlayback();
    expect(advancePlayback(lesson, initial, { type: "tick" })).toBe(initial);
    const playing = advancePlayback(lesson, initial, { type: "play" });
    const first = advancePlayback(lesson, playing, { type: "tick" });
    expect([...visibleBoardActions(lesson, first)]).toEqual(["ece-memory"]);
    const paused = advancePlayback(lesson, first, { type: "pause" });
    expect(advancePlayback(lesson, paused, { type: "tick" })).toBe(paused);
  });

  it("pauses exactly at an unanswered check and never invents an answer", () => {
    let state = advancePlayback(lesson, initialPlayback(), { type: "play" });
    for (let i = 0; i < 30; i++)
      state = advancePlayback(lesson, state, { type: "tick" });
    expect(state.stepIndex).toBe(1);
    expect(state.revealed).toBe(lesson.steps[1].actions.length);
    expect(state.playing).toBe(false);
    expect(needsAnswer(lesson, state)).toBe(true);
    expect(state.answers).toEqual({});
    expect(advancePlayback(lesson, state, { type: "play" })).toBe(state);
    for (const choice of [-1, 0.5, 3, Number.NaN]) {
      expect(advancePlayback(lesson, state, { type: "answer", choice })).toBe(
        state,
      );
    }
    const answered = advancePlayback(lesson, state, {
      type: "answer",
      choice: 0,
    });
    expect(answered.answers).toEqual({ "ece-edge": 0 });
    expect(answered.playing).toBe(false);
    expect(needsAnswer(lesson, answered)).toBe(false);
    expect(advancePlayback(lesson, answered, { type: "tick" })).toBe(answered);
    const resumed = advancePlayback(lesson, answered, { type: "play" });
    expect(advancePlayback(lesson, resumed, { type: "tick" }).stepIndex).toBe(
      2,
    );
  });

  it("rejects early answers, supports explicit timeline skips and hides later content on rewind", () => {
    const initial = initialPlayback();
    expect(
      advancePlayback(lesson, initial, { type: "answer", choice: 1 }),
    ).toBe(initial);
    for (const index of [-1, 0.5, 50, Number.NaN]) {
      expect(advancePlayback(lesson, initial, { type: "jump", index })).toBe(
        initial,
      );
    }
    const last = advancePlayback(lesson, initial, { type: "jump", index: 2 });
    expect(visibleBoardActions(lesson, last).size).toBe(7);
    expect(needsAnswer(lesson, last)).toBe(true);
    const first = advancePlayback(lesson, last, { type: "jump", index: 0 });
    expect(visibleBoardActions(lesson, first)).toEqual(
      new Set(lesson.steps[0].actions.map((item) => item.id)),
    );
    const replay = advancePlayback(lesson, first, { type: "replay" });
    expect(visibleBoardActions(lesson, replay).size).toBe(0);
    expect(replay.playing).toBe(true);
  });

  it.each(whiteboardLessons.map((item) => [item.id, item] as const))(
    "can finish %s through explicit checks, replay and restart without losing prior board steps",
    (_id, current) => {
      let state = advancePlayback(current, initialPlayback(), { type: "play" });
      let answered = 0;
      for (let i = 0; i < 200 && !state.complete; i++) {
        if (needsAnswer(current, state)) {
          state = advancePlayback(current, state, {
            type: "answer",
            choice: current.steps[state.stepIndex].check?.answer ?? 0,
          });
          answered++;
          state = advancePlayback(current, state, { type: "play" });
        }
        state = advancePlayback(current, state, { type: "tick" });
      }
      expect(state.complete).toBe(true);
      expect(state.playing).toBe(false);
      expect(answered).toBe(2);
      expect(visibleBoardActions(current, state).size).toBe(
        current.steps.flatMap((step) => step.actions).length,
      );
      expect(advancePlayback(current, state, { type: "play" })).toBe(state);
      const replay = advancePlayback(current, state, { type: "replay" });
      expect(replay.complete).toBe(false);
      expect(replay.answers).toEqual(state.answers);
      expect(advancePlayback(current, replay, { type: "restart" })).toEqual(
        initialPlayback(),
      );
    },
  );

  it("uses authored narration length for readable cue pacing and honors slower/faster controls", () => {
    const initial = initialPlayback();
    expect(playbackDelay(lesson, initial)).toBe(500);
    const first = { ...initial, revealed: 1 };
    expect(playbackDelay(lesson, first)).toBeGreaterThan(4000);
    expect(playbackDelay(lesson, first, 0.65)).toBeGreaterThan(
      playbackDelay(lesson, first),
    );
    expect(playbackDelay(lesson, first, 1.5)).toBeLessThan(
      playbackDelay(lesson, first),
    );
    const step = { ...first, stepIndex: 1, revealed: 3 };
    expect(playbackDelay(lesson, step)).toBeGreaterThan(5000);
  });
});
