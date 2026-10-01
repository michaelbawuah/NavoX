import { describe, expect, it, vi } from "vitest";
import { createSpeechAdapter } from "./assistant-speech";

interface RecognitionStub {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
  onresult: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onend: (() => void) | null;
}

function recognitionScope() {
  const instances: RecognitionStub[] = [];
  class FakeRecognition {
    lang = "";
    continuous = false;
    interimResults = false;
    start = vi.fn();
    stop = vi.fn(() => {
      this.onend?.();
    });
    abort = vi.fn(() => {
      this.onend?.();
    });
    onresult: ((event: unknown) => void) | null = null;
    onerror: ((event: unknown) => void) | null = null;
    onend: (() => void) | null = null;
    constructor() {
      instances.push(this as unknown as RecognitionStub);
    }
  }
  return { scope: { SpeechRecognition: FakeRecognition }, instances };
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
      utterance = this;
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

describe("browser speech adapter", () => {
  it("reports a typed fallback when the browser has no speech APIs", () => {
    const adapter = createSpeechAdapter({});
    expect(adapter.recognitionSupported).toBe(false);
    expect(adapter.synthesisSupported).toBe(false);
    expect(adapter.reason).toMatch(/cannot capture microphone input/i);
    expect(adapter.synthesisReason).toMatch(/cannot play spoken answers/i);
    const onError = vi.fn();
    const onEnd = vi.fn();
    adapter.startListening({ onTranscript: vi.fn(), onError, onEnd });
    expect(onError).toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalled();
  });

  it("reports no speech fallback on a browser that can play answers", () => {
    const { scope } = synthesisScope();
    const adapter = createSpeechAdapter(scope);
    expect(adapter.synthesisSupported).toBe(true);
    expect(adapter.synthesisReason).toBeNull();
  });

  it("delivers a final transcript once, on end", () => {
    const { scope, instances } = recognitionScope();
    const adapter = createSpeechAdapter(scope);
    expect(adapter.recognitionSupported).toBe(true);
    const onTranscript = vi.fn();
    adapter.startListening({ onTranscript, onError: vi.fn(), onEnd: vi.fn() });
    const instance = instances[0];
    expect(instance?.start).toHaveBeenCalled();
    instance?.onresult?.({
      results: [[{ transcript: "What am I missing today? " }]],
    });
    expect(onTranscript).not.toHaveBeenCalled();
    instance?.onend?.();
    expect(onTranscript).toHaveBeenCalledWith("What am I missing today? ");
  });

  it("never submits an empty or failed transcript", () => {
    const { scope, instances } = recognitionScope();
    const adapter = createSpeechAdapter(scope);
    const onTranscript = vi.fn();
    const onError = vi.fn();
    adapter.startListening({ onTranscript, onError, onEnd: vi.fn() });
    instances[0]?.onerror?.({ error: "not-allowed" });
    instances[0]?.onend?.();
    expect(onTranscript).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith(
      expect.stringMatching(/microphone access was blocked/i),
    );
  });

  it("cancels without submitting a buffered transcript", () => {
    const { scope, instances } = recognitionScope();
    const adapter = createSpeechAdapter(scope);
    const onTranscript = vi.fn();
    const onEnd = vi.fn();
    adapter.startListening({ onTranscript, onError: vi.fn(), onEnd });
    const instance = instances[0];
    // The engine already buffered a partial result before the operator stopped.
    instance?.onresult?.({
      results: [[{ transcript: "What am I missing today?" }]],
    });
    adapter.stopListening();
    expect(instance?.abort).toHaveBeenCalled();
    expect(instance?.stop).not.toHaveBeenCalled();
    expect(onTranscript).not.toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalled();
  });

  it("does not submit after an explicit cancel followed by a late result", () => {
    const { scope, instances } = recognitionScope();
    const adapter = createSpeechAdapter(scope);
    const onTranscript = vi.fn();
    adapter.startListening({ onTranscript, onError: vi.fn(), onEnd: vi.fn() });
    const instance = instances[0];
    adapter.stopListening();
    instance?.onresult?.({ results: [[{ transcript: "late words" }]] });
    instance?.onend?.();
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("speaks a voice answer and reports start and end", () => {
    const { scope, spoken, current } = synthesisScope();
    const adapter = createSpeechAdapter(scope);
    expect(adapter.synthesisSupported).toBe(true);
    const onStart = vi.fn();
    const onEnd = vi.fn();
    adapter.speak("2 items need attention now.", {
      onStart,
      onEnd,
      onError: vi.fn(),
    });
    expect(spoken).toEqual(["2 items need attention now."]);
    current()?.onstart?.();
    current()?.onend?.();
    expect(onStart).toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalled();
  });

  it("cancels speech when a new utterance interrupts", () => {
    const { scope, synth } = synthesisScope();
    const adapter = createSpeechAdapter(scope);
    adapter.speak("first", { onEnd: vi.fn(), onError: vi.fn() });
    adapter.stopSpeaking();
    expect(synth.cancel).toHaveBeenCalled();
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
