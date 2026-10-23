import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createPorcupineWakeWordAdapter,
  type PorcupineWakeConfig,
  type PorcupineWakeSDK,
  type PorcupineWakeWorker,
} from "./vox-porcupine-wake";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function rig() {
  const worker = {
    release: vi.fn<() => Promise<void>>().mockResolvedValue(),
    terminate: vi.fn<() => void>(),
  };
  let detection: (value: { label: string }) => void = () => {};
  let processError: (error: unknown) => void = () => {};
  const sdk = {
    PorcupineWorker: {
      create: vi
        .fn<PorcupineWakeSDK["PorcupineWorker"]["create"]>()
        .mockImplementation(
          async (_key, _keywords, callback, _model, options) => {
            detection = callback;
            processError = options.processErrorCallback;
            return worker;
          },
        ),
    },
    WebVoiceProcessor: {
      subscribe: vi.fn<() => Promise<void>>().mockResolvedValue(),
      unsubscribe: vi.fn<() => Promise<void>>().mockResolvedValue(),
    },
  };
  const config: PorcupineWakeConfig = {
    accessKey: "test-fixture-key",
    keywordPublicPath: "/models/hey-navox.ppn",
    modelPublicPath: "/models/porcupine.pv",
    label: "Hey NavoX",
  };
  const loadConfig = vi.fn(async () => config);
  const loadSdk = vi.fn(async () => sdk);
  const onError = vi.fn();
  const create = (supported = true) =>
    createPorcupineWakeWordAdapter({
      supported,
      loadConfig,
      loadSdk,
      onError,
      now: () => new Date("2026-10-23T12:00:00Z"),
      cleanupTimeoutMs: 50,
    });
  return {
    worker,
    sdk,
    config,
    loadConfig,
    loadSdk,
    onError,
    create,
    detection: () => detection,
    processError: () => processError,
  };
}

afterEach(() => vi.useRealTimers());

describe("optional Porcupine on-device wake adapter", () => {
  it("performs no work on construction and refuses unsupported browsers", async () => {
    const env = rig();
    const wake = env.create(false);
    expect(wake.supported).toBe(false);
    expect(wake.active).toBe(false);
    await expect(wake.start(vi.fn())).rejects.toThrow("unavailable");
    expect(env.loadConfig).not.toHaveBeenCalled();
    expect(env.loadSdk).not.toHaveBeenCalled();
    expect(env.sdk.WebVoiceProcessor.subscribe).not.toHaveBeenCalled();
  });

  it("uses official custom-model APIs and emits only the configured wake label", async () => {
    const env = rig();
    const wake = env.create();
    const heard = vi.fn();
    expect(env.loadConfig).not.toHaveBeenCalled();
    await wake.start(heard);
    expect(wake.active).toBe(true);
    expect(env.sdk.PorcupineWorker.create).toHaveBeenCalledWith(
      env.config.accessKey,
      [{ publicPath: env.config.keywordPublicPath, label: "Hey NavoX" }],
      expect.any(Function),
      { publicPath: env.config.modelPublicPath },
      { processErrorCallback: expect.any(Function) },
    );
    env.detection()({ label: "Another keyword" });
    expect(heard).not.toHaveBeenCalled();
    env.detection()({ label: "Hey NavoX" });
    expect(wake.active).toBe(false);
    expect(heard).toHaveBeenCalledWith({
      type: "wake",
      detected_at: "2026-10-23T12:00:00.000Z",
      device_id: "browser-local-porcupine",
      confidence: 1,
    });
    await wake.stop();
    expect(env.sdk.WebVoiceProcessor.unsubscribe).toHaveBeenCalled();
    expect(env.worker.release).toHaveBeenCalledTimes(1);
    expect(env.worker.terminate).toHaveBeenCalledTimes(1);
  });

  it.each(["config", "SDK"] as const)(
    "cancels a pending %s load before microphone or worker creation",
    async (stage) => {
      const env = rig();
      const gate = deferred<PorcupineWakeConfig | typeof env.sdk>();
      if (stage === "config")
        env.loadConfig.mockImplementationOnce(
          () => gate.promise as Promise<PorcupineWakeConfig>,
        );
      else
        env.loadSdk.mockImplementationOnce(
          () => gate.promise as Promise<typeof env.sdk>,
        );
      const wake = env.create();
      const start = wake.start(vi.fn());
      const rejected = expect(start).rejects.toMatchObject({
        name: "AbortError",
      });
      await vi.waitFor(() =>
        expect(
          stage === "config" ? env.loadConfig : env.loadSdk,
        ).toHaveBeenCalled(),
      );
      await wake.stop();
      await rejected;
      gate.resolve(stage === "config" ? env.config : env.sdk);
      await Promise.resolve();
      expect(env.sdk.PorcupineWorker.create).not.toHaveBeenCalled();
      expect(env.sdk.WebVoiceProcessor.subscribe).not.toHaveBeenCalled();
    },
  );

  it("releases a worker created after cancellation without subscribing it", async () => {
    const env = rig();
    const gate = deferred<PorcupineWakeWorker>();
    env.sdk.PorcupineWorker.create.mockReturnValueOnce(gate.promise);
    const wake = env.create();
    const start = wake.start(vi.fn());
    const rejected = expect(start).rejects.toMatchObject({
      name: "AbortError",
    });
    await vi.waitFor(() =>
      expect(env.sdk.PorcupineWorker.create).toHaveBeenCalled(),
    );
    await wake.stop();
    await rejected;
    gate.resolve(env.worker);
    await vi.waitFor(() =>
      expect(env.worker.terminate).toHaveBeenCalledTimes(1),
    );
    expect(env.sdk.WebVoiceProcessor.subscribe).not.toHaveBeenCalled();
    expect(env.worker.release).toHaveBeenCalledTimes(1);
  });

  it("revokes callbacks synchronously and unsubscribes again when a permission prompt resolves late", async () => {
    const env = rig();
    const gate = deferred<void>();
    env.sdk.WebVoiceProcessor.subscribe.mockReturnValueOnce(gate.promise);
    const wake = env.create();
    const heard = vi.fn();
    const start = wake.start(heard);
    const rejected = expect(start).rejects.toMatchObject({
      name: "AbortError",
    });
    await vi.waitFor(() =>
      expect(env.sdk.WebVoiceProcessor.subscribe).toHaveBeenCalled(),
    );
    await wake.stop();
    await rejected;
    env.detection()({ label: "Hey NavoX" });
    expect(heard).not.toHaveBeenCalled();
    gate.resolve();
    await vi.waitFor(() =>
      expect(env.sdk.WebVoiceProcessor.unsubscribe).toHaveBeenCalledTimes(2),
    );
    expect(wake.active).toBe(false);
    expect(env.worker.terminate).toHaveBeenCalledTimes(1);
  });

  it("cleans partial subscription failures and sanitizes vendor diagnostics", async () => {
    const env = rig();
    env.sdk.WebVoiceProcessor.subscribe.mockRejectedValueOnce(
      new Error("private fixture key"),
    );
    const wake = env.create();
    await expect(wake.start(vi.fn())).rejects.toThrow(
      "On-device wake listening could not start.",
    );
    expect(wake.active).toBe(false);
    expect(env.sdk.WebVoiceProcessor.unsubscribe).toHaveBeenCalled();
    expect(env.worker.release).toHaveBeenCalledTimes(1);
    expect(env.worker.terminate).toHaveBeenCalledTimes(1);
  });

  it("ignores stale worker detections after stop and after a new start", async () => {
    const env = rig();
    const wake = env.create();
    const oldListener = vi.fn();
    await wake.start(oldListener);
    const stale = env.detection();
    await wake.stop();
    const current = vi.fn();
    await wake.start(current);
    stale({ label: "Hey NavoX" });
    expect(oldListener).not.toHaveBeenCalled();
    expect(current).not.toHaveBeenCalled();
    expect(wake.active).toBe(true);
    await wake.stop();
  });

  it("bounds failed cleanup, still terminates the worker, and rejects reuse", async () => {
    vi.useFakeTimers();
    const env = rig();
    env.worker.release.mockImplementationOnce(() => new Promise(() => {}));
    const wake = env.create();
    await wake.start(vi.fn());
    const stopped = wake.stop();
    const rejected = expect(stopped).rejects.toThrow(
      "cleanup could not be confirmed",
    );
    expect(wake.active).toBe(false);
    await vi.advanceTimersByTimeAsync(50);
    await rejected;
    expect(env.worker.terminate).toHaveBeenCalledTimes(1);
    await expect(wake.start(vi.fn())).rejects.toThrow(
      "cleanup could not be confirmed",
    );
  });

  it("revokes and cleans up on local processing failure without exposing its body", async () => {
    const env = rig();
    const wake = env.create();
    await wake.start(vi.fn());
    env.processError()(new Error("private fixture key"));
    expect(wake.active).toBe(false);
    expect(env.onError).toHaveBeenCalledWith(
      "On-device wake listening could not start.",
    );
    await vi.waitFor(() =>
      expect(env.worker.terminate).toHaveBeenCalledTimes(1),
    );
  });
});
