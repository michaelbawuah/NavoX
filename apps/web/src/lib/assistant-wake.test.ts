import type { WakeWordEvent } from "@navox/assistant-runtime/voice";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createBrowserWakeWordAdapter,
  resolveWakePhrase,
} from "./assistant-wake";

class FakeRecognition {
  static status = "available";
  static installResult = true;
  static installGate: Promise<boolean> | null = null;
  static instances: FakeRecognition[] = [];
  static available = vi.fn(async () => FakeRecognition.status);
  static install = vi.fn(async () => {
    const result = FakeRecognition.installGate
      ? await FakeRecognition.installGate
      : FakeRecognition.installResult;
    if (result) FakeRecognition.status = "available";
    return result;
  });
  lang = "";
  continuous = false;
  interimResults = true;
  maxAlternatives = 0;
  processLocally = false;
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  abort = vi.fn();
  constructor() {
    FakeRecognition.instances.push(this);
  }
  result(transcript: string, confidence = 0.9, isFinal = true) {
    this.onresult?.({
      resultIndex: 0,
      results: [{ isFinal, length: 1, 0: { transcript, confidence } }],
    });
  }
}

function adapter(onError = vi.fn()) {
  return createBrowserWakeWordAdapter({
    scope: { SpeechRecognition: FakeRecognition as never },
    onError,
    now: () => new Date("2026-10-01T00:00:00Z"),
  });
}

function recognition(index = 0): FakeRecognition {
  const instance = FakeRecognition.instances[index];
  if (!instance) throw new Error("Expected a local recognition instance.");
  return instance;
}

beforeEach(() => {
  FakeRecognition.status = "available";
  FakeRecognition.installResult = true;
  FakeRecognition.installGate = null;
  FakeRecognition.instances = [];
  FakeRecognition.available.mockClear();
  FakeRecognition.install.mockClear();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("local Hey NavoX adapter", () => {
  it("fails closed without on-device availability controls", async () => {
    const wake = createBrowserWakeWordAdapter({ scope: {} });
    expect(wake.supported).toBe(false);
    await expect(wake.start(vi.fn())).rejects.toThrow("unavailable");
    expect(wake.active).toBe(false);
  });

  it("only accepts a direct final wake phrase and preserves its suffix", async () => {
    const wake = adapter();
    const heard: WakeWordEvent[] = [];
    await wake.start((event) => heard.push(event));
    const instance = recognition();
    expect(instance.processLocally).toBe(true);
    expect(instance.continuous).toBe(true);
    expect(instance.interimResults).toBe(false);
    expect(FakeRecognition.available).toHaveBeenCalledWith({
      langs: ["en-US"],
      processLocally: true,
    });
    instance.result("I said Hey NavoX", 0.99);
    instance.result("Hey Navarro, what time is it?", 0.99);
    instance.result("Hey NavoX, what am I missing today?", 0.99, false);
    instance.result("Hey NavoX, what am I missing today?", 0.4);
    expect(heard).toHaveLength(0);
    instance.result("Hey, NavoX, what am I missing today?", 0.92);
    expect(heard).toEqual([
      {
        type: "wake",
        detected_at: "2026-10-01T00:00:00.000Z",
        device_id: "browser-local",
        confidence: 0.92,
        request_text: "what am I missing today?",
      },
    ]);
    expect(wake.active).toBe(false);
    expect(instance.abort).toHaveBeenCalledOnce();
    instance.result("Hey NavoX", 0.99);
    expect(heard).toHaveLength(1);
  });

  it("installs only after explicit start and confirms the local pack", async () => {
    FakeRecognition.status = "downloadable";
    const wake = adapter();
    expect(FakeRecognition.install).not.toHaveBeenCalled();
    await wake.start(vi.fn());
    expect(FakeRecognition.install).toHaveBeenCalledOnce();
    expect(FakeRecognition.available).toHaveBeenCalledTimes(2);
    expect(wake.active).toBe(true);
    await wake.stop();
  });

  it("refuses a failed pack install without opening a microphone", async () => {
    FakeRecognition.status = "downloadable";
    FakeRecognition.installResult = false;
    const wake = adapter();
    await expect(wake.start(vi.fn())).rejects.toThrow("could not be installed");
    expect(FakeRecognition.instances).toHaveLength(0);
    expect(wake.active).toBe(false);
  });

  it("drops a late pack install after Hands-Free was disabled", async () => {
    FakeRecognition.status = "downloadable";
    let finish!: (value: boolean) => void;
    FakeRecognition.installGate = new Promise((resolve) => {
      finish = resolve;
    });
    const wake = adapter();
    const starting = wake.start(vi.fn());
    await vi.waitFor(() =>
      expect(FakeRecognition.install).toHaveBeenCalledOnce(),
    );
    await wake.stop();
    finish(true);
    await starting;
    expect(FakeRecognition.instances).toHaveLength(0);
    expect(wake.active).toBe(false);
  });

  it("restarts after a quiet browser end and stops on an engine error", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const wake = adapter(onError);
    await wake.start(vi.fn());
    const instance = recognition();
    instance.onend?.();
    await vi.advanceTimersByTimeAsync(250);
    expect(instance.start).toHaveBeenCalledTimes(2);
    instance.onerror?.({ error: "network" });
    expect(onError).toHaveBeenCalledOnce();
    expect(wake.active).toBe(false);
    instance.onend?.();
    await vi.advanceTimersByTimeAsync(250);
    expect(instance.start).toHaveBeenCalledTimes(2);
  });

  it("fails closed when the browser repeatedly ends recognition immediately", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const wake = adapter(onError);
    await wake.start(vi.fn());
    const instance = recognition();
    instance.onend?.();
    await vi.advanceTimersByTimeAsync(250);
    instance.onend?.();
    await vi.advanceTimersByTimeAsync(250);
    instance.onend?.();
    expect(onError).toHaveBeenCalledOnce();
    expect(wake.active).toBe(false);
  });

  it("keeps a stopped or restarted listener from receiving stale events", async () => {
    const wake = adapter();
    const first = vi.fn();
    const second = vi.fn();
    await wake.start(first);
    const old = recognition();
    const stale = old.onresult;
    await wake.stop();
    await wake.start(second);
    stale?.({
      resultIndex: 0,
      results: [
        {
          isFinal: true,
          length: 1,
          0: { transcript: "Hey NavoX", confidence: 1 },
        },
      ],
    });
    expect(first).not.toHaveBeenCalled();
    expect(second).not.toHaveBeenCalled();
    recognition(1).result("Hey NavoX", 1);
    expect(second).toHaveBeenCalledOnce();
  });
});

describe("wake phrase boundary", () => {
  it("rejects reports, near words and low-confidence inputs", () => {
    expect(resolveWakePhrase("I said Hey NavoX", 1)).toBeNull();
    expect(resolveWakePhrase("Hey NavoXbox", 1)).toBeNull();
    expect(resolveWakePhrase("Hey NavoX", Number.NaN)).toBeNull();
    expect(resolveWakePhrase("Hey NavoX", 0.54)).toBeNull();
  });
  it("preserves oversized same-utterance text for explicit submission rejection", () => {
    const question = "a".repeat(600);
    const found = resolveWakePhrase(`Hey NavoX, ${question}`, 0.9);
    expect(found?.request_text).toBe(question);
  });
});
