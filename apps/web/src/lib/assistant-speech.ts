/**
 * Isolated browser speech adapters for the `/navox` page.
 *
 * Microphone capture is a click-to-talk recording: the first click opens the
 * microphone and buffers PCM frames in memory, the second click encodes one
 * bounded 16 kHz mono WAV clip and hands it to an injected uploader. The reply
 * text is the only thing that leaves this module. Spoken answers are bounded
 * MP3 payloads fetched by an injected session-scoped selector and played with
 * an `Audio` element; the browser `speechSynthesis` vendor path is removed.
 *
 * Nothing here carries authority, runs in the background, persists audio or
 * logs it, and every failure is reported to the caller so the page can fall
 * back to typed input.
 */

import { MAX_SPEECH_AUDIO_BYTES } from "./assistant-client";
import { VoiceActivityDetector } from "./assistant-voice-activity";
import {
  encodeWavPcm16,
  MAX_WAV_MILLISECONDS,
  parseAssistantWav,
  SpeechClipError,
} from "./assistant-wav";

export interface SpeechHandlers {
  onStart?: () => void;
  onTranscript: (text: string) => void;
  onError: (reason: string) => void;
  onEnd: () => void;
}

/**
 * Uploads one bounded WAV clip and resolves the bounded question text. The
 * implementation owns the same-origin fetch; the adapter owns the abort.
 */
export type TranscribeSpeech = (
  wav: Uint8Array<ArrayBuffer>,
  signal: AbortSignal,
) => Promise<string>;

/**
 * Fetches one bounded MP3 answer for a saved turn. The implementation owns the
 * same-origin fetch; the adapter owns the abort and the object URL.
 */
export type SynthesizeSpeech = (
  turnId: string,
  signal: AbortSignal,
) => Promise<Uint8Array<ArrayBuffer>>;

export interface SpeechAdapter {
  readonly captureSupported: boolean;
  /** Typed fallback copy when the microphone is unavailable. */
  readonly captureReason: string | null;
  readonly synthesisSupported: boolean;
  /** Typed fallback copy when spoken answers are unavailable. */
  readonly synthesisReason: string | null;
  /** Opens the microphone and starts buffering one clip. */
  startListening(handlers: SpeechHandlers, transcribe: TranscribeSpeech): void;
  /** Opt-in Hands-Free capture, automatically finished after local silence. */
  startAutomaticListening(
    handlers: SpeechHandlers,
    transcribe: TranscribeSpeech,
  ): void;
  /**
   * Monitor locally while TTS plays. Actual user speech cancels playback and
   * is retained in the same bounded WAV clip sent to SPEC-005.
   */
  startBargeIn(
    handlers: SpeechHandlers,
    transcribe: TranscribeSpeech,
    onSpeech: () => void,
  ): void;
  /**
   * The operator's second click: finish the clip and transcribe it. Returns
   * true only when this call really ended a recording and started the upload,
   * so the page never claims a transcription that is not running.
   */
  finishListening(): boolean;
  /** Stop, clear or unmount: abort capture or the in-flight upload. */
  stopListening(): void;
  /**
   * Fetches and plays one bounded saved-turn answer. `turnId` is a selector,
   * never the spoken text: the server derives and bounds the answer.
   */
  speak(
    turnId: string,
    handlers: Omit<SpeechHandlers, "onTranscript">,
    synthesize: SynthesizeSpeech,
  ): void;
  stopSpeaking(): void;
}

interface MediaStreamTrackLike {
  stop(): void;
}

interface MediaStreamLike {
  getTracks(): MediaStreamTrackLike[];
}

interface AudioProcessEventLike {
  inputBuffer: { getChannelData(channel: number): Float32Array };
}

interface ScriptProcessorLike {
  onaudioprocess: ((event: AudioProcessEventLike) => void) | null;
  connect(node: unknown): void;
  disconnect(): void;
}

interface MediaStreamSourceLike {
  connect(node: unknown): void;
  disconnect(): void;
}

interface GainLike {
  gain: { value: number };
  connect(node: unknown): void;
  disconnect(): void;
}

interface AudioContextLike {
  sampleRate: number;
  destination: unknown;
  createMediaStreamSource(stream: MediaStreamLike): MediaStreamSourceLike;
  createScriptProcessor?(
    bufferSize: number,
    inputChannels: number,
    outputChannels: number,
  ): ScriptProcessorLike;
  createGain?(): GainLike;
  close?(): Promise<void> | void;
}

interface AudioElementLike {
  src: string;
  onended: (() => void) | null;
  onerror: (() => void) | null;
  play(): Promise<void> | void;
  pause(): void;
}

interface CaptureScope {
  navigator?: {
    mediaDevices?: {
      getUserMedia?: (constraints: {
        audio:
          | boolean
          | {
              echoCancellation: boolean;
              noiseSuppression: boolean;
              autoGainControl: boolean;
            };
      }) => Promise<MediaStreamLike>;
    };
  };
  AudioContext?: new () => AudioContextLike;
  webkitAudioContext?: new () => AudioContextLike;
  Audio?: new (src?: string) => AudioElementLike;
  Blob?: new (parts: unknown[], options?: { type?: string }) => Blob;
  URL?: {
    createObjectURL?: (blob: Blob) => string;
    revokeObjectURL?: (url: string) => void;
  };
}

const BUFFER_FRAMES = 4_096;
/** Small slack so the frame cap, not the timer, wins a 30-second tie. */
const CEILING_SLACK_MS = 250;
const CEILING_REASON =
  "That recording reached 30 seconds and was not sent. Record a shorter question.";
const CAPTURE_REASON =
  "This browser cannot record from the microphone. Type your question instead.";
const SYNTHESIS_REASON =
  "This browser cannot play spoken answers. Read the answer instead.";
const SYNTHESIS_FAILED =
  "The spoken answer could not be played. Read the answer instead.";

/** One live or in-flight microphone attempt. Audio never leaves this object. */
interface Attempt {
  controller: AbortController;
  handlers: SpeechHandlers;
  transcribe: TranscribeSpeech;
  phase: "opening" | "monitoring" | "recording" | "uploading";
  mode: "manual" | "auto" | "barge";
  activity: VoiceActivityDetector | null;
  onSpeech: (() => void) | null;
  preRollFrames: Float32Array[];
  preRollSamples: number;
  settled: boolean;
  buffered: number;
  frames: Float32Array[];
  context: AudioContextLike | null;
  source: MediaStreamSourceLike | null;
  processor: ScriptProcessorLike | null;
  sink: GainLike | null;
  stream: MediaStreamLike | null;
  timer: ReturnType<typeof setTimeout> | null;
}

/** One in-flight fetch and the audio element it may start. */
interface SpeechAttempt {
  controller: AbortController;
  audio: AudioElementLike | null;
  url: string | null;
  release: () => void;
}

function permissionMessage(error: unknown): string {
  const name =
    error && typeof error === "object" && "name" in error
      ? String((error as { name?: unknown }).name)
      : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone access was blocked. Type your question instead.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "No microphone was found. Type your question instead.";
  }
  return "The microphone could not start. Type your question instead.";
}

function failureMessage(error: unknown): string {
  if (error instanceof SpeechClipError) return error.message;
  if (error instanceof Error && error.message.trim()) return error.message;
  return "The recording could not be transcribed. Type your question instead.";
}

export function createSpeechAdapter(scope: unknown): SpeechAdapter {
  const environment = (scope ?? {}) as CaptureScope;
  const mediaDevices = environment.navigator?.mediaDevices;
  const AudioContextCtor =
    environment.AudioContext ?? environment.webkitAudioContext;
  const AudioCtor = environment.Audio;
  const BlobCtor = environment.Blob;
  const objectUrls = environment.URL;
  const captureSupported =
    typeof mediaDevices?.getUserMedia === "function" &&
    typeof AudioContextCtor === "function" &&
    typeof AudioContextCtor.prototype?.createScriptProcessor === "function";
  const synthesisSupported =
    typeof AudioCtor === "function" &&
    typeof BlobCtor === "function" &&
    typeof objectUrls?.createObjectURL === "function" &&
    typeof objectUrls?.revokeObjectURL === "function";

  const captureReason = captureSupported ? null : CAPTURE_REASON;
  const synthesisReason = synthesisSupported ? null : SYNTHESIS_REASON;

  let attempt: Attempt | null = null;
  let activeSpeech: SpeechAttempt | null = null;

  /**
   * Detaches first: a cancelled playback must not report a late end or error
   * into a session that already moved on, and its object URL is revoked.
   */
  const releaseSpeech = (target: SpeechAttempt) => {
    target.release();
    try {
      target.audio?.pause();
    } catch {
      // An element that never started needs no further release.
    }
    if (target.url) {
      try {
        objectUrls?.revokeObjectURL?.(target.url);
      } catch {
        // A revoked URL is already released.
      }
    }
  };

  const cancelSpeech = () => {
    const current = activeSpeech;
    activeSpeech = null;
    if (!current) return;
    current.controller.abort();
    releaseSpeech(current);
  };

  const releaseRecording = (target: Attempt) => {
    if (target.timer !== null) {
      clearTimeout(target.timer);
      target.timer = null;
    }
    if (target.processor) {
      target.processor.onaudioprocess = null;
      target.processor.disconnect();
    }
    target.source?.disconnect();
    target.sink?.disconnect();
    void target.context?.close?.();
    for (const track of target.stream?.getTracks() ?? []) {
      try {
        track.stop();
      } catch {
        // A track that already ended needs no further release.
      }
    }
    target.phase = "uploading";
  };

  /** Terminal callback for one attempt: never twice, never after it is over. */
  const settle = (
    target: Attempt,
    outcome: { text: string } | { error: string } | null,
  ) => {
    if (target.settled) return;
    target.settled = true;
    if (target.timer !== null) {
      clearTimeout(target.timer);
      target.timer = null;
    }
    if (attempt === target) attempt = null;
    if (outcome && "text" in outcome)
      target.handlers.onTranscript(outcome.text);
    if (outcome && "error" in outcome) target.handlers.onError(outcome.error);
    target.handlers.onEnd();
  };

  const abandonAttempt = (target: Attempt) => {
    if (attempt === target) attempt = null;
    // An in-flight upload loses its right to answer before the tracks close.
    target.controller.abort();
    releaseRecording(target);
    if (!target.settled) {
      target.settled = true;
      target.handlers.onEnd();
    }
  };

  /**
   * The microphone never stays open past the contract's ceiling: the graph is
   * closed, every track is stopped, and the operator gets bounded typed copy
   * instead of a clip SPEC-005 would refuse.
   */
  const stopAtCeiling = (target: Attempt) => {
    if (
      attempt !== target ||
      target.settled ||
      (target.phase !== "recording" && target.phase !== "monitoring")
    ) {
      return;
    }
    const heardSpeech = target.phase === "recording";
    releaseRecording(target);
    settle(target, heardSpeech ? { error: CEILING_REASON } : null);
  };

  const upload = async (target: Attempt, wav: Uint8Array<ArrayBuffer>) => {
    try {
      const text = await target.transcribe(wav, target.controller.signal);
      if (attempt !== target || target.controller.signal.aborted) return;
      const question = typeof text === "string" ? text.trim() : "";
      if (!question) {
        settle(target, {
          error: "No speech was heard. Type your question instead.",
        });
        return;
      }
      settle(target, { text: question });
    } catch (error) {
      if (attempt !== target || target.controller.signal.aborted) return;
      settle(target, { error: failureMessage(error) });
    }
  };

  /** Close one recording and upload its bounded WAV through the injected route. */
  const finishClip = (target: Attempt): boolean => {
    if (attempt !== target || target.settled || target.phase !== "recording")
      return false;
    const sampleRate = target.context?.sampleRate ?? 0;
    const frames = target.frames;
    target.frames = [];
    releaseRecording(target);
    let wav: Uint8Array<ArrayBuffer>;
    try {
      const total = frames.reduce((sum, frame) => sum + frame.length, 0);
      const merged = new Float32Array(total);
      let offset = 0;
      for (const frame of frames) {
        merged.set(frame, offset);
        offset += frame.length;
      }
      wav = encodeWavPcm16(merged, sampleRate);
      parseAssistantWav(wav);
    } catch (error) {
      settle(target, { error: failureMessage(error) });
      return false;
    }
    void upload(target, wav);
    return true;
  };

  const openMicrophone = async (target: Attempt) => {
    const devices = mediaDevices;
    const ContextCtor = AudioContextCtor;
    if (
      !devices ||
      typeof devices.getUserMedia !== "function" ||
      typeof ContextCtor !== "function"
    ) {
      settle(target, { error: CAPTURE_REASON });
      return;
    }
    let stream: MediaStreamLike;
    try {
      // Called on the MediaDevices object: a bare function reference loses the
      // receiver a WebIDL brand check requires.
      stream = await devices.getUserMedia({
        audio:
          target.mode === "manual"
            ? true
            : {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
              },
      });
    } catch (error) {
      if (attempt !== target || target.settled) return;
      settle(target, { error: permissionMessage(error) });
      return;
    }
    // The operator may have stopped, cleared or restarted while the browser
    // was still asking: release the tracks instead of recording them.
    if (
      attempt !== target ||
      target.settled ||
      target.controller.signal.aborted
    ) {
      for (const track of stream.getTracks?.() ?? []) {
        try {
          track.stop();
        } catch {
          // Nothing to release.
        }
      }
      return;
    }
    target.stream = stream;
    try {
      const context = new ContextCtor();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor?.(BUFFER_FRAMES, 1, 1);
      if (!processor) throw new Error("no script processor");
      // A silent sink keeps the graph pulling frames in browsers that only
      // run a processor node while it reaches the destination.
      const sink = context.createGain?.() ?? null;
      // The contract's 30-second ceiling is enforced on both the buffered
      // frames and the wall clock, so a forgotten recording can neither grow
      // without bound nor leave the microphone open.
      const maxFrames = Math.ceil(
        (MAX_WAV_MILLISECONDS / 1_000) * context.sampleRate,
      );
      processor.onaudioprocess = (event) => {
        if (target.settled || attempt !== target) return;
        const remaining = maxFrames - target.buffered;
        if (remaining <= 0) return;
        const channel = event.inputBuffer.getChannelData(0);
        const chunk =
          channel.length > remaining ? channel.subarray(0, remaining) : channel;
        const frame = Float32Array.from(chunk);
        if (target.mode === "manual") {
          target.frames.push(frame);
          target.buffered += frame.length;
          // Talk can still be finished by a second click, but ordinary speech
          // ends automatically after the same local silence window.
          if (target.activity?.observe(frame, context.sampleRate).ended) {
            finishClip(target);
            return;
          }
        } else {
          const activity = target.activity?.observe(frame, context.sampleRate);
          if (target.phase === "monitoring") {
            target.preRollFrames.push(frame);
            target.preRollSamples += frame.length;
            const maxPreRoll = Math.ceil(context.sampleRate * 0.5);
            while (
              target.preRollSamples > maxPreRoll &&
              target.preRollFrames.length > 1
            ) {
              const removed = target.preRollFrames.shift();
              target.preRollSamples -= removed?.length ?? 0;
            }
            if (activity?.started) {
              target.phase = "recording";
              target.frames.push(...target.preRollFrames);
              target.buffered = target.preRollSamples;
              target.preRollFrames = [];
              target.preRollSamples = 0;
              try {
                target.onSpeech?.();
              } catch {
                abandonAttempt(target);
                return;
              }
            }
          } else {
            target.frames.push(frame);
            target.buffered += frame.length;
          }
          if (activity?.ended && target.phase === "recording") {
            finishClip(target);
            return;
          }
        }
        if (target.buffered >= maxFrames) stopAtCeiling(target);
      };
      source.connect(processor);
      if (sink) {
        sink.gain.value = 0;
        processor.connect(sink);
        sink.connect(context.destination);
      } else {
        processor.connect(context.destination);
      }
      target.context = context;
      target.source = source;
      target.processor = processor;
      target.sink = sink;
      target.phase = target.mode === "manual" ? "recording" : "monitoring";
      target.timer = setTimeout(
        () => stopAtCeiling(target),
        MAX_WAV_MILLISECONDS + CEILING_SLACK_MS,
      );
    } catch {
      releaseRecording(target);
      settle(target, {
        error: "The microphone could not start. Type your question instead.",
      });
    }
  };

  const beginCapture = (
    mode: Attempt["mode"],
    handlers: SpeechHandlers,
    transcribe: TranscribeSpeech,
    onSpeech: (() => void) | null = null,
  ) => {
    if (mode !== "barge") cancelSpeech();
    const previous = attempt;
    if (previous) abandonAttempt(previous);
    if (!captureSupported) {
      handlers.onError(captureReason ?? CAPTURE_REASON);
      handlers.onEnd();
      return;
    }
    const target: Attempt = {
      controller: new AbortController(),
      handlers,
      transcribe,
      phase: "opening",
      mode,
      activity: new VoiceActivityDetector(mode === "barge"),
      onSpeech,
      preRollFrames: [],
      preRollSamples: 0,
      settled: false,
      buffered: 0,
      frames: [],
      context: null,
      source: null,
      processor: null,
      sink: null,
      stream: null,
      timer: null,
    };
    attempt = target;
    if (mode !== "barge") handlers.onStart?.();
    void openMicrophone(target);
  };

  return {
    captureSupported,
    captureReason,
    synthesisSupported,
    synthesisReason,

    startListening(handlers, transcribe) {
      beginCapture("manual", handlers, transcribe);
    },

    startAutomaticListening(handlers, transcribe) {
      beginCapture("auto", handlers, transcribe);
    },

    startBargeIn(handlers, transcribe, onSpeech) {
      beginCapture("barge", handlers, transcribe, () => {
        cancelSpeech();
        onSpeech();
      });
    },

    finishListening() {
      const target = attempt;
      // Only a live capture can finish. A second click while the browser is
      // still asking for permission is ignored, and Stop owns cancellation.
      return target?.mode === "manual" ? finishClip(target) : false;
    },

    stopListening() {
      const target = attempt;
      if (target) abandonAttempt(target);
    },

    speak(turnId, handlers, synthesize) {
      if (!AudioCtor || !BlobCtor || !objectUrls || !turnId.trim()) {
        handlers.onError(SYNTHESIS_REASON);
        handlers.onEnd();
        return;
      }
      cancelSpeech();
      const controller = new AbortController();
      const current: SpeechAttempt = {
        controller,
        audio: null,
        url: null,
        release: () => {},
      };
      activeSpeech = current;

      /** One terminal report for this attempt: never twice, never when stale. */
      const finish = (outcome: "ended" | "failed") => {
        if (activeSpeech !== current) return;
        activeSpeech = null;
        releaseSpeech(current);
        if (outcome === "failed") handlers.onError(SYNTHESIS_FAILED);
        handlers.onEnd();
      };

      const start = async () => {
        let bytes: Uint8Array<ArrayBuffer>;
        try {
          bytes = await synthesize(turnId, controller.signal);
        } catch {
          // A fetch aborted by Stop, mute, clear or unmount is not an error.
          if (activeSpeech !== current || controller.signal.aborted) return;
          finish("failed");
          return;
        }
        if (activeSpeech !== current || controller.signal.aborted) return;
        try {
          if (
            bytes.byteLength === 0 ||
            bytes.byteLength > MAX_SPEECH_AUDIO_BYTES
          ) {
            finish("failed");
            return;
          }
          const url = objectUrls.createObjectURL?.(
            new BlobCtor([bytes], { type: "audio/mpeg" }),
          );
          if (!url) {
            finish("failed");
            return;
          }
          const audio = new AudioCtor(url);
          audio.onended = () => finish("ended");
          audio.onerror = () => finish("failed");
          current.audio = audio;
          current.url = url;
          current.release = () => {
            audio.onended = null;
            audio.onerror = null;
          };
          await audio.play();
        } catch {
          finish("failed");
        }
      };

      void start();
    },

    stopSpeaking() {
      cancelSpeech();
    },
  };
}
