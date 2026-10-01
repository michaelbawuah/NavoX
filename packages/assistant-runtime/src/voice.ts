import type { AssistantTurnView, AssistantVoiceState } from "@navox/contracts";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";
import { clampText } from "./validate";

export interface VoiceSessionState {
  state: AssistantVoiceState;
  /**
   * False once the browser reports no microphone. Typed input stays available,
   * and the flag survives playback so the session never claims a microphone it
   * does not have.
   */
  microphoneSupported: boolean;
  /** Automatic answer speech is suppressed until the operator unmutes. */
  muted: boolean;
  transcript: string | null;
  error: string | null;
  turn: number;
}

export function initialVoiceState(): VoiceSessionState {
  return {
    state: "IDLE",
    microphoneSupported: true,
    muted: false,
    transcript: null,
    error: null,
    turn: 0,
  };
}

export type VoiceEvent =
  | { type: "REQUEST_LISTENING" }
  | { type: "LISTENING_STARTED" }
  | { type: "TRANSCRIPTION_STARTED" }
  | { type: "TRANSCRIPT"; text: string }
  | { type: "TRANSCRIPT_FAILED"; reason: string }
  | { type: "SUBMITTED" }
  | { type: "ANSWER_READY" }
  | { type: "SPEAKING_STARTED" }
  | { type: "READ_ALOUD_STARTED" }
  | { type: "SPEAKING_ENDED" }
  | { type: "BARGE_IN" }
  | { type: "MUTE" }
  | { type: "UNMUTE" }
  | { type: "STOP" }
  | { type: "MICROPHONE_UNSUPPORTED"; reason: string }
  | { type: "RESET" };

const UNSUPPORTED_REASON = "This browser cannot capture microphone input.";

/** A stop is terminal for whatever the operator was hearing or saying. */
function isSettled(state: VoiceSessionState): boolean {
  return state.state === "STOPPED" || state.state === "UNSUPPORTED";
}

/**
 * Mute never aborts work the operator already started: an open microphone, a
 * transcription and a pending answer keep running, and only the spoken answer
 * is suppressed. A stop stays stopped and an unsupported browser stays
 * unsupported, because mute is not what ended those sessions. Every other
 * state rests as `MUTED`.
 */
function mutedState(state: VoiceSessionState): VoiceSessionState {
  const carriesState =
    state.state === "LISTENING" ||
    state.state === "TRANSCRIBING" ||
    state.state === "THINKING" ||
    state.state === "UNSUPPORTED" ||
    state.state === "STOPPED";
  return {
    ...state,
    muted: true,
    state: carriesState ? state.state : "MUTED",
  };
}

function unmutedState(state: VoiceSessionState): VoiceSessionState {
  const unmuted = { ...state, muted: false };
  return {
    ...unmuted,
    state: state.state === "MUTED" ? restingState(unmuted) : state.state,
  };
}

/**
 * Where the session rests when nothing is playing: idle with a working
 * microphone, unsupported without one, and muted whenever mute is on.
 */
function restingState(state: VoiceSessionState): AssistantVoiceState {
  if (state.muted) return "MUTED";
  return state.microphoneSupported ? "IDLE" : "UNSUPPORTED";
}

/**
 * Whether an arriving answer may start speaking by itself. Muted, stopped and
 * unsupported sessions stay silent; a typed turn is filtered before this.
 */
export function automaticSpeechAllowed(state: VoiceSessionState): boolean {
  return !state.muted && !isSettled(state);
}

/**
 * Click-to-talk state machine. It carries no authority: a transcript is input
 * for a turn, never an approval, and raw audio is never stored here.
 */
export function reduceVoiceState(
  state: VoiceSessionState,
  event: VoiceEvent,
): VoiceSessionState {
  switch (event.type) {
    case "REQUEST_LISTENING":
      if (!state.microphoneSupported) return state;
      return { ...state, state: "LISTENING", transcript: null, error: null };
    /**
     * `REQUEST_LISTENING` already opened the session, so a start confirmation
     * is always a no-op. A late `onstart` from a finished recognition instance
     * therefore cannot reopen a stopped, muted or unsupported session.
     */
    case "LISTENING_STARTED":
      return state;
    /**
     * The operator's second microphone click: capture really ended and the
     * clip is uploading. Only an open listening session may enter
     * transcription, so a duplicated click, a late callback or a click after
     * Stop can never move a settled session.
     */
    case "TRANSCRIPTION_STARTED":
      if (state.state !== "LISTENING") return state;
      return { ...state, state: "TRANSCRIBING", transcript: null };
    case "TRANSCRIPT": {
      // A transcript that arrives after Stop or an unsupported fallback is
      // stale input and must never open a new turn.
      if (isSettled(state)) return state;
      const text = clampText(event.text.trim(), LIMITS.maxQuestionLength);
      if (!text) return { ...state, state: "IDLE", transcript: null };
      return { ...state, state: "TRANSCRIBING", transcript: text, error: null };
    }
    case "TRANSCRIPT_FAILED":
      if (isSettled(state)) return state;
      return {
        ...state,
        state: "IDLE",
        transcript: null,
        error: clampText(event.reason, 200),
      };
    case "SUBMITTED":
      // A transcript that stopped before its turn was submitted must not be
      // promoted back into a pending answer.
      if (isSettled(state)) return state;
      return { ...state, state: "THINKING", error: null, turn: state.turn + 1 };
    case "ANSWER_READY":
      if (state.state !== "THINKING") return state;
      return { ...state, state: restingState(state) };
    case "SPEAKING_STARTED":
      // Mute, Stop and an unsupported browser all veto playback, so a late
      // synthesis start cannot restore a stale speaking state.
      if (!automaticSpeechAllowed(state)) return state;
      return { ...state, state: "SPEAKING" };
    /**
     * The explicit Read aloud control is a fresh operator request, not an
     * answer speaking for itself. It works after Stop and while only the
     * microphone is unsupported; mute still wins.
     */
    case "READ_ALOUD_STARTED":
      if (state.muted) return state;
      return { ...state, state: "SPEAKING" };
    case "SPEAKING_ENDED":
      return state.state === "SPEAKING"
        ? { ...state, state: restingState(state) }
        : state;
    /**
     * A new operator utterance interrupts playback. This is a manual
     * interruption: the client cancels synthesis first, then opens the
     * microphone. It is not acoustic barge-in.
     */
    case "BARGE_IN":
      if (!state.microphoneSupported) return state;
      return { ...state, state: "LISTENING", transcript: null, error: null };
    case "MUTE":
      return state.muted ? state : mutedState(state);
    case "UNMUTE":
      return state.muted ? unmutedState(state) : state;
    case "STOP":
      return { ...state, state: "STOPPED", transcript: null };
    case "MICROPHONE_UNSUPPORTED":
      return {
        ...state,
        state: "UNSUPPORTED",
        microphoneSupported: false,
        transcript: null,
        error: clampText(event.reason || UNSUPPORTED_REASON, 200),
      };
    case "RESET":
      return initialVoiceState();
  }
}

/** Longest spoken answer the runtime offers; mirrors `LIMITS.maxSpeechLength`. */
export const MAX_SPEECH_LENGTH = LIMITS.maxSpeechLength;

/**
 * The bounded spoken text of one saved turn, derived from the saved
 * presentation only. A question is never spoken, an answer that would need
 * approval stays silent, and a turn with no answer or notice has nothing to
 * say. The Next speech route uses this as the server-side source of truth and
 * the client uses it to decide whether Read aloud is offered.
 */
export function spokenTextForTurn(turn: AssistantTurnView): string | null {
  if (turn.decision?.requires_approval) return null;
  if (turn.state !== "READY" && turn.state !== "CLARIFY") return null;
  const speech = turn.presentation?.speech_text?.trim();
  if (turn.presentation?.speak && speech) {
    return clampText(speech, MAX_SPEECH_LENGTH);
  }
  const blocks = turn.presentation?.blocks ?? [];
  const answer = blocks.find((block) => block.kind === "ANSWER");
  if (answer?.kind === "ANSWER" && answer.text.trim()) {
    return clampText(answer.text.trim(), MAX_SPEECH_LENGTH);
  }
  const notice = blocks.find((block) => block.kind === "NOTICE");
  if (notice?.kind === "NOTICE" && notice.text.trim()) {
    return clampText(notice.text.trim(), MAX_SPEECH_LENGTH);
  }
  return null;
}

export interface WakeWordEvent {
  type: "wake";
  detected_at: string;
  device_id: string;
  confidence: number;
}

/**
 * Isolated boundary for a later local wake detector. A web or native
 * (Swift/Kotlin) adapter implements this without changing the runtime
 * contracts, and it may only deliver a wake event while the app is open.
 */
export interface WakeWordAdapter {
  readonly id: string;
  readonly supported: boolean;
  readonly active: boolean;
  start(listener: (event: WakeWordEvent) => void): Promise<void>;
  stop(): Promise<void>;
}

/**
 * M1 ships no detector and no background listening. This adapter is inert:
 * `supported` is false, nothing listens, and starting it fails loudly rather
 * than silently opening a microphone.
 */
export function createInactiveWakeWordAdapter(): WakeWordAdapter {
  return {
    id: "inactive",
    supported: false,
    active: false,
    async start() {
      throw new AssistantError(
        "unsupported",
        "Wake detection is not part of this release. Use the click-to-talk control.",
      );
    },
    async stop() {
      // Nothing is listening, so there is nothing to release.
    },
  };
}
