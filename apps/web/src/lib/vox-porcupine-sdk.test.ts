import { describe, expect, it, vi } from "vitest";
import { loadPorcupineWakeSdk } from "./vox-porcupine-wake";

const modules = vi.hoisted(() => ({
  loaded: vi.fn(),
  worker: { release: vi.fn(), terminate: vi.fn() },
  create: vi.fn(),
  subscribe: vi.fn(async () => {}),
  unsubscribe: vi.fn(async () => {}),
}));
vi.mock("@picovoice/porcupine-web/dist/esm/index.js", () => {
  modules.loaded("porcupine");
  return { PorcupineWorker: { create: modules.create } };
});
vi.mock("@picovoice/web-voice-processor/dist/esm/index.js", () => {
  modules.loaded("voice");
  return {
    WebVoiceProcessor: {
      subscribe: modules.subscribe,
      unsubscribe: modules.unsubscribe,
    },
  };
});

describe("official Porcupine SDK bridge", () => {
  it("imports only when requested and reuses constructors without configuring an engine", async () => {
    expect(modules.loaded).not.toHaveBeenCalled();
    const first = loadPorcupineWakeSdk();
    expect(loadPorcupineWakeSdk()).toBe(first);
    const sdk = await first;
    expect(modules.loaded).toHaveBeenCalledTimes(2);
    expect(modules.create).not.toHaveBeenCalled();
    modules.create.mockResolvedValue(modules.worker);
    const callback = vi.fn();
    const options = { processErrorCallback: vi.fn() };
    const worker = await sdk.PorcupineWorker.create(
      "fixture-key",
      [{ publicPath: "/wake/hey-navox-v1.ppn", label: "Hey NavoX" }],
      callback,
      { publicPath: "/wake/porcupine-en-v4.pv" },
      options,
    );
    expect(modules.create).toHaveBeenCalledWith(
      "fixture-key",
      expect.any(Array),
      callback,
      { publicPath: "/wake/porcupine-en-v4.pv" },
      options,
    );
    await sdk.WebVoiceProcessor.subscribe(worker);
    await sdk.WebVoiceProcessor.unsubscribe(worker);
    expect(modules.subscribe).toHaveBeenCalledWith(modules.worker);
    expect(modules.unsubscribe).toHaveBeenCalledWith(modules.worker);
  });
});
