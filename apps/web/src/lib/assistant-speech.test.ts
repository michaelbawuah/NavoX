import type { AssistantTurnView } from "@navox/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AssistantTurnLedger,
  transcribeAssistantSpeech,
} from "./assistant-client";
import {
  createAssistantTurnRunner,
  createVoiceSession,
  type VoiceSession,
} from "./assistant-controller";
import { createSpeechAdapter } from "./assistant-speech";
import { MAX_WAV_MILLISECONDS, parseAssistantWav } from "./assistant-wav";

interface FakeProcessor {
  args: number[];
  onaudioprocess: ((event: unknown) => void) | null;
  connect: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  emit(samples: Float32Array): void;
  emitChunk(samples: number[]): void;
}

interface FakeStream {
  getTracks(): { stop: ReturnType<typeof vi.fn> }[];
}

function tone(frames: number, amplitude = 0.4): Float32Array {
  const samples = new Float32Array(frames);
  for (let index = 0; index < frames; index += 1) {
    samples[index] = Math.sin((index / 40) * Math.PI) * amplitude;
  }
  return samples;
}

/**
 * A controlled Web Audio graph. Nothing here records or plays: the test emits
 * exact PCM frames so the wire bytes can be asserted byte for byte.
 */
function captureScope(sampleRate = 16_000) {
  const tracks: { stop: ReturnType<typeof vi.fn> }[] = [];
  const processors: FakeProcessor[] = [];
  const contexts: { close: ReturnType<typeof vi.fn> }[] = [];

  class FakeContext {
    sampleRate = sampleRate;
    destination = { id: "destination" };
    createMediaStreamSource() {
      return { connect: vi.fn(), disconnect: vi.fn() };
    }
    createGain() {
      return { gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() };
    }
    createScriptProcessor(...args: number[]) {
      const processor = {
        args,
        onaudioprocess: null as ((event: unknown) => void) | null,
        connect: vi.fn(),
        disconnect: vi.fn(),
        emit(samples: Float32Array) {
          this.onaudioprocess?.({
            inputBuffer: { getChannelData: () => samples },
          });
        },
        emitChunk(values: number[]) {
          this.emit(Float32Array.from(values));
        },
      } satisfies FakeProcessor;
      processors.push(processor);
      return processor;
    }
    close = vi.fn(async () => undefined);
    constructor() {
      contexts.push(this);
    }
  }

  const getUserMedia = vi.fn(async (): Promise<FakeStream> => {
    const track = { stop: vi.fn() };
    tracks.push(track);
    return { getTracks: () => tracks };
  });
  return {
    scope: {
      navigator: { mediaDevices: { getUserMedia } },
      AudioContext: FakeContext,
    },
    getUserMedia,
    tracks,
    processors,
    contexts,
  };
}

function synthesisScope() {
  const spoken: string[] = [];
  let utterance: {
    onstart: (() => void) | null;
    onend: (() => void) | null;
    onerror: (() => void) | null;
  } | null = null;
  class FakeUtterance {
    lang = "";
    onstart: (() => void) | null = null;
    onend: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(public text: string) {
      spoken.push(text);
      utterance = this as unknown as typeof utterance;
    }
  }
  const synth = { speak: vi.fn(), cancel: vi.fn() };
  return {
    scope: { speechSynthesis: synth, SpeechSynthesisUtterance: FakeUtterance },
    spoken,
    synth,
    current: () => utterance,
  };
}

function handlers() {
  return {
    onStart: vi.fn(),
    onTranscript: vi.fn(),
    onError: vi.fn(),
    onEnd: vi.fn(),
  };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("browser microphone capture adapter", () => {
  it("reports a typed fallback when the browser has no media APIs", () => {
    const adapter = createSpeechAdapter({});
    expect(adapter.captureSupported).toBe(false);
    expect(adapter.synthesisSupported).toBe(false);
    expect(adapter.captureReason).toMatch(/record from the microphone/i);
    expect(adapter.synthesisReason).toMatch(/cannot play spoken answers/i);
    const spies = handlers();
    adapter.startListening(spies, vi.fn());
    expect(spies.onError).toHaveBeenCalled();
    expect(spies.onEnd).toHaveBeenCalled();
  });

  it("reports no speech fallback on a browser that can play answers", () => {
    const { scope } = synthesisScope();
    const adapter = createSpeechAdapter(scope);
    expect(adapter.synthesisSupported).toBe(true);
    expect(adapter.synthesisReason).toBeNull();
    expect(adapter.captureSupported).toBe(false);
  });

  it("records one clip, encodes 16 kHz WAV bytes and delivers the question", async () => {
    const { scope, tracks, processors } = captureScope();
    const adapter = createSpeechAdapter(scope);
    expect(adapter.captureSupported).toBe(true);
    expect(adapter.captureReason).toBeNull();
    const spies = handlers();
    const transcribe = vi.fn(
      async (_wav: Uint8Array<ArrayBuffer>, _signal: AbortSignal) =>
        "What am I missing today?",
    );

    adapter.startListening(spies, transcribe);
    await tick();
    expect(processors).toHaveLength(1);
    processors[0]?.emit(tone(16_000));
    expect(adapter.finishListening()).toBe(true);
    await tick();

    expect(spies.onStart).toHaveBeenCalledTimes(1);
    expect(transcribe).toHaveBeenCalledTimes(1);
    const [wav, signal] = transcribe.mock.calls[0];
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);
    // The wire bytes are the container SPEC-005 accepts: 44-byte header plus
    // one second of mono 16-bit frames at 16 kHz.
    expect(parseAssistantWav(wav)).toEqual({
      channels: 1,
      sampleRate: 16_000,
      bitsPerSample: 16,
      dataBytes: 32_000,
      durationMilliseconds: 1_000,
    });
    expect(spies.onTranscript).toHaveBeenCalledWith("What am I missing today?");
    expect(spies.onError).not.toHaveBeenCalled();
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
    // The microphone and the graph are released before the upload starts.
    expect(processors[0]?.onaudioprocess).toBeNull();
    expect(processors[0]?.disconnect).toHaveBeenCalled();
    for (const track of tracks) expect(track.stop).toHaveBeenCalled();
  });

  it("resamples a 48 kHz device track onto the fixed 16 kHz wire", async () => {
    const { scope, processors } = captureScope(48_000);
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const transcribe = vi.fn(
      async (_wav: Uint8Array<ArrayBuffer>, _signal: AbortSignal) =>
        "Any updates?",
    );

    adapter.startListening(spies, transcribe);
    await tick();
    processors[0]?.emit(tone(48_000));
    adapter.finishListening();
    await tick();

    const [wav] = transcribe.mock.calls[0];
    expect(parseAssistantWav(wav)).toMatchObject({
      sampleRate: 16_000,
      dataBytes: 32_000,
      durationMilliseconds: 1_000,
    });
  });

  it("refuses a clip shorter than the SPEC-005 floor without uploading", async () => {
    const { scope, processors } = captureScope();
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const transcribe = vi.fn(async () => "too short");

    adapter.startListening(spies, transcribe);
    await tick();
    processors[0]?.emit(tone(1_600)); // 100 ms
    adapter.finishListening();
    await tick();

    expect(transcribe).not.toHaveBeenCalled();
    expect(spies.onError).toHaveBeenCalledWith(
      expect.stringMatching(/0\.2 and 30 seconds/i),
    );
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
  });

  it("aborts capture and an in-flight upload on Stop with no late turn", async () => {
    const { scope, processors, tracks } = captureScope();
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    let settle!: (text: string) => void;
    let observed!: AbortSignal;
    const transcribe = vi.fn((_wav: Uint8Array, signal: AbortSignal) => {
      observed = signal;
      return new Promise<string>((resolve) => {
        settle = resolve;
      });
    });

    adapter.startListening(spies, transcribe);
    await tick();
    processors[0]?.emit(tone(16_000));
    adapter.finishListening();
    await tick();
    expect(transcribe).toHaveBeenCalled();

    adapter.stopListening();
    expect(observed.aborted).toBe(true);
    for (const track of tracks) expect(track.stop).toHaveBeenCalled();

    // A late provider reply cannot become a turn once the operator stopped.
    settle("late words");
    await tick();
    expect(spies.onTranscript).not.toHaveBeenCalled();
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
  });

  it("reports blocked permission and never opens a graph", async () => {
    const { scope, getUserMedia, processors } = captureScope();
    getUserMedia.mockRejectedValueOnce({ name: "NotAllowedError" });
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const transcribe = vi.fn(async () => "never");

    adapter.startListening(spies, transcribe);
    await tick();

    expect(spies.onError).toHaveBeenCalledWith(
      expect.stringMatching(/microphone access was blocked/i),
    );
    expect(processors).toEqual([]);
    expect(transcribe).not.toHaveBeenCalled();
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
  });

  it("releases tracks that arrive after the operator already stopped", async () => {
    const { scope, getUserMedia } = captureScope();
    const track = { stop: vi.fn() };
    let settle!: (stream: FakeStream) => void;
    getUserMedia.mockImplementationOnce(
      () =>
        new Promise<FakeStream>((resolve) => {
          settle = resolve;
        }),
    );
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();

    adapter.startListening(spies, vi.fn());
    adapter.stopListening();
    settle({ getTracks: () => [track] });
    await tick();

    expect(track.stop).toHaveBeenCalled();
    expect(spies.onTranscript).not.toHaveBeenCalled();
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
  });

  it("surfaces an upload failure as the typed fallback it carries", async () => {
    const { scope, processors } = captureScope();
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const transcribe = vi.fn(async () => {
      throw new Error("Speech transcription is not available right now.");
    });

    adapter.startListening(spies, transcribe);
    await tick();
    processors[0]?.emit(tone(16_000));
    adapter.finishListening();
    await tick();

    expect(spies.onError).toHaveBeenCalledWith(
      "Speech transcription is not available right now.",
    );
    expect(spies.onTranscript).not.toHaveBeenCalled();
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
  });

  it("ignores a repeated finish while the clip is already being transcribed", async () => {
    const { scope, processors } = captureScope();
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const transcribe = vi.fn(async () => "Any updates?");

    adapter.startListening(spies, transcribe);
    await tick();
    processors[0]?.emit(tone(16_000));
    expect(adapter.finishListening()).toBe(true);
    expect(adapter.finishListening()).toBe(false);
    await tick();

    expect(transcribe).toHaveBeenCalledTimes(1);
    expect(spies.onTranscript).toHaveBeenCalledTimes(1);
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
  });

  it("calls getUserMedia on the receiver a brand check requires", async () => {
    const { scope, tracks, processors } = captureScope();
    const constraints: unknown[] = [];
    const mediaDevices = {
      async getUserMedia(this: unknown, request: unknown) {
        if (this !== mediaDevices) throw new TypeError("Illegal invocation");
        constraints.push(request);
        tracks.push({ stop: vi.fn() });
        return { getTracks: () => tracks };
      },
    };
    const adapter = createSpeechAdapter({
      ...scope,
      navigator: { mediaDevices },
    });
    expect(adapter.captureSupported).toBe(true);
    const spies = handlers();

    adapter.startListening(spies, vi.fn());
    await tick();

    expect(constraints).toEqual([{ audio: true }]);
    expect(processors).toHaveLength(1);
    expect(spies.onError).not.toHaveBeenCalled();
    adapter.stopListening();
  });

  it("closes the microphone at the 30-second frame ceiling", async () => {
    const { scope, processors, tracks } = captureScope();
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const transcribe = vi.fn(async () => "never");

    adapter.startListening(spies, transcribe);
    await tick();
    processors[0]?.emit(tone(16_000 * 30));

    expect(transcribe).not.toHaveBeenCalled();
    expect(spies.onError).toHaveBeenCalledWith(
      expect.stringMatching(/30 seconds/i),
    );
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
    expect(processors[0]?.onaudioprocess).toBeNull();
    for (const track of tracks) expect(track.stop).toHaveBeenCalled();
    // The session is already released: nothing can upload this clip later.
    expect(adapter.finishListening()).toBe(false);
    expect(transcribe).not.toHaveBeenCalled();
  });

  it("closes the microphone at the wall-clock ceiling when no frames arrive", async () => {
    vi.useFakeTimers();
    const { scope, tracks } = captureScope();
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const transcribe = vi.fn(async () => "never");

    adapter.startListening(spies, transcribe);
    await vi.advanceTimersByTimeAsync(0);
    // A stalled audio graph must not leave the microphone open.
    await vi.advanceTimersByTimeAsync(MAX_WAV_MILLISECONDS + 250);

    expect(transcribe).not.toHaveBeenCalled();
    expect(spies.onError).toHaveBeenCalledWith(
      expect.stringMatching(/30 seconds/i),
    );
    expect(spies.onEnd).toHaveBeenCalledTimes(1);
    expect(tracks).toHaveLength(1);
    for (const track of tracks) expect(track.stop).toHaveBeenCalled();
  });

  it("never writes audio or a transcript to storage or the console", async () => {
    const { scope, processors } = captureScope();
    const adapter = createSpeechAdapter(scope);
    const spies = handlers();
    const store = { setItem: vi.fn(), getItem: vi.fn() };
    vi.stubGlobal("localStorage", store);
    const consoleSpies = [
      vi.spyOn(console, "log"),
      vi.spyOn(console, "info"),
      vi.spyOn(console, "warn"),
      vi.spyOn(console, "error"),
      vi.spyOn(console, "debug"),
    ];

    adapter.startListening(spies, async () => "Where is the vendor recap?");
    await tick();
    processors[0]?.emit(tone(16_000));
    adapter.finishListening();
    await tick();

    for (const spy of consoleSpies) expect(spy).not.toHaveBeenCalled();
    expect(store.setItem).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("recorded-clip voice flow", () => {
  const sessionId = "66666666-6666-4666-8666-666666666666";

  function voiceTurn(text: string): AssistantTurnView {
    return {
      id: "88888888-8888-4888-8888-888888888888",
      sequence: 1,
      modality: "VOICE",
      state: "READY",
      question: text,
      plan: null,
      decision: null,
      presentation: {
        presentation: "VOICE",
        speak: false,
        speech_text: null,
        blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
      },
      action_refs: [],
      created_at: "2026-09-30T12:00:00.000Z",
    };
  }

  it("carries one recorded clip through the route into exactly one VOICE turn", async () => {
    const { scope, processors } = captureScope();
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify({ text: "What am I missing today?" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetcher);

    const submitted: { sessionId: string; input: Record<string, unknown> }[] =
      [];
    const appended: AssistantTurnView[] = [];
    const transcripts: string[] = [];
    const pendingToken = { current: 0 };
    let pending: Promise<void> | null = null;
    let voice!: VoiceSession;

    const runner = createAssistantTurnRunner({
      submit: async (id, input) => {
        submitted.push({ sessionId: id, input: { ...input } });
        return {
          session_id: id,
          turn: voiceTurn(input.text),
          replay: false,
        };
      },
      ledger: new AssistantTurnLedger(),
      timezone: () => "America/New_York",
      onTurn: (turn) => {
        appended.push(turn);
        if (turn.modality === "VOICE") voice.answerReady();
      },
      onNotice: () => {},
      onBusy: () => {},
      onSpeak: (text) => voice.speakAutomatic(text, pendingToken.current),
    });

    voice = createVoiceSession({
      adapter: createSpeechAdapter(scope),
      transcribe: (wav, signal) =>
        transcribeAssistantSpeech(sessionId, wav, signal),
      onState: () => {},
      onNotice: () => {},
      onTranscript: (request) => {
        transcripts.push(request.text);
        pendingToken.current = voice.beginTurn();
        voice.markSubmitted();
        pending = runner.run({
          sessionId,
          modality: request.modality,
          text: request.text,
          referents: request.referents,
        });
      },
    });

    voice.toggleListening(); // first click opens the microphone
    await tick();
    processors[0]?.emit(tone(16_000));
    voice.toggleListening(); // second click finishes and transcribes the clip
    await tick();
    await tick();
    await pending;

    expect(transcripts).toEqual(["What am I missing today?"]);
    expect(appended).toHaveLength(1);
    expect(appended[0]?.modality).toBe("VOICE");
    expect(submitted).toHaveLength(1);
    expect(submitted[0]?.sessionId).toBe(sessionId);
    // The turn carries the operator's own words and nothing else: no action,
    // approval, referent, provider or model authority.
    expect(Object.keys(submitted[0]?.input ?? {}).sort()).toEqual([
      "modality",
      "referents",
      "requestId",
      "text",
      "timezone",
    ]);
    expect(submitted[0]?.input.modality).toBe("VOICE");
    expect(submitted[0]?.input.text).toBe("What am I missing today?");
    expect(submitted[0]?.input.referents).toEqual([]);

    const [url, init] = fetcher.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      `/api/v1/assistant/sessions/${sessionId}/speech/transcribe`,
    );
    expect(init.headers).toEqual({ "content-type": "audio/wav" });
    expect(
      parseAssistantWav(init.body as Uint8Array<ArrayBuffer>),
    ).toMatchObject({
      sampleRate: 16_000,
      channels: 1,
      bitsPerSample: 16,
      durationMilliseconds: 1_000,
    });
  });
});

describe("browser speech playback", () => {
  it("speaks an answer and reports start and end", () => {
    const { scope, spoken, current } = synthesisScope();
    const adapter = createSpeechAdapter(scope);
    expect(adapter.synthesisSupported).toBe(true);
    const onStart = vi.fn();
    const onEnd = vi.fn();
    adapter.speak("1 item needs attention now.", {
      onStart,
      onEnd,
      onError: vi.fn(),
    });
    expect(spoken).toEqual(["1 item needs attention now."]);
    current()?.onstart?.();
    current()?.onend?.();
    expect(onStart).toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalled();
  });

  it("cancels speech when a new utterance or a recording takes the turn", () => {
    const { scope, synth } = synthesisScope();
    const adapter = createSpeechAdapter(scope);
    adapter.speak("first", { onEnd: vi.fn(), onError: vi.fn() });
    const afterSpeak = synth.cancel.mock.calls.length;
    adapter.stopSpeaking();
    expect(synth.cancel.mock.calls.length).toBeGreaterThan(afterSpeak);

    const capture = captureScope();
    const capturing = createSpeechAdapter({ ...capture.scope, ...scope });
    capturing.speak("second", { onEnd: vi.fn(), onError: vi.fn() });
    const afterSecond = synth.cancel.mock.calls.length;
    // Opening the microphone is an operator turn: playback is cancelled first.
    capturing.startListening(handlers(), vi.fn());
    expect(synth.cancel.mock.calls.length).toBeGreaterThan(afterSecond);
    capturing.stopListening();
  });

  it("drops a late utterance callback after speech was cancelled", () => {
    const { scope, current } = synthesisScope();
    const adapter = createSpeechAdapter(scope);
    const onEnd = vi.fn();
    const onError = vi.fn();
    adapter.speak("1 item needs attention now.", { onEnd, onError });
    const cancelled = current();
    adapter.stopSpeaking();
    // A browser that reports an end for a cancelled utterance must not move
    // the session out of its current state.
    cancelled?.onend?.();
    cancelled?.onerror?.();
    expect(onEnd).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });
});
