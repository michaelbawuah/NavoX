import {
  automaticSpeechAllowed,
  initialVoiceState,
  reduceVoiceState,
  spokenTextForTurn,
  type VoiceEvent,
  type VoiceSessionState,
  voiceModeAllowsSpeech,
} from "@navox/assistant-runtime/voice";
import type {
  AssistantMessageResponse,
  AssistantModality,
  AssistantTurnView,
} from "@navox/contracts";
import type { AssistantTurnLedger } from "./assistant-client";
import type {
  SpeechAdapter,
  SynthesizeSpeech,
  TranscribeSpeech,
} from "./assistant-speech";

/**
 * Whether a saved turn may be spoken, and the bounded text a click would hear.
 * The server derives the text it synthesizes from the same saved turn; this
 * copy only decides whether the Read aloud control is offered at all.
 */
export const speechTextForTurn = spokenTextForTurn;

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
  /**
   * The saved turn whose answer may speak. `explicit` is the server-recorded
   * delivery preference: `true` only for an answer the operator asked for in
   * words, so the page honors it ahead of the Voice Mode default. The page never
   * derives this from the question text.
   */
  onSpeak: (turn: AssistantTurnView, explicit: boolean) => void;
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
        if (response.turn.presentation?.speak) {
          // Only a server-recorded SPEAK preference is an explicit request; a
          // spoken question follows the Voice Mode preference.
          deps.onSpeak(
            response.turn,
            response.turn.presentation.delivery === "SPEAK",
          );
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
  /**
   * Session-scoped upload of one bounded WAV clip. The page binds it to the
   * active session; the adapter owns the abort signal.
   */
  transcribe: TranscribeSpeech;
  /**
   * Session-scoped fetch of one bounded MP3 answer for a saved turn. The page
   * binds it to the active session; the adapter owns the abort signal.
   */
  synthesize: SynthesizeSpeech;
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
  speakAutomatic(turn: AssistantTurnView, token: number): void;
  /** The explicit Read aloud control. The page offers it only when unmuted. */
  speakManually(turn: AssistantTurnView): void;
  listen(): void;
  /** Opt-in Hands-Free clip: local silence finishes the SPEC-005 upload. */
  listenAutomatically(): void;
  /** Opt-in local speech monitor while TTS plays. */
  armBargeIn(): void;
  /** A local wake event resumes a stopped conversation without granting authority. */
  resumeForWake(): void;
  /**
   * The microphone control: the first click starts capture, the second click
   * finishes and transcribes it. Clicking during playback cancels speech first.
   */
  toggleListening(): void;
  /** Cancels microphone and speech and suppresses the in-flight answer. */
  stop(): void;
  setMuted(muted: boolean): void;
  toggleMuted(): void;
  /** The visible Voice Mode preference; it never carries authority. */
  setVoiceMode(enabled: boolean): void;
  dispose(): void;
}

export interface VoiceControls {
  listening: boolean;
  /** The microphone is open and buffering a clip. */
  capturing: boolean;
  /** The clip is uploaded and transcribed; the microphone control is inert. */
  transcribing: boolean;
  speaking: boolean;
  muted: boolean;
  /** Voice Mode: may a spoken question's answer start playing by itself? */
  voiceModeEnabled: boolean;
  microphoneDisabled: boolean;
  stopAvailable: boolean;
  readAloudAvailable: boolean;
}

/** How one saved answer should start speaking, if at all. */
export type SpeechStart = "AUTOMATIC" | "MANUAL" | "NONE";

/**
 * The one rule the page uses to start spoken playback.
 *
 * An explicit "read it to me" is the operator's own instruction, so it outranks
 * the Voice Mode default and works after Stop and without a microphone; mute
 * still vetoes it. A spoken question's answer is automatic only while Voice Mode
 * is on, mute is off and the session is neither stopped nor unsupported.
 */
export function speechStartForTurn(
  state: VoiceSessionState,
  explicit: boolean,
): SpeechStart {
  if (explicit) return state.muted ? "NONE" : "MANUAL";
  if (!automaticSpeechAllowed(state)) return "NONE";
  if (!voiceModeAllowsSpeech(state)) return "NONE";
  return "AUTOMATIC";
}

/** One source of truth for the microphone, mute, stop and read-aloud controls. */
export function voiceControls(state: VoiceSessionState): VoiceControls {
  const capturing = state.state === "LISTENING";
  const transcribing = state.state === "TRANSCRIBING";
  const speaking = state.state === "SPEAKING";
  return {
    listening: capturing || transcribing,
    capturing,
    transcribing,
    speaking,
    muted: state.muted,
    voiceModeEnabled: state.voiceMode,
    // The browser capability survives explicit playback, so a session that
    // cannot capture audio never offers an openable microphone.
    microphoneDisabled:
      state.state === "UNSUPPORTED" || !state.microphoneSupported,
    stopAvailable:
      capturing || transcribing || speaking || state.state === "THINKING",
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
  const startSpeech = (turn: AssistantTurnView, event: VoiceEvent) => {
    if (disposed || !speechTextForTurn(turn)) return;
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
    adapter.speak(
      turn.id,
      {
        onEnd: () => {
          adapter.stopListening();
          dispatch({ type: "SPEAKING_ENDED" });
        },
        onError: (reason) => {
          deps.onNotice(reason);
          adapter.stopListening();
          dispatch({ type: "SPEAKING_ENDED" });
        },
      },
      deps.synthesize,
    );
  };

  if (!adapter.captureSupported) {
    publish(
      reduceVoiceState(state, {
        type: "MICROPHONE_UNSUPPORTED",
        reason: adapter.captureReason ?? "",
      }),
    );
  }

  const capture = (mode: "manual" | "auto" | "barge") => {
    if (disposed) return;
    if (!adapter.captureSupported) {
      dispatch({
        type: "MICROPHONE_UNSUPPORTED",
        reason: adapter.captureReason ?? "",
      });
      return;
    }
    if (mode === "barge" && state.state !== "SPEAKING") return;
    endAttempt();
    const currentAttempt = attempt;
    let interrupted = mode !== "barge";
    const live = () => !disposed && currentAttempt === attempt;
    if (mode !== "barge") {
      adapter.stopSpeaking();
      dispatch({ type: "BARGE_IN" });
    }
    const handlers = {
      onTranscript: (text: string) => {
        if (!live()) return;
        const next = reduceVoiceState(state, { type: "TRANSCRIPT", text });
        publish(next);
        if (next.state !== "TRANSCRIBING" || !next.transcript) return;
        deps.onTranscript(voiceTurnRequest(next.transcript));
        if (state.state === "TRANSCRIBING") dispatch({ type: "STOP" });
      },
      onError: (reason: string) => {
        if (!live()) return;
        deps.onNotice(reason);
      },
      onEnd: () => {
        if (!live()) return;
        // A quiet barge-in monitor may end when TTS finishes, without a new
        // utterance. It must not stop the conversation or fabricate a turn.
        if (mode === "barge" && !interrupted) return;
        if (state.state === "THINKING") return;
        dispatch({ type: "STOP" });
      },
    };
    if (mode === "barge") {
      adapter.startBargeIn(handlers, deps.transcribe, () => {
        if (!live()) return;
        interrupted = true;
        dispatch({ type: "BARGE_IN" });
      });
    } else if (mode === "auto") {
      adapter.startAutomaticListening(handlers, deps.transcribe);
    } else {
      adapter.startListening(handlers, deps.transcribe);
    }
  };

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

    speakAutomatic(turn, token) {
      if (disposed || token !== turnToken || token <= stoppedToken) return;
      if (!automaticSpeechAllowed(state)) return;
      startSpeech(turn, { type: "SPEAKING_STARTED" });
    },

    speakManually(turn) {
      if (disposed || !voiceControls(state).readAloudAvailable) return;
      // Playback takes the session over: an open microphone is released and
      // its pending callbacks are invalidated before the answer is spoken.
      if (voiceControls(state).listening) {
        endAttempt();
        adapter.stopListening();
      }
      startSpeech(turn, { type: "READ_ALOUD_STARTED" });
    },

    listen() {
      capture("manual");
    },

    listenAutomatically() {
      capture("auto");
    },

    armBargeIn() {
      capture("barge");
    },

    resumeForWake() {
      if (disposed) return;
      dispatch({ type: "WAKE" });
    },

    toggleListening() {
      if (disposed) return;
      // The microphone control is a start/finish toggle: the first click opens
      // the microphone, the second ends capture and transcribes the clip. The
      // separate Stop control owns canceling a capture or an in-flight upload.
      if (state.state === "LISTENING") {
        // Only a capture that really became an upload may show transcription.
        if (adapter.finishListening())
          dispatch({ type: "TRANSCRIPTION_STARTED" });
        return;
      }
      if (state.state === "TRANSCRIBING") return;
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

    setVoiceMode(enabled) {
      if (disposed || state.voiceMode === enabled) return;
      // Turning Voice Mode off never interrupts playback already under way;
      // it changes whether the next voice answer starts by itself.
      dispatch({ type: "SET_VOICE_MODE", enabled });
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
