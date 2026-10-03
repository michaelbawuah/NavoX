/** Local, app-open wake phrase boundary. No transcript or audio is sent away. */
import type {
  WakeWordAdapter,
  WakeWordEvent,
} from "@navox/assistant-runtime/voice";

const LANGUAGE = "en-US";
const MIN_CONFIDENCE = 0.55;
const MAX_SUFFIX = 500;
const RESTART_DELAY_MS = 250;

interface RecognitionAlternative {
  transcript: string;
  confidence: number;
}
interface RecognitionResult {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: RecognitionAlternative;
}
interface RecognitionResultList {
  readonly length: number;
  [index: number]: RecognitionResult;
}
interface RecognitionEvent {
  resultIndex: number;
  results: RecognitionResultList;
}
interface LocalRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  processLocally?: boolean;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  abort(): void;
}
interface LocalRecognitionConstructor {
  new (): LocalRecognition;
  available(options: {
    langs: string[];
    processLocally: true;
  }): Promise<string>;
  install(options: { langs: string[]; processLocally: true }): Promise<boolean>;
}
export interface WakeScope {
  SpeechRecognition?: LocalRecognitionConstructor;
  webkitSpeechRecognition?: LocalRecognitionConstructor;
}
export interface WakeOptions {
  scope?: WakeScope;
  onError?: (message: string) => void;
  now?: () => Date;
}

/** A direct call must begin the utterance; quoted or reported phrases stay inert. */
export function resolveWakePhrase(
  transcript: string,
  confidence: number,
): { request_text?: string; confidence: number } | null {
  if (!Number.isFinite(confidence) || confidence < MIN_CONFIDENCE) return null;
  const match = /^\s*hey[,.!?]*\s+navox\b[,.!?\s]*/i.exec(transcript);
  if (!match) return null;
  const suffix = transcript.slice(match[0].length).trim().slice(0, MAX_SUFFIX);
  return suffix ? { request_text: suffix, confidence } : { confidence };
}

/**
 * The browser's on-device recognizer is used only as a wake detector. `start`
 * is called after the operator explicitly enables Hands-Free. It may download
 * a language pack then, but never switches to remote recognition.
 */
export class BrowserWakeWordAdapter implements WakeWordAdapter {
  readonly id = "browser-local-wake";
  private readonly engine: LocalRecognitionConstructor | null;
  private readonly onError: (message: string) => void;
  private readonly now: () => Date;
  private instance: LocalRecognition | null = null;
  private listener: ((event: WakeWordEvent) => void) | null = null;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;
  private starting = false;
  private rapidEnds = 0;
  private startedAt = 0;

  constructor(options: WakeOptions = {}) {
    const scope =
      options.scope ??
      (typeof window === "undefined" ? {} : (window as unknown as WakeScope));
    const candidate = scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
    this.engine =
      candidate &&
      typeof candidate.available === "function" &&
      typeof candidate.install === "function"
        ? candidate
        : null;
    this.onError = options.onError ?? (() => {});
    this.now = options.now ?? (() => new Date());
  }

  get supported(): boolean {
    return this.engine !== null;
  }

  get active(): boolean {
    return this.instance !== null;
  }

  async start(listener: (event: WakeWordEvent) => void): Promise<void> {
    if (this.starting || this.instance)
      throw new Error("Wake listening is already active.");
    const engine = this.engine;
    if (!engine)
      throw new Error(
        "On-device wake recognition is unavailable in this browser.",
      );
    this.starting = true;
    const generation = ++this.generation;
    try {
      const options = { langs: [LANGUAGE], processLocally: true as const };
      let status = await engine.available(options);
      if (status === "downloadable" || status === "downloading") {
        if (!(await engine.install(options)))
          throw new Error("The on-device speech pack could not be installed.");
        status = await engine.available(options);
      }
      if (status !== "available")
        throw new Error("The on-device speech pack is unavailable.");
      if (generation !== this.generation) return;
      const instance = new engine();
      if (!("processLocally" in instance)) {
        throw new Error(
          "This browser cannot guarantee local wake recognition.",
        );
      }
      instance.processLocally = true;
      if (instance.processLocally !== true) {
        throw new Error(
          "This browser cannot guarantee local wake recognition.",
        );
      }
      instance.lang = LANGUAGE;
      instance.continuous = true;
      instance.interimResults = false;
      instance.maxAlternatives = 1;
      this.listener = listener;
      this.instance = instance;
      this.rapidEnds = 0;
      this.startedAt = Date.now();
      instance.onresult = (event) => {
        if (this.instance !== instance || generation !== this.generation)
          return;
        for (
          let index = event.resultIndex;
          index < event.results.length;
          index++
        ) {
          const result = event.results[index];
          if (!result?.isFinal) continue;
          const candidate = result[0];
          const resolved =
            candidate &&
            resolveWakePhrase(candidate.transcript, candidate.confidence);
          if (!resolved) continue;
          const notify = this.listener;
          void this.stop();
          notify?.({
            type: "wake",
            detected_at: this.now().toISOString(),
            device_id: "browser-local",
            confidence: resolved.confidence,
            ...(resolved.request_text
              ? { request_text: resolved.request_text }
              : {}),
          });
          return;
        }
      };
      instance.onerror = () => {
        if (this.instance !== instance || generation !== this.generation)
          return;
        this.onError(
          "On-device wake listening stopped. Use the microphone button or try Hands-Free again.",
        );
        void this.stop();
      };
      instance.onend = () => {
        if (this.instance !== instance || generation !== this.generation)
          return;
        this.rapidEnds =
          Date.now() - this.startedAt < 1_000 ? this.rapidEnds + 1 : 0;
        if (this.rapidEnds >= 3) {
          this.onError(
            "On-device wake listening stopped. Use the microphone button or try Hands-Free again.",
          );
          void this.stop();
          return;
        }
        // Browsers end recognizers after a quiet window. Keep the explicitly
        // enabled wake state alive until the page disables it or becomes hidden.
        this.restartTimer = setTimeout(() => {
          this.restartTimer = null;
          if (this.instance !== instance || generation !== this.generation)
            return;
          try {
            this.startedAt = Date.now();
            instance.start();
          } catch {
            this.onError(
              "On-device wake listening stopped. Use the microphone button or try Hands-Free again.",
            );
            void this.stop();
          }
        }, RESTART_DELAY_MS);
      };
      instance.start();
    } catch (error) {
      if (generation === this.generation) await this.stop();
      throw error;
    } finally {
      this.starting = false;
    }
  }

  async stop(): Promise<void> {
    this.generation += 1;
    this.listener = null;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.restartTimer = null;
    const instance = this.instance;
    this.instance = null;
    if (instance) {
      instance.onresult = null;
      instance.onerror = null;
      instance.onend = null;
      try {
        instance.abort();
      } catch {
        /* Already stopped. */
      }
    }
  }
}

export function createBrowserWakeWordAdapter(
  options?: WakeOptions,
): BrowserWakeWordAdapter {
  return new BrowserWakeWordAdapter(options);
}
