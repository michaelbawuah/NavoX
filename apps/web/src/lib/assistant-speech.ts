/**
 * Isolated browser speech adapters for the `/navox` page.
 *
 * These wrap `SpeechRecognition` and `speechSynthesis` only. They carry no
 * authority, they never run in the background, and every failure is reported
 * to the caller so the page can fall back to typed input.
 */

export interface SpeechHandlers {
  onStart?: () => void;
  onTranscript: (text: string) => void;
  onError: (reason: string) => void;
  onEnd: () => void;
}

export interface SpeechAdapter {
  readonly recognitionSupported: boolean;
  readonly synthesisSupported: boolean;
  /** Typed fallback copy when the microphone is unavailable. */
  readonly reason: string | null;
  /** Typed fallback copy when spoken answers are unavailable. */
  readonly synthesisReason: string | null;
  startListening(handlers: SpeechHandlers): void;
  /** Cancels listening. A buffered transcript is discarded, never submitted. */
  stopListening(): void;
  speak(text: string, handlers: Omit<SpeechHandlers, "onTranscript">): void;
  stopSpeaking(): void;
}

interface RecognitionEvent {
  results?: ArrayLike<ArrayLike<{ transcript?: string }>>;
  error?: string;
}

interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: RecognitionEvent) => void) | null;
  onend: (() => void) | null;
}

interface UtteranceLike {
  lang: string;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
}

interface SpeechScope {
  SpeechRecognition?: new () => RecognitionLike;
  webkitSpeechRecognition?: new () => RecognitionLike;
  speechSynthesis?: {
    speak(utterance: UtteranceLike): void;
    cancel(): void;
  };
  SpeechSynthesisUtterance?: new (text: string) => UtteranceLike;
}

function errorText(reason: string | undefined): string {
  if (reason === "not-allowed" || reason === "service-not-allowed") {
    return "Microphone access was blocked. Type your question instead.";
  }
  if (reason === "no-speech")
    return "No speech was heard. Try again or type instead.";
  if (reason === "aborted") return "Listening stopped.";
  return "Speech input stopped unexpectedly. Type your question instead.";
}

export function createSpeechAdapter(scope: unknown): SpeechAdapter {
  const environment = (scope ?? {}) as SpeechScope;
  const Recognition =
    environment.SpeechRecognition ?? environment.webkitSpeechRecognition;
  const synthesis = environment.speechSynthesis;
  const Utterance = environment.SpeechSynthesisUtterance;
  const recognitionSupported = typeof Recognition === "function";
  const synthesisSupported =
    typeof Utterance === "function" && Boolean(synthesis);
  let recognition: RecognitionLike | null = null;
  let cancelCurrent: (() => void) | null = null;
  let activeSpeech: { utterance: UtteranceLike; release: () => void } | null =
    null;

  const reason = recognitionSupported
    ? null
    : "This browser cannot capture microphone input. Type your question instead.";
  const synthesisReason = synthesisSupported
    ? null
    : "This browser cannot play spoken answers. Read the answer instead.";

  /**
   * Detaches first: a cancelled utterance must not report a late end or error
   * into a session that already moved on.
   */
  const cancelSpeech = () => {
    const current = activeSpeech;
    activeSpeech = null;
    current?.release();
    synthesis?.cancel();
  };

  return {
    recognitionSupported,
    synthesisSupported,
    reason,
    synthesisReason,

    startListening(handlers) {
      if (!Recognition) {
        handlers.onError(reason ?? "Speech input is unavailable.");
        handlers.onEnd();
        return;
      }
      // A new operator utterance takes the turn: any speech in flight stops.
      cancelSpeech();
      if (recognition) {
        recognition.onend = null;
        recognition.onerror = null;
        recognition.onresult = null;
        recognition.abort();
        recognition = null;
      }
      cancelCurrent = null;
      const instance = new Recognition();
      instance.lang = "en-US";
      instance.continuous = false;
      instance.interimResults = false;
      let transcript = "";
      let failed = false;
      let cancelled = false;
      instance.onresult = (event) => {
        const first = event.results?.[0]?.[0]?.transcript;
        if (typeof first === "string") transcript = first;
      };
      instance.onerror = (event) => {
        failed = true;
        handlers.onError(errorText(event.error));
      };
      instance.onend = () => {
        recognition = null;
        cancelCurrent = null;
        if (!failed && !cancelled && transcript.trim()) {
          handlers.onTranscript(transcript);
        }
        handlers.onEnd();
      };
      cancelCurrent = () => {
        cancelled = true;
        transcript = "";
        try {
          instance.abort();
        } catch {
          // Already stopped; nothing else to release.
        }
      };
      recognition = instance;
      handlers.onStart?.();
      try {
        instance.start();
      } catch {
        recognition = null;
        cancelCurrent = null;
        handlers.onError(
          "Speech input could not start. Type your question instead.",
        );
        handlers.onEnd();
      }
    },

    stopListening() {
      const cancel = cancelCurrent;
      cancelCurrent = null;
      cancel?.();
    },

    speak(text, handlers) {
      if (!Utterance || !synthesis || !text.trim()) {
        handlers.onEnd();
        return;
      }
      cancelSpeech();
      const utterance = new Utterance(text);
      utterance.lang = "en-US";
      const release = () => {
        utterance.onstart = null;
        utterance.onend = null;
        utterance.onerror = null;
      };
      const settle = () => {
        activeSpeech = null;
        release();
      };
      utterance.onstart = () => handlers.onStart?.();
      utterance.onend = () => {
        settle();
        handlers.onEnd();
      };
      utterance.onerror = () => {
        settle();
        handlers.onError("The spoken answer could not be played.");
        handlers.onEnd();
      };
      activeSpeech = { utterance, release };
      synthesis.speak(utterance);
    },

    stopSpeaking() {
      cancelSpeech();
    },
  };
}
