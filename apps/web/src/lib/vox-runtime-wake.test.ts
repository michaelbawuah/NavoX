import type {
  WakeWordAdapter,
  WakeWordEvent,
} from "@navox/assistant-runtime/voice";
import { describe, expect, it, vi } from "vitest";
import type {
  PorcupineWakeConfig,
  PorcupineWakeSDK,
} from "./vox-porcupine-wake";
import { createRuntimeVoxWakeWordAdapter } from "./vox-runtime-wake";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function rig() {
  let nativeActive = false;
  let nativeListener: ((event: WakeWordEvent) => void) | null = null;
  const native: WakeWordAdapter = {
    id: "local-native",
    supported: true,
    get active() {
      return nativeActive;
    },
    start: vi.fn(async (listener) => {
      nativeListener = listener;
      nativeActive = true;
    }),
    stop: vi.fn(async () => {
      nativeActive = false;
    }),
  };
  const worker = {
    release: vi.fn(async () => {}),
    terminate: vi.fn(async () => {}),
  };
  let detection: ((event: { label: string }) => void) | null = null;
  const sdk: PorcupineWakeSDK = {
    PorcupineWorker: {
      create: vi.fn(async (_key, _keywords, callback) => {
        detection = callback;
        return worker;
      }),
    },
    WebVoiceProcessor: {
      subscribe: vi.fn(async () => {}),
      unsubscribe: vi.fn(async () => {}),
    },
  };
  const config: PorcupineWakeConfig = {
    accessKey: "fixture-only-browser-key",
    label: "Hey NavoX",
    keywordPublicPath: "/wake/hey-navox-v1.ppn",
    modelPublicPath: "/wake/porcupine-en-v4.pv",
  };
  const loadConfig = vi.fn<
    (signal: AbortSignal) => Promise<PorcupineWakeConfig | null>
  >(async () => config);
  const loadSdk = vi.fn(async () => sdk);
  const wake = createRuntimeVoxWakeWordAdapter(
    { loadConfig },
    {
      nativeFactory: () => native,
      porcupineSupported: true,
      loadSdk,
    },
  );
  return {
    wake,
    native,
    worker,
    sdk,
    config,
    loadConfig,
    loadSdk,
    detect: () => detection?.({ label: "Hey NavoX" }),
    nativeDetect: () =>
      nativeListener?.({
        type: "wake",
        confidence: 1,
        detected_at: "2026-10-23T12:00:00Z",
        device_id: "fixture-local",
      }),
  };
}

describe("connection-bound runtime wake selection", () => {
  it("does no configuration, SDK or microphone work at construction", () => {
    const env = rig();
    expect(env.loadConfig).not.toHaveBeenCalled();
    expect(env.loadSdk).not.toHaveBeenCalled();
    expect(env.native.start).not.toHaveBeenCalled();
    expect(env.wake.active).toBe(false);
  });

  it("loads licensed configuration lazily and forwards actual custom wake detection", async () => {
    const env = rig();
    const heard = vi.fn();
    await env.wake.start(heard);
    expect(env.loadConfig.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
    expect(env.loadSdk).toHaveBeenCalledTimes(1);
    expect(env.native.start).not.toHaveBeenCalled();
    expect(env.wake.active).toBe(true);
    env.detect();
    expect(heard).toHaveBeenCalledTimes(1);
    await env.wake.stop();
    expect(env.worker.terminate).toHaveBeenCalledTimes(1);
    env.detect();
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("retains the supported local native fallback when licensed configuration is disabled", async () => {
    const env = rig();
    env.loadConfig.mockResolvedValue(null);
    const heard = vi.fn();
    await env.wake.start(heard);
    expect(env.native.start).toHaveBeenCalledTimes(1);
    expect(env.loadSdk).not.toHaveBeenCalled();
    env.nativeDetect();
    expect(heard).toHaveBeenCalledTimes(1);
    await env.wake.stop();
    env.nativeDetect();
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("aborts a pending private configuration read and ignores its late result", async () => {
    const env = rig();
    const pending = deferred<PorcupineWakeConfig>();
    env.loadConfig.mockReturnValue(pending.promise);
    const starting = env.wake.start(vi.fn());
    const rejected = expect(starting).rejects.toMatchObject({
      name: "AbortError",
    });
    await vi.waitFor(() => expect(env.loadConfig).toHaveBeenCalledTimes(1));
    const signal = env.loadConfig.mock.calls[0]?.[0];
    await env.wake.stop();
    expect(signal?.aborted).toBe(true);
    await rejected;
    pending.resolve(env.config);
    await Promise.resolve();
    expect(env.loadSdk).not.toHaveBeenCalled();
    expect(env.native.start).not.toHaveBeenCalled();
  });

  it("does not downgrade an authorization or malformed-configuration failure to local listening", async () => {
    const env = rig();
    env.loadConfig.mockRejectedValue(new Error("private key response body"));
    await expect(env.wake.start(vi.fn())).rejects.toThrow(
      "Local wake listening could not start.",
    );
    expect(env.native.start).not.toHaveBeenCalled();
    expect(env.loadSdk).not.toHaveBeenCalled();
    expect(env.wake.active).toBe(false);
  });

  it("waits for old microphone cleanup before reading config for another start", async () => {
    const env = rig();
    env.loadConfig.mockResolvedValue(null);
    await env.wake.start(vi.fn());
    const pending = deferred<void>();
    vi.mocked(env.native.stop).mockReturnValueOnce(pending.promise);
    const stopping = env.wake.stop();
    const starting = env.wake.start(vi.fn());
    await Promise.resolve();
    expect(env.loadConfig).toHaveBeenCalledTimes(1);
    pending.resolve();
    await stopping;
    await starting;
    expect(env.loadConfig).toHaveBeenCalledTimes(2);
    await env.wake.stop();
  });

  it("fails closed after cleanup cannot be confirmed", async () => {
    const env = rig();
    env.loadConfig.mockResolvedValue(null);
    await env.wake.start(vi.fn());
    vi.mocked(env.native.stop).mockRejectedValueOnce(
      new Error("private native diagnostic"),
    );
    await expect(env.wake.stop()).rejects.toThrow(
      "Local wake cleanup could not be confirmed.",
    );
    await expect(env.wake.start(vi.fn())).rejects.toThrow("unavailable");
    expect(env.loadConfig).toHaveBeenCalledTimes(1);
  });
});
