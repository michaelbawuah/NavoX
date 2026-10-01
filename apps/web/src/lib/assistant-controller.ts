import {
  automaticSpeechAllowed,
  initialVoiceState,
  reduceVoiceState,
  type VoiceEvent,
  type VoiceSessionState,
} from "@navox/assistant-runtime/voice";
import type {
  AssistantMessageResponse,
  AssistantModality,
  AssistantTurnView,
} from "@navox/contracts";
import type { AssistantTurnLedger } from "./assistant-client";
import type { SpeechAdapter } from "./assistant-speech";

/** Matches the runtime's spoken-text bound. */
export const MAX_SPEECH_LENGTH = 600;

function clampSpeech(text: string): string {
  return text.length <= MAX_SPEECH_LENGTH
    ? text
    : text.slice(0, MAX_SPEECH_LENGTH);
}

/**
 * Spoken text for a saved turn. The server speaks the first answer block;
 * a clicked Speak control uses the same text so a typed answer can be replayed
 * on demand without ever speaking by itself.
 */
export function speechTextForTurn(turn: AssistantTurnView): string | null {
  const blocks = turn.presentation?.blocks ?? [];
  const answer = blocks.find((block) => block.kind === "ANSWER");
  if (answer?.kind === "ANSWER" && answer.text.trim()) {
    return clampSpeech(answer.text.trim());
  }
  const speech = turn.presentation?.speak
    ? turn.presentation.speech_text
    : null;
  return speech?.trim() ? clampSpeech(speech.trim()) : null;
}

/** Eligible answer turns may offer a Speak control; notices alone do not. */
export function canSpeakTurn(turn: AssistantTurnView): boolean {
  return speechTextForTurn(turn) !== null;
}

export type NoticeHandler = (
  message: string,
  modality: AssistantModality,
) => void;

export interface AssistantTurnRunnerDeps {
  submit: (
    sessionId: string,
    input: {
      requestId: string;
      text: string;
      modality: AssistantModality;
      timezone: string | null;
      referents?: string[];
    },
  ) => Promise<AssistantMessageResponse>;
  ledger: AssistantTurnLedger;
  timezone?: () => string | null;
  onTurn: (turn: AssistantTurnView) => void;
  onNotice: NoticeHandler;
  onBusy: (busy: boolean) => void;
  onSpeak: (text: string) => void;
  messageForError?: (error: unknown) => string;
}

export interface AssistantTurnRunner {
  generation(): number;
  /** Abandons in-flight work: cleared history or an unmounted page. */
  invalidate(): void;
  run(input: {
    sessionId: string | null;
    modality: AssistantModality;
    text: string;
    referents?: string[];
  }): Promise<void>;
}

/**
 * Runs one turn at a time and drops late completions from an older generation,
 * so clearing the conversation or unmounting can never append a stale answer or
 * speak it after the fact.
 */
export function createAssistantTurnRunner(
  deps: AssistantTurnRunnerDeps,
): AssistantTurnRunner {
  let generation = 0;
  let busy = false;

  return {
    generation: () => generation,

    invalidate() {
      generation += 1;
      busy = false;
      deps.ledger.forget();
      deps.onBusy(false);
    },

    async run({ sessionId, modality, text, referents = [] }) {
      const question = text.trim();
      if (!sessionId || !question || busy) return;
      const token = generation;
      const current = () => token === generation;
      busy = true;
      deps.onBusy(true);
      try {
        // Request-id creation can fail when secure crypto is unavailable; that
        // must surface as a notice rather than an unhandled rejection.
        const requestId = deps.ledger.requestIdFor(
          `${modality}:${question}:${JSON.stringify(referents)}`,
        );
        const response = await deps.submit(sessionId, {
          requestId,
          text: question,
          modality,
          timezone: deps.timezone?.() ?? null,
          referents,
        });
        if (!current()) return;
        deps.ledger.resolve();
        deps.onTurn(response.turn);
        if (modality === "VOICE") {
          const speech = response.turn.presentation?.speak
            ? response.turn.presentation.speech_text
            : null;
          if (speech) deps.onSpeak(speech);
        }
      } catch (error) {
        if (!current()) return;
        deps.onNotice(
          deps.messageForError?.(error) ??
            "The assistant could not answer right now. Please try again.",
          modality,
        );
      } finally {
        if (current()) {
          busy = false;
          deps.onBusy(false);
        }
      }
    },
  };
}

/**
 * A transcript is plain input for one turn: it carries the operator's own
 * words and nothing else. There is no approval, action id or capability field,
 * so a spoken "yes" can only ever be a question.
 */
export interface VoiceTurnRequest {
  modality: "VOICE";
  text: string;
  referents: string[];
}

export function voiceTurnRequest(transcript: string): VoiceTurnRequest {
  return { modality: "VOICE", text: transcript.trim(), referents: [] };
}

export interface VoiceSessionDeps {
  adapter: SpeechAdapter;
  /** A finished transcript the page may submit as one voice turn. */
  onTranscript: (request: VoiceTurnRequest) => void;
  onNotice: (message: string) => void;
  onState: (state: VoiceSessionState) => void;
}

export interface VoiceSession {
  current(): VoiceSessionState;
  /** Opens a voice turn and returns the token its answer may speak with. */
  beginTurn(): number;
  /** The page submitted the transcript as one turn. */
  markSubmitted(): void;
  /** The pending voice turn produced an answer. */
  answerReady(): void;
  /** The pending voice turn failed before it produced an answer. */
  turnFailed(reason: string): void;
  /** Speaks an answer for a live turn only. Stale, muted and stopped ones stay silent. */
  speakAutomatic(text: string, token: number): void;
  /** The explicit Read aloud control. The page offers it only when unmuted. */
  speakManually(text: string): void;
  listen(): void;
  /** A microphone click while playback runs cancels speech first. */
  toggleListening(): void;
  /** Cancels microphone and speech and suppresses the in-flight answer. */
  stop(): void;
  setMuted(muted: boolean): void;
  toggleMuted(): void;
  dispose(): void;
}

export interface VoiceControls {
  listening: boolean;
  speaking: boolean;
  muted: boolean;
  microphoneDisabled: boolean;
  stopAvailable: boolean;
  readAloudAvailable: boolean;
}

/** One source of truth for the microphone, mute, stop and read-aloud controls. */
export function voiceControls(state: VoiceSessionState): VoiceControls {
  const listening =
    state.state === "LISTENING" || state.state === "TRANSCRIBING";
  const speaking = state.state === "SPEAKING";
  return {
    listening,
    speaking,
    muted: state.muted,
    // The browser capability survives explicit playback, so a session that
    // cannot capture audio never offers an openable microphone.
    microphoneDisabled:
      state.state === "UNSUPPORTED" || !state.microphoneSupported,
    stopAvailable: listening || speaking || state.state === "THINKING",
    readAloudAvailable: !state.muted,
  };
}

/**
 * Owns the click-to-talk lifecycle: microphone, speech, mute and stop. It
 * holds no authority, and every late adapter callback is dropped once the
 * session is stopped, cleared or disposed.
 */
export function createVoiceSession(deps: VoiceSessionDeps): VoiceSession {
  const { adapter } = deps;
  let state = initialVoiceState();
  let turnToken = 0;
  let stoppedToken = 0;
  /**
   * Identity of the current microphone attempt. Stop, a new listen, an
   * explicit read aloud and disposal all advance it, so a callback from an
   * older attempt can neither submit a transcript nor change the session.
   */
  let attempt = 0;
  let disposed = false;

  const publish = (next: VoiceSessionState) => {
    if (disposed || next === state) return;
    state = next;
    deps.onState(state);
  };

  const dispatch = (event: VoiceEvent) => {
    publish(reduceVoiceState(state, event));
  };

  const endAttempt = () => {
    attempt += 1;
  };

  /**
   * Starts playback only when the state machine accepts the transition, so the
   * UI state and the audio can never disagree about who is speaking.
   */
  const startSpeech = (text: string, event: VoiceEvent) => {
    if (disposed || !text.trim()) return;
    if (!adapter.synthesisSupported) {
      deps.onNotice(
        adapter.synthesisReason ??
          "This browser cannot play spoken answers. Read the answer instead.",
      );
      return;
    }
    const next = reduceVoiceState(state, event);
    if (next.state !== "SPEAKING") return;
    publish(next);
    adapter.speak(text, {
      onEnd: () => dispatch({ type: "SPEAKING_ENDED" }),
      onError: (reason) => {
        deps.onNotice(reason);
        dispatch({ type: "SPEAKING_ENDED" });
      },
    });
  };

  if (!adapter.recognitionSupported) {
    publish(
      reduceVoiceState(state, {
        type: "MICROPHONE_UNSUPPORTED",
        reason: adapter.reason ?? "",
      }),
    );
  }

  return {
    current: () => state,

    beginTurn() {
      turnToken += 1;
      return turnToken;
    },

    markSubmitted() {
      dispatch({ type: "SUBMITTED" });
    },

    answerReady() {
      dispatch({ type: "ANSWER_READY" });
    },

    turnFailed(reason) {
      dispatch({ type: "TRANSCRIPT_FAILED", reason });
    },

    speakAutomatic(text, token) {
      if (disposed || token !== turnToken || token <= stoppedToken) return;
      if (!automaticSpeechAllowed(state)) return;
      startSpeech(text, { type: "SPEAKING_STARTED" });
    },

    speakManually(text) {
      if (disposed || !voiceControls(state).readAloudAvailable) return;
      // Playback takes the session over: an open microphone is released and
      // its pending callbacks are invalidated before the answer is spoken.
      if (voiceControls(state).listening) {
        endAttempt();
        adapter.stopListening();
      }
      startSpeech(text, { type: "READ_ALOUD_STARTED" });
    },

    listen() {
      if (disposed) return;
      if (!adapter.recognitionSupported) {
        dispatch({
          type: "MICROPHONE_UNSUPPORTED",
          reason: adapter.reason ?? "",
        });
        return;
      }
      endAttempt();
      const currentAttempt = attempt;
      const live = () => !disposed && currentAttempt === attempt;
      // Manual interruption: cancel playback first, then open the microphone.
      adapter.stopSpeaking();
      dispatch({ type: "BARGE_IN" });
      adapter.startListening({
        onTranscript: (text) => {
          if (!live()) return;
          // The session decides whether this transcript is still current; a
          // late result after Stop or unmount is never submitted.
          const next = reduceVoiceState(state, { type: "TRANSCRIPT", text });
          publish(next);
          if (next.state !== "TRANSCRIBING" || !next.transcript) return;
          deps.onTranscript(voiceTurnRequest(next.transcript));
        },
        onError: (reason) => {
          if (!live()) return;
          deps.onNotice(reason);
        },
        onEnd: () => {
          if (!live()) return;
          // A finished recognition session that produced a turn leaves the
          // pending answer alone; anything else releases the microphone.
          if (state.state === "TRANSCRIBING" || state.state === "THINKING")
            return;
          dispatch({ type: "STOP" });
        },
      });
    },

    toggleListening() {
      if (voiceControls(state).listening) {
        endAttempt();
        adapter.stopListening();
        dispatch({ type: "STOP" });
        return;
      }
      this.listen();
    },

    stop() {
      if (disposed) return;
      endAttempt();
      // The turn already in flight loses its right to speak, and the next
      // voice turn gets a fresh token.
      stoppedToken = turnToken;
      adapter.stopSpeaking();
      adapter.stopListening();
      dispatch({ type: "STOP" });
    },

    setMuted(muted) {
      if (disposed || state.muted === muted) return;
      if (muted) adapter.stopSpeaking();
      dispatch({ type: muted ? "MUTE" : "UNMUTE" });
    },

    toggleMuted() {
      this.setMuted(!state.muted);
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      endAttempt();
      stoppedToken = turnToken;
      adapter.stopSpeaking();
      adapter.stopListening();
    },
  };
}
