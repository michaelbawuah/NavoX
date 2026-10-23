import type {
  WakeWordAdapter,
  WakeWordEvent,
} from "@navox/assistant-runtime/voice";
import { describe, expect, it, vi } from "vitest";
import { VoxStandbyWake } from "./vox-local-wake";

const event: WakeWordEvent = {
  type: "wake",
  detected_at: "2026-10-23T12:00:00Z",
  device_id: "local-test",
  confidence: 0.95,
  request_text: "What were we discussing?",
};
async function settle() {
  for (let i = 0; i < 30; i++) await Promise.resolve();
}
function rig(supported = true) {
  let permitted = true;
  let active = false;
  const listeners: Array<(event: WakeWordEvent) => void> = [];
  const adapter: WakeWordAdapter = {
    id: "local-test",
    supported,
    get active() {
      return active;
    },
    start: vi.fn(async (listener) => {
      listeners.push(listener);
      active = true;
    }),
    stop: vi.fn(async () => {
      active = false;
    }),
  };
  const onWake = vi.fn();
  const onStatus = vi.fn();
  const onUnsafe = vi.fn();
  const wake = new VoxStandbyWake({
    adapter,
    mayListen: () => permitted,
    onWake,
    onStatus,
    onUnsafe,
  });
  return {
    wake,
    adapter,
    listeners,
    onWake,
    onStatus,
    onUnsafe,
    permit: (value: boolean) => {
      permitted = value;
    },
  };
}

describe("Vox local standby wake", () => {
  it("does no work until permitted standby, then forwards the whole following question once", async () => {
    const env = rig();
    expect(env.adapter.start).not.toHaveBeenCalled();
    env.wake.setEnabled(true);
    await settle();
    expect(env.onStatus).toHaveBeenLastCalledWith("listening");
    env.listeners[0](event);
    expect(env.adapter.active).toBe(false);
    await settle();
    expect(env.onWake).toHaveBeenCalledExactlyOnceWith(event);
    env.listeners[0](event);
    expect(env.onWake).toHaveBeenCalledTimes(1);
    await env.wake.close();
  });

  it("waits for local microphone cleanup before resuming and cancels a wake if ownership changes meanwhile", async () => {
    const env = rig();
    env.wake.setEnabled(true);
    await settle();
    let release!: () => void;
    vi.mocked(env.adapter.stop).mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    env.listeners[0](event);
    await settle();
    expect(env.onWake).not.toHaveBeenCalled();
    env.permit(false);
    release();
    await settle();
    expect(env.onWake).not.toHaveBeenCalled();
    await env.wake.close();
  });

  it("never starts an unsupported detector or falls back to remote recognition", async () => {
    const env = rig(false);
    expect(env.wake.supported).toBe(false);
    env.wake.setEnabled(true);
    await settle();
    expect(env.adapter.start).not.toHaveBeenCalled();
    expect(env.onWake).not.toHaveBeenCalled();
    await env.wake.close();
  });

  it("revokes wake synchronously on mute, owner loss, hide or hang-up and ignores late callbacks", async () => {
    const env = rig();
    env.wake.setEnabled(true);
    await settle();
    env.permit(false);
    env.wake.refresh();
    expect(env.adapter.active).toBe(false);
    env.listeners[0](event);
    expect(env.onWake).not.toHaveBeenCalled();
    env.permit(true);
    env.wake.setEnabled(true);
    await settle();
    expect(env.adapter.start).toHaveBeenCalledTimes(2);
    const closing = env.wake.close();
    expect(env.adapter.active).toBe(false);
    env.listeners[1](event);
    expect(env.onWake).not.toHaveBeenCalled();
    await closing;
  });

  it("serializes a new standby listener behind an aborted pending start", async () => {
    const env = rig();
    let release!: () => void;
    vi.mocked(env.adapter.start).mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    env.wake.setEnabled(true);
    await settle();
    env.wake.setEnabled(false);
    env.wake.setEnabled(true);
    await settle();
    expect(env.adapter.start).toHaveBeenCalledTimes(1);
    release();
    await settle();
    expect(env.adapter.start).toHaveBeenCalledTimes(2);
    expect(env.onStatus).toHaveBeenLastCalledWith("listening");
    await env.wake.close();
  });

  it("reports start failure honestly while leaving manual resume available", async () => {
    const env = rig();
    vi.mocked(env.adapter.start).mockRejectedValueOnce(
      new Error("Local language pack unavailable"),
    );
    env.wake.setEnabled(true);
    await settle();
    expect(env.wake.supported).toBe(false);
    expect(env.onStatus).toHaveBeenLastCalledWith("unavailable");
    expect(env.onWake).not.toHaveBeenCalled();
    expect(env.onUnsafe).not.toHaveBeenCalled();
    await env.wake.close();
  });

  it("does not claim listening after an engine stops and surfaces uncertain cleanup", async () => {
    const env = rig();
    env.wake.setEnabled(true);
    await settle();
    await env.adapter.stop();
    env.wake.refresh();
    await settle();
    expect(env.onStatus).toHaveBeenLastCalledWith("unavailable");
    await env.wake.close();
    const unsafe = rig();
    unsafe.wake.setEnabled(true);
    await settle();
    vi.mocked(unsafe.adapter.stop).mockRejectedValueOnce(
      new Error("device did not release"),
    );
    await expect(unsafe.wake.close()).rejects.toThrow("cleanup failed");
    expect(unsafe.onUnsafe).toHaveBeenCalledTimes(1);
  });
});
