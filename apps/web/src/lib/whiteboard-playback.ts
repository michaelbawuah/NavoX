import { type Lesson, parseCues } from "./whiteboard-tutor";

export type PlaybackState = {
  stepIndex: number;
  revealed: number;
  playing: boolean;
  complete: boolean;
  answers: Record<string, number>;
};

export type PlaybackEvent =
  | { type: "play" | "pause" | "tick" | "replay" | "restart" }
  | { type: "jump"; index: number }
  | { type: "answer"; choice: number };

export const initialPlayback = (): PlaybackState => ({
  stepIndex: 0,
  revealed: 0,
  playing: false,
  complete: false,
  answers: {},
});

export function needsAnswer(lesson: Lesson, state: PlaybackState): boolean {
  const step = lesson.steps[state.stepIndex];
  return Boolean(
    step.check &&
      state.revealed === step.actions.length &&
      state.answers[step.id] === undefined,
  );
}

/** A lesson advances only on playback, timeline navigation or an explicit answer. */
export function advancePlayback(
  lesson: Lesson,
  state: PlaybackState,
  event: PlaybackEvent,
): PlaybackState {
  const step = lesson.steps[state.stepIndex];
  switch (event.type) {
    case "restart":
      return initialPlayback();
    case "pause":
      return { ...state, playing: false };
    case "play":
      return state.complete || needsAnswer(lesson, state)
        ? state
        : { ...state, playing: true };
    case "jump":
      return Number.isInteger(event.index) && lesson.steps[event.index]
        ? {
            ...state,
            stepIndex: event.index,
            revealed: lesson.steps[event.index].actions.length,
            playing: false,
            complete: false,
          }
        : state;
    case "replay":
      return { ...state, revealed: 0, playing: true, complete: false };
    case "answer":
      return step.check &&
        Number.isInteger(event.choice) &&
        event.choice >= 0 &&
        event.choice < step.check.choices.length &&
        state.revealed === step.actions.length
        ? {
            ...state,
            playing: false,
            answers: { ...state.answers, [step.id]: event.choice },
          }
        : state;
    case "tick":
      if (!state.playing) return state;
      if (state.revealed < step.actions.length) {
        const next = { ...state, revealed: state.revealed + 1 };
        return needsAnswer(lesson, next) ? { ...next, playing: false } : next;
      }
      if (needsAnswer(lesson, state)) return { ...state, playing: false };
      if (state.stepIndex === lesson.steps.length - 1)
        return { ...state, playing: false, complete: true };
      return { ...state, stepIndex: state.stepIndex + 1, revealed: 0 };
  }
}

/** Reading pace between authored cues; this is not an audio synchronization clock. */
export function playbackDelay(
  lesson: Lesson,
  state: PlaybackState,
  speed = 1,
): number {
  if (state.revealed === 0 && lesson.steps[state.stepIndex].actions.length)
    return 500 / speed;
  const { text, cues } = parseCues(lesson.steps[state.stepIndex].say);
  const start =
    state.revealed === 1 ? 0 : (cues[state.revealed - 1]?.offset ?? 0);
  const end = cues[state.revealed]?.offset ?? text.length;
  const words = text
    .slice(start, end)
    .trim()
    .split(/\s+/u)
    .filter(Boolean).length;
  return Math.max(1500, (words / 180) * 60_000) / speed;
}

export function visibleBoardActions(
  lesson: Lesson,
  state: Pick<PlaybackState, "stepIndex" | "revealed">,
): ReadonlySet<string> {
  return new Set(
    lesson.steps.flatMap((step, index) =>
      index < state.stepIndex
        ? step.actions.map((action) => action.id)
        : index === state.stepIndex
          ? step.actions.slice(0, state.revealed).map((action) => action.id)
          : [],
    ),
  );
}
