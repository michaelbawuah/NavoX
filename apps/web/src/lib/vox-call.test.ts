import { describe, expect, it, vi } from "vitest";
import {
  VOX_END_CALL_TIMEOUT_MS,
  VOX_STANDBY_AFTER_MS,
  VoxCallController,
  type VoxConnection,
  type VoxOwner,
  type VoxTransport,
  type VoxTransportRequest,
} from "./vox-call";

const owner: VoxOwner = {
  userId: "10000000-0000-4000-8000-000000000001",
  workspaceId: "20000000-0000-4000-8000-000000000001",
};
const anotherOwner: VoxOwner = {
  ...owner,
  userId: "10000000-0000-4000-8000-000000000002",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function connection(request: VoxTransportRequest) {
  return {
    owner: { ...request.owner },
    callId: request.callId,
    setMicrophoneEnabled: vi.fn<(enabled: boolean) => void>(),
    setSpeakerEnabled: vi.fn<(enabled: boolean) => void>(),
    setWakeEnabled: vi.fn<(enabled: boolean) => void>(),
    stopPlayback: vi.fn(),
    close: vi.fn<() => void | Promise<void>>(),
  };
}

function rig(
  options: { available?: boolean; localWakeAvailable?: boolean } = {},
) {
  let now = 0;
  let serial = 0;
  const requests: VoxTransportRequest[] = [];
  const connections: ReturnType<typeof connection>[] = [];
  const transport = {
    available: options.available ?? true,
    localWakeAvailable: options.localWakeAvailable ?? true,
    connect: vi.fn(
      async (request: VoxTransportRequest): Promise<VoxConnection> => {
        requests.push(request);
        const joined = connection(request);
        connections.push(joined);
        return joined;
      },
    ),
  };
  const controller = new VoxCallController({
    owner,
    transport,
    newCallId: () =>
      `30000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
    now: () => now,
  });
  return {
    controller,
    transport,
    requests,
    connections,
    advance(milliseconds: number) {
      now += milliseconds;
      controller.tick();
    },
    setNow(value: number) {
      now = value;
      controller.tick();
    },
  };
}

describe("Vox terminal transport cleanup", () => {
  it("revokes gates and invokes the old scoped end hook once before a pending join settles", async () => {
    const pending = deferred<VoxConnection>();
    const requests: VoxTransportRequest[] = [];
    const transport: VoxTransport = {
      available: true,
      localWakeAvailable: false,
      connect: vi.fn(async (request) => {
        requests.push(request);
        return pending.promise;
      }),
      endCall: vi.fn((scope, id) => {
        expect(scope).toEqual(owner);
        expect(id).toBe("30000000-0000-4000-8000-000000000001");
        expect(requests[0].signal.aborted).toBe(true);
        expect(requests[0].mayTransmitAudio()).toBe(false);
        expect(requests[0].mayPlayAudio()).toBe(false);
        expect(call.current().owner).toEqual(anotherOwner);
      }),
    };
    const call = new VoxCallController({
      owner,
      transport,
      newCallId: () => "30000000-0000-4000-8000-000000000001",
    });
    const starting = call.start();
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    const changed = call.bindOwner(anotherOwner);
    expect(transport.endCall).toHaveBeenCalledTimes(1);
    const hangingUp = call.hangUp();
    const disposed = call.dispose();
    expect(transport.endCall).toHaveBeenCalledTimes(1);
    const late = connection(requests[0]);
    pending.resolve(late);
    await Promise.all([starting, changed, hangingUp, disposed]);
    expect(late.close).toHaveBeenCalledTimes(1);
    expect(transport.endCall).toHaveBeenCalledTimes(1);
  });

  it("registers server cleanup before reentrant starts and closes only attempts on reconnect", async () => {
    const current = rig();
    const end = deferred<void>();
    let reentrant: Promise<void> | undefined;
    const transport: VoxTransport = {
      ...current.transport,
      endCall: vi.fn(() => {
        reentrant = call.start();
        return end.promise;
      }),
    };
    let serial = 0;
    const call = new VoxCallController({
      owner,
      transport,
      newCallId: () =>
        `30000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
    });
    await call.start();
    await call.reconnect();
    expect(transport.endCall).not.toHaveBeenCalled();
    const hangingUp = call.hangUp();
    expect(current.transport.connect).toHaveBeenCalledTimes(2);
    end.resolve();
    await hangingUp;
    await reentrant;
    expect(current.transport.connect).toHaveBeenCalledTimes(3);
    expect(call.current().phase).toBe("active");
    await call.dispose();
  });

  it("bounds a hung terminal hook and blocks replacement after unsafe server cleanup", async () => {
    vi.useFakeTimers();
    try {
      const end = deferred<void>();
      const transport: VoxTransport = {
        available: true,
        localWakeAvailable: false,
        connect: vi.fn(async (request) => connection(request)),
        endCall: vi.fn(() => end.promise),
      };
      let serial = 0;
      const call = new VoxCallController({
        owner,
        transport,
        newCallId: () =>
          `30000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
      });
      await call.start();
      const hangingUp = call.hangUp();
      let finished = false;
      void hangingUp.then(() => {
        finished = true;
      });
      await vi.advanceTimersByTimeAsync(VOX_END_CALL_TIMEOUT_MS - 1);
      expect(finished).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await hangingUp;
      expect(call.current().endReason).toBe("cleanup-incomplete");
      await call.bindOwner(anotherOwner);
      end.resolve();
      await Promise.resolve();
      await call.start();
      expect(transport.connect).toHaveBeenCalledTimes(1);
      expect(transport.endCall).toHaveBeenCalledTimes(1);
      expect(call.current()).toMatchObject({
        phase: "ended",
        owner: anotherOwner,
        callId: null,
        endReason: "cleanup-incomplete",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("contains a throwing terminal hook without reviving the old call", async () => {
    const current = rig();
    const transport: VoxTransport = {
      ...current.transport,
      endCall: vi.fn(() => {
        throw new Error("private server response");
      }),
    };
    const call = new VoxCallController({
      owner,
      transport,
      newCallId: () => "30000000-0000-4000-8000-000000000001",
    });
    await call.start();
    await expect(call.hangUp()).resolves.toBeUndefined();
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(JSON.stringify(call.current())).not.toContain("private");
    await call.dispose();
    expect(transport.endCall).toHaveBeenCalledTimes(1);
  });

  it("bounds stalled physical release and never recovers from its late success", async () => {
    vi.useFakeTimers();
    try {
      const current = rig();
      await current.controller.start();
      const request = current.requests[0];
      const close = deferred<void>();
      current.connections[0].close.mockReturnValue(close.promise);
      const hangingUp = current.controller.hangUp();
      expect(request.signal.aborted).toBe(true);
      expect(request.mayTransmitAudio()).toBe(false);
      expect(request.mayPlayAudio()).toBe(false);
      expect(
        current.connections[0].setMicrophoneEnabled,
      ).toHaveBeenLastCalledWith(false);
      expect(current.connections[0].setSpeakerEnabled).toHaveBeenLastCalledWith(
        false,
      );
      expect(current.connections[0].setWakeEnabled).toHaveBeenLastCalledWith(
        false,
      );
      expect(current.connections[0].stopPlayback).toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(VOX_END_CALL_TIMEOUT_MS);
      await hangingUp;
      expect(current.controller.current().endReason).toBe("cleanup-incomplete");
      await current.controller.bindOwner(anotherOwner);
      close.resolve();
      await Promise.resolve();
      await current.controller.start();
      expect(current.transport.connect).toHaveBeenCalledTimes(1);
      expect(current.controller.current()).toMatchObject({
        phase: "ended",
        owner: anotherOwner,
        callId: null,
        endReason: "cleanup-incomplete",
      });
      expect(request.mayTransmitAudio()).toBe(false);
      expect(current.connections[0].close).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Vox recoverable browser playback", () => {
  it("keeps blocked speaker playback separate from microphone and supports a user-gesture retry", async () => {
    const current = rig();
    await current.controller.start();
    current.controller.setMicrophoneMuted(true);
    const request = current.requests[0];
    request.onEvent({ type: "activity", activity: "speaking" });
    request.onEvent({ type: "playback-blocked" });
    request.onEvent({ type: "activity", activity: "speaking" });
    expect(current.controller.current()).toMatchObject({
      phase: "active",
      speakerBlocked: true,
      activity: "quiet",
      microphoneMuted: true,
      speakerEnabled: true,
    });
    current.controller.setSpeakerEnabled(true);
    expect(current.connections[0].setSpeakerEnabled).toHaveBeenLastCalledWith(
      true,
    );
    expect(
      current.connections[0].setMicrophoneEnabled,
    ).toHaveBeenLastCalledWith(false);
    expect(request.mayTransmitAudio()).toBe(false);
    request.onEvent({ type: "playback-ready" });
    expect(current.controller.current().speakerBlocked).toBe(false);
    await current.controller.hangUp();
    expect(current.controller.current().speakerBlocked).toBe(false);
  });

  it("ignores blocked/ready events from stale epochs and suspended authority", async () => {
    const current = rig();
    await current.controller.start();
    const old = current.requests[0];
    await current.controller.reconnect();
    old.onEvent({ type: "playback-blocked" });
    expect(current.controller.current().speakerBlocked).toBe(false);
    const request = current.requests[1];
    request.onEvent({ type: "playback-blocked" });
    current.controller.suspendForSessionCheck();
    request.onEvent({ type: "playback-ready" });
    expect(current.controller.current().speakerBlocked).toBe(true);
    await current.controller.bindOwner(owner);
    request.onEvent({ type: "playback-ready" });
    expect(current.controller.current().speakerBlocked).toBe(false);
    await current.controller.hangUp();
    request.onEvent({ type: "playback-blocked" });
    expect(current.controller.current().speakerBlocked).toBe(false);
  });
});

describe("Vox transport-independent call ownership", () => {
  it("opens nothing on construction, subscription, or a timer tick", () => {
    const current = rig();
    const listener = vi.fn();
    const unsubscribe = current.controller.subscribe(listener);
    current.advance(10_000);
    expect(current.transport.connect).not.toHaveBeenCalled();
    expect(current.controller.current()).toMatchObject({
      phase: "idle",
      owner,
      callId: null,
      elapsedMs: 0,
    });
    unsubscribe();
    expect(listener).not.toHaveBeenCalled();
  });

  it("rejects missing scope IDs and returns independent state copies", async () => {
    const current = rig();
    const snapshot = current.controller.current();
    if (snapshot.owner) snapshot.owner.userId = anotherOwner.userId;
    snapshot.phase = "active";
    expect(current.controller.current()).toMatchObject({
      owner,
      phase: "idle",
    });
    await expect(
      current.controller.bindOwner({ ...owner, workspaceId: "" }),
    ).rejects.toThrow("verified account and workspace");
    expect(current.controller.current().owner).toBeNull();
    await current.controller.bindOwner(null);
    await current.controller.start();
    expect(current.transport.connect).not.toHaveBeenCalled();
  });

  it("reports unavailable transport honestly and never invents an active call", async () => {
    const current = rig({ available: false });
    await current.controller.start();
    expect(current.controller.current()).toMatchObject({
      phase: "unavailable",
      callId: null,
      startedAt: null,
      endReason: "transport-unavailable",
    });
    expect(current.transport.connect).not.toHaveBeenCalled();
  });

  it("waits for an actual join and refuses duplicate starts", async () => {
    const current = rig();
    const pending = deferred<VoxConnection>();
    current.transport.connect.mockImplementation(async (request) => {
      current.requests.push(request);
      return pending.promise;
    });
    const started = current.controller.start();
    await vi.waitFor(() => expect(current.requests).toHaveLength(1));
    const request = current.requests[0];
    expect(Object.isFrozen(request.owner)).toBe(true);
    expect(request.owner).toEqual(owner);
    expect(current.controller.current().phase).toBe("connecting");
    expect(request.mayTransmitAudio()).toBe(false);
    expect(request.mayPlayAudio()).toBe(false);
    request.onEvent({ type: "disconnected" });
    request.onEvent({ type: "activity", activity: "speaking" });
    await current.controller.start();
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    pending.resolve(connection(request));
    await started;
    expect(current.controller.current()).toMatchObject({
      phase: "active",
      startedAt: 0,
      activity: "quiet",
    });
    expect(request.mayTransmitAudio()).toBe(true);
    expect(request.mayPlayAudio()).toBe(true);
    expect(request.mayListenForWake()).toBe(false);
  });

  it("keeps mic, speaker, captions and minimize independent", async () => {
    const current = rig();
    await current.controller.start();
    const request = current.requests[0];
    const joined = current.connections[0];
    current.controller.setMicrophoneMuted(true);
    expect(request.mayTransmitAudio()).toBe(false);
    expect(request.mayPlayAudio()).toBe(true);
    expect(joined.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);
    current.controller.setMicrophoneMuted(false);
    current.controller.setSpeakerEnabled(false);
    expect(request.mayTransmitAudio()).toBe(true);
    expect(request.mayPlayAudio()).toBe(false);
    expect(joined.stopPlayback).toHaveBeenCalledTimes(1);
    current.controller.setCaptionsEnabled(false);
    current.controller.setMinimized(true);
    expect(current.controller.current()).toMatchObject({
      phase: "active",
      microphoneMuted: false,
      speakerEnabled: false,
      captionsEnabled: false,
      minimized: true,
    });
    expect(joined.close).not.toHaveBeenCalled();
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    request.onEvent({ type: "activity", activity: "thinking" });
    current.controller.setMinimized(false);
    expect(current.controller.current().activity).toBe("thinking");
    expect(current.controller.current().callId).toBe(request.callId);
  });

  it("honors controls changed before a pending join completes", async () => {
    const current = rig();
    const pending = deferred<VoxConnection>();
    current.transport.connect.mockImplementation(async (request) => {
      current.requests.push(request);
      return pending.promise;
    });
    const started = current.controller.start();
    await vi.waitFor(() => expect(current.requests).toHaveLength(1));
    current.controller.setMicrophoneMuted(true);
    current.controller.setSpeakerEnabled(false);
    current.controller.setCaptionsEnabled(false);
    current.controller.setMinimized(true);
    const joined = connection(current.requests[0]);
    pending.resolve(joined);
    await started;
    expect(joined.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);
    expect(joined.setSpeakerEnabled).toHaveBeenLastCalledWith(false);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(current.controller.current().minimized).toBe(true);
    expect(current.controller.current().captionsEnabled).toBe(false);
  });
});

describe("Vox silence and standby", () => {
  it("keeps short pauses open, gates all standby audio and resumes the same call on tap", async () => {
    const current = rig({ localWakeAvailable: false });
    await current.controller.start();
    const request = current.requests[0];
    current.advance(20_000);
    expect(current.controller.current().phase).toBe("active");
    current.advance(VOX_STANDBY_AFTER_MS - 20_001);
    expect(current.controller.current().phase).toBe("active");
    current.advance(1);
    expect(current.controller.current().phase).toBe("standby");
    expect(request.mayTransmitAudio()).toBe(false);
    expect(request.mayPlayAudio()).toBe(false);
    expect(request.mayListenForWake()).toBe(false);
    expect(
      current.connections[0].setMicrophoneEnabled,
    ).toHaveBeenLastCalledWith(false);
    expect(current.connections[0].setWakeEnabled).toHaveBeenLastCalledWith(
      false,
    );
    request.onWake();
    expect(current.controller.current().phase).toBe("standby");
    current.controller.resume();
    expect(current.controller.current()).toMatchObject({
      phase: "active",
      callId: request.callId,
    });
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    expect(request.mayTransmitAudio()).toBe(true);
  });

  it("accepts local wake only in an unmuted standby call", async () => {
    const current = rig();
    await current.controller.start();
    const request = current.requests[0];
    request.onWake();
    expect(current.controller.current().phase).toBe("active");
    current.advance(VOX_STANDBY_AFTER_MS);
    expect(request.mayListenForWake()).toBe(true);
    current.controller.setMicrophoneMuted(true);
    expect(request.mayListenForWake()).toBe(false);
    request.onWake();
    expect(current.controller.current().phase).toBe("standby");
    current.controller.setMicrophoneMuted(false);
    request.onWake();
    expect(current.controller.current().phase).toBe("active");
    expect(current.connections[0].setWakeEnabled).toHaveBeenLastCalledWith(
      false,
    );
  });

  it("does not enter standby during speaking/thinking and restarts quiet time after activity", async () => {
    const current = rig();
    await current.controller.start();
    const request = current.requests[0];
    for (const activity of ["speaking", "thinking", "listening"] as const) {
      request.onEvent({ type: "activity", activity });
      current.advance(VOX_STANDBY_AFTER_MS * 2);
      expect(current.controller.current().phase).toBe("active");
    }
    request.onEvent({ type: "activity", activity: "quiet" });
    current.advance(VOX_STANDBY_AFTER_MS - 1);
    expect(current.controller.current().phase).toBe("active");
    current.advance(1);
    expect(current.controller.current().phase).toBe("standby");
  });

  it("allows hours in standby without a silence hang-up and ignores backwards/nonfinite clocks", async () => {
    const current = rig();
    await current.controller.start();
    current.advance(3 * 60 * 60_000);
    expect(current.controller.current()).toMatchObject({
      phase: "standby",
      elapsedMs: 3 * 60 * 60_000,
    });
    current.setNow(100);
    current.setNow(Number.NaN);
    expect(current.controller.current().elapsedMs).toBe(3 * 60 * 60_000);
    expect(current.connections[0].close).not.toHaveBeenCalled();
  });

  it("quiet heartbeats do not postpone the standby silence boundary", async () => {
    const current = rig();
    await current.controller.start();
    current.advance(60_000);
    current.requests[0].onEvent({ type: "activity", activity: "quiet" });
    current.advance(60_000);
    expect(current.controller.current().phase).toBe("standby");
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
  });
});

describe("Vox session-authority suspension", () => {
  it("synchronously gates active audio without ending the same-owner call or changing preferences", async () => {
    const current = rig();
    await current.controller.start();
    current.controller.setCaptionsEnabled(false);
    current.controller.setMinimized(true);
    const before = current.controller.current();
    const request = current.requests[0];
    const joined = current.connections[0];
    current.controller.suspendForSessionCheck();
    expect(current.controller.current()).toEqual({
      ...before,
      ownerVerified: false,
    });
    expect(request.mayTransmitAudio()).toBe(false);
    expect(request.mayPlayAudio()).toBe(false);
    expect(request.mayListenForWake()).toBe(false);
    expect(joined.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);
    expect(joined.setSpeakerEnabled).toHaveBeenLastCalledWith(false);
    expect(joined.setWakeEnabled).toHaveBeenLastCalledWith(false);
    expect(joined.stopPlayback).toHaveBeenCalledTimes(1);
    expect(joined.close).not.toHaveBeenCalled();
    await current.controller.bindOwner({ ...owner });
    expect(current.controller.current()).toEqual(before);
    expect(request.mayTransmitAudio()).toBe(true);
    expect(request.mayPlayAudio()).toBe(true);
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("preserves standby and mic/speaker preferences across a verified check without waking", async () => {
    const current = rig();
    await current.controller.start();
    current.controller.setSpeakerEnabled(false);
    current.controller.setMicrophoneMuted(true);
    current.advance(VOX_STANDBY_AFTER_MS);
    const before = current.controller.current();
    current.controller.suspendForSessionCheck();
    current.controller.resume();
    await current.controller.reconnect();
    await current.controller.start();
    current.requests[0].onWake();
    expect(current.controller.current()).toEqual({
      ...before,
      ownerVerified: false,
    });
    await current.controller.bindOwner(owner);
    expect(current.controller.current()).toEqual(before);
    expect(current.requests[0].mayListenForWake()).toBe(false);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(current.connections[0].setWakeEnabled).toHaveBeenLastCalledWith(
      false,
    );
    current.controller.setMicrophoneMuted(false);
    expect(current.requests[0].mayListenForWake()).toBe(true);
  });

  it("does not start for a suspended owner and never enables transport through preference controls", async () => {
    const current = rig();
    current.controller.suspendForSessionCheck();
    await current.controller.start();
    expect(current.transport.connect).not.toHaveBeenCalled();
    await current.controller.bindOwner(owner);
    await current.controller.start();
    current.controller.suspendForSessionCheck();
    current.controller.setMicrophoneMuted(true);
    current.controller.setMicrophoneMuted(false);
    current.controller.setSpeakerEnabled(false);
    current.controller.setSpeakerEnabled(true);
    current.controller.setCaptionsEnabled(false);
    current.advance(VOX_STANDBY_AFTER_MS * 5);
    expect(current.controller.current()).toMatchObject({
      ownerVerified: false,
      phase: "active",
      microphoneMuted: false,
      speakerEnabled: true,
      captionsEnabled: false,
    });
    expect(
      current.connections[0].setMicrophoneEnabled,
    ).toHaveBeenLastCalledWith(false);
    expect(current.connections[0].setSpeakerEnabled).toHaveBeenLastCalledWith(
      false,
    );
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
  });

  it("keeps a late successful join suspended until an explicit verified binding", async () => {
    const current = rig();
    const pending = deferred<VoxConnection>();
    current.transport.connect.mockImplementationOnce(async (request) => {
      current.requests.push(request);
      return pending.promise;
    });
    const started = current.controller.start();
    await vi.waitFor(() => expect(current.requests).toHaveLength(1));
    current.controller.suspendForSessionCheck();
    const joined = connection(current.requests[0]);
    pending.resolve(joined);
    await started;
    expect(current.controller.current()).toMatchObject({
      ownerVerified: false,
      phase: "active",
    });
    expect(joined.setMicrophoneEnabled).not.toHaveBeenCalledWith(true);
    expect(joined.setSpeakerEnabled).not.toHaveBeenCalledWith(true);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    await current.controller.bindOwner(owner);
    expect(joined.setMicrophoneEnabled).toHaveBeenLastCalledWith(true);
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("defers a not-yet-started reconnect join until session verification succeeds", async () => {
    const current = rig();
    await current.controller.start();
    const callId = current.controller.current().callId;
    const closing = deferred<void>();
    current.connections[0].close.mockReturnValue(closing.promise);
    const reconnect = current.controller.reconnect();
    current.controller.suspendForSessionCheck();
    closing.resolve();
    await reconnect;
    expect(current.controller.current()).toMatchObject({
      ownerVerified: false,
      phase: "reconnecting",
      callId,
    });
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    await current.controller.bindOwner(owner);
    expect(current.transport.connect).toHaveBeenCalledTimes(2);
    expect(current.controller.current()).toMatchObject({
      ownerVerified: true,
      phase: "active",
      callId,
    });
  });

  it("queues a disconnected transport while unverified and reconnects the same call only after binding", async () => {
    const current = rig();
    await current.controller.start();
    const first = current.requests[0];
    current.controller.suspendForSessionCheck();
    first.onEvent({ type: "activity", activity: "speaking" });
    first.onEvent({ type: "disconnected" });
    first.onEvent({ type: "disconnected" });
    await current.controller.reconnect();
    expect(current.controller.current()).toMatchObject({
      ownerVerified: false,
      phase: "active",
      activity: "quiet",
    });
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    await current.controller.bindOwner(owner);
    expect(current.transport.connect).toHaveBeenCalledTimes(2);
    expect(current.requests[1].callId).toBe(first.callId);
    expect(first.mayTransmitAudio()).toBe(false);
    expect(current.requests[1].mayTransmitAudio()).toBe(true);
  });

  it.each([null, anotherOwner, { ...owner, workspaceId: "" }])(
    "terminally clears the old call when session verification returns %j",
    async (next) => {
      const current = rig();
      await current.controller.start();
      const first = current.requests[0];
      current.controller.suspendForSessionCheck();
      await current.controller.bindOwner(next).catch(() => {});
      expect(current.controller.current()).toMatchObject({
        callId: null,
        phase: "ended",
        ownerVerified: next === anotherOwner,
      });
      expect(current.connections[0].close).toHaveBeenCalledTimes(1);
      await current.controller.bindOwner(owner);
      expect(first.mayTransmitAudio()).toBe(false);
      expect(first.mayListenForWake()).toBe(false);
      expect(current.controller.current().callId).toBeNull();
      expect(current.transport.connect).toHaveBeenCalledTimes(1);
    },
  );

  it("fresh verification never revives a call ended during the check", async () => {
    const current = rig();
    await current.controller.start();
    const first = current.requests[0];
    current.controller.suspendForSessionCheck();
    first.onEvent({ type: "ended" });
    await current.controller.bindOwner(owner);
    expect(current.controller.current()).toMatchObject({
      ownerVerified: true,
      phase: "ended",
      endReason: "transport-ended",
    });
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    expect(first.mayTransmitAudio()).toBe(false);
    expect(first.mayPlayAudio()).toBe(false);
  });

  it("suspends before publishing so reentrant controls and wake callbacks cannot regain audio", async () => {
    const current = rig();
    await current.controller.start();
    const first = current.requests[0];
    current.advance(VOX_STANDBY_AFTER_MS);
    let checked = false;
    current.controller.subscribe((state) => {
      if (!state.ownerVerified && !checked) {
        checked = true;
        expect(first.mayTransmitAudio()).toBe(false);
        expect(first.mayListenForWake()).toBe(false);
        current.controller.resume();
        first.onWake();
        void current.controller.reconnect();
        void current.controller.start();
      }
    });
    current.controller.suspendForSessionCheck();
    expect(checked).toBe(true);
    expect(current.controller.current()).toMatchObject({
      phase: "standby",
      ownerVerified: false,
    });
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("a new session check during synchronous control restoration stays suspended", async () => {
    const current = rig();
    await current.controller.start();
    current.controller.suspendForSessionCheck();
    let once = false;
    current.connections[0].setMicrophoneEnabled.mockImplementation(
      (enabled) => {
        if (enabled && !once) {
          once = true;
          current.controller.suspendForSessionCheck();
        }
      },
    );
    await current.controller.bindOwner(owner);
    expect(current.controller.current().ownerVerified).toBe(false);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(current.requests[0].mayPlayAudio()).toBe(false);
    expect(
      current.connections[0].setMicrophoneEnabled,
    ).toHaveBeenLastCalledWith(false);
    expect(current.connections[0].setSpeakerEnabled).toHaveBeenLastCalledWith(
      false,
    );
  });

  it.each(["speaking", "thinking"] as const)(
    "clears interrupted %s activity and gives the restored call a fresh quiet window",
    async (activity) => {
      const current = rig();
      await current.controller.start();
      current.requests[0].onEvent({ type: "activity", activity });
      current.controller.suspendForSessionCheck();
      current.requests[0].onEvent({ type: "activity", activity: "quiet" });
      current.advance(VOX_STANDBY_AFTER_MS * 3);
      expect(current.controller.current()).toMatchObject({
        ownerVerified: false,
        phase: "active",
        activity: "quiet",
      });
      await current.controller.bindOwner(owner);
      current.advance(VOX_STANDBY_AFTER_MS - 1);
      expect(current.controller.current().phase).toBe("active");
      current.advance(1);
      expect(current.controller.current().phase).toBe("standby");
    },
  );

  it("cannot restore disposed authority or inherit a call from a null initial owner", async () => {
    const current = rig();
    const controller = new VoxCallController({
      owner: null,
      transport: current.transport,
      newCallId: () => "30000000-0000-4000-8000-000000000001",
    });
    expect(controller.current().ownerVerified).toBe(false);
    await controller.bindOwner(owner);
    expect(controller.current()).toMatchObject({
      ownerVerified: true,
      callId: null,
    });
    await controller.start();
    await controller.dispose();
    await controller.bindOwner(owner);
    expect(controller.current()).toMatchObject({
      ownerVerified: false,
      phase: "ended",
    });
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
  });
});

describe("Vox reconnect and terminal resource fences", () => {
  it("closes the old connection before reconnect and preserves call identity and controls", async () => {
    const current = rig();
    await current.controller.start();
    const first = current.requests[0];
    current.controller.setMicrophoneMuted(true);
    current.controller.setSpeakerEnabled(false);
    current.controller.setCaptionsEnabled(false);
    current.controller.setMinimized(true);
    const closing = deferred<void>();
    current.connections[0].close.mockReturnValue(closing.promise);
    const reconnect = current.controller.reconnect();
    expect(current.controller.current().phase).toBe("reconnecting");
    expect(first.signal.aborted).toBe(true);
    expect(first.mayTransmitAudio()).toBe(false);
    expect(first.mayPlayAudio()).toBe(false);
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    first.onEvent({ type: "ended" });
    first.onEvent({ type: "disconnected" });
    closing.resolve();
    await reconnect;
    expect(current.requests[1].callId).toBe(first.callId);
    expect(current.requests[1].owner).toEqual(owner);
    expect(current.controller.current()).toMatchObject({
      phase: "active",
      minimized: true,
      captionsEnabled: false,
      microphoneMuted: true,
      speakerEnabled: false,
    });
    expect(current.connections[0].close).toHaveBeenCalledTimes(1);
    expect(
      current.connections[1].setMicrophoneEnabled,
    ).toHaveBeenLastCalledWith(false);
    expect(current.connections[1].setSpeakerEnabled).toHaveBeenLastCalledWith(
      false,
    );
  });

  it("automatically reconnects a current disconnection and ignores callbacks from older attempts", async () => {
    const current = rig();
    await current.controller.start();
    const first = current.requests[0];
    first.onEvent({ type: "disconnected" });
    await vi.waitFor(() => expect(current.requests).toHaveLength(2));
    await vi.waitFor(() =>
      expect(current.controller.current().phase).toBe("active"),
    );
    current.advance(VOX_STANDBY_AFTER_MS);
    first.onWake();
    first.onEvent({ type: "activity", activity: "speaking" });
    first.onEvent({ type: "disconnected" });
    expect(current.controller.current().phase).toBe("standby");
    expect(current.transport.connect).toHaveBeenCalledTimes(2);
    current.requests[1].onWake();
    expect(current.controller.current().phase).toBe("active");
  });

  it("reconnects standby without automatically enabling audio", async () => {
    const current = rig();
    await current.controller.start();
    current.advance(VOX_STANDBY_AFTER_MS);
    await current.controller.reconnect();
    expect(current.controller.current().phase).toBe("standby");
    expect(current.requests[1].mayTransmitAudio()).toBe(false);
    expect(
      current.connections[1].setMicrophoneEnabled,
    ).toHaveBeenLastCalledWith(false);
    expect(current.connections[1].setWakeEnabled).toHaveBeenLastCalledWith(
      true,
    );
  });

  it("does not complete a reconnect after hang-up during old-resource cleanup", async () => {
    const current = rig();
    await current.controller.start();
    const closing = deferred<void>();
    current.connections[0].close.mockReturnValue(closing.promise);
    const reconnect = current.controller.reconnect();
    const ended = current.controller.hangUp();
    closing.resolve();
    await Promise.all([reconnect, ended]);
    expect(current.controller.current().phase).toBe("ended");
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("does not overwrite hang-up from a synchronous transport abort listener", async () => {
    const current = rig();
    await current.controller.start();
    current.requests[0].signal.addEventListener("abort", () => {
      void current.controller.hangUp();
    });
    await current.controller.reconnect();
    expect(current.controller.current()).toMatchObject({
      phase: "ended",
      endReason: "hang-up",
    });
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    expect(current.connections[0].close).toHaveBeenCalledTimes(1);
  });

  it("registers resource cleanup before synchronous adapter callbacks can start a new call", async () => {
    const current = rig();
    await current.controller.start();
    let restarted: Promise<void> | undefined;
    let once = false;
    const closing = deferred<void>();
    current.connections[0].close.mockReturnValue(closing.promise);
    current.connections[0].setWakeEnabled.mockImplementation(() => {
      if (!once) {
        once = true;
        restarted = current.controller.start();
      }
    });
    const ended = current.controller.hangUp();
    await Promise.resolve();
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    closing.resolve();
    await Promise.all([ended, restarted]);
    expect(current.transport.connect).toHaveBeenCalledTimes(2);
    expect(current.controller.current().phase).toBe("active");
  });

  it.each(["hang-up", "owner-changed", "disposed"] as const)(
    "releases a late join after %s and leaves all its callbacks inert",
    async (reason) => {
      const current = rig();
      const pending = deferred<VoxConnection>();
      current.transport.connect.mockImplementation(async (request) => {
        current.requests.push(request);
        return pending.promise;
      });
      const started = current.controller.start();
      await vi.waitFor(() => expect(current.requests).toHaveLength(1));
      const request = current.requests[0];
      const ended =
        reason === "owner-changed"
          ? current.controller.bindOwner(anotherOwner)
          : reason === "disposed"
            ? current.controller.dispose()
            : current.controller.hangUp();
      const late = connection(request);
      pending.resolve(late);
      await Promise.all([started, ended]);
      expect(late.close).toHaveBeenCalledTimes(1);
      expect(late.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);
      expect(late.setWakeEnabled).toHaveBeenLastCalledWith(false);
      expect(request.signal.aborted).toBe(true);
      expect(request.mayTransmitAudio()).toBe(false);
      expect(request.mayPlayAudio()).toBe(false);
      expect(request.mayListenForWake()).toBe(false);
      request.onWake();
      request.onEvent({ type: "disconnected" });
      request.onEvent({ type: "ended" });
      request.onEvent({ type: "activity", activity: "speaking" });
      expect(current.controller.current()).toMatchObject({
        phase: "ended",
        activity: "quiet",
        endReason: reason,
        owner: reason === "owner-changed" ? anotherOwner : owner,
      });
      expect(current.transport.connect).toHaveBeenCalledTimes(1);
    },
  );

  it("resets private call metadata on owner or workspace change", async () => {
    const current = rig();
    await current.controller.start();
    current.advance(10_000);
    current.controller.setMinimized(true);
    const changedWorkspace = {
      ...owner,
      workspaceId: "20000000-0000-4000-8000-000000000002",
    };
    await current.controller.bindOwner(changedWorkspace);
    expect(current.controller.current()).toMatchObject({
      owner: changedWorkspace,
      phase: "ended",
      callId: null,
      startedAt: null,
      elapsedMs: 0,
      minimized: false,
    });
    await current.controller.start();
    expect(current.requests[1].owner).toEqual(changedWorkspace);
    expect(current.requests[1].callId).not.toBe(current.requests[0].callId);
  });

  it("same-owner re-render does not end or reconnect an active call", async () => {
    const current = rig();
    await current.controller.start();
    await current.controller.bindOwner({ ...owner });
    expect(current.controller.current().phase).toBe("active");
    expect(current.connections[0].close).not.toHaveBeenCalled();
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("revokes the old call before publishing an owner switch to reentrant subscribers", async () => {
    const current = rig();
    await current.controller.start();
    const first = current.requests[0];
    let restarted = false;
    current.controller.subscribe((state) => {
      if (state.phase === "ended" && !restarted) {
        restarted = true;
        void current.controller.start();
      }
    });
    await current.controller.bindOwner(anotherOwner);
    await vi.waitFor(() => expect(current.requests).toHaveLength(2));
    expect(current.requests[1].owner).toEqual(anotherOwner);
    expect(current.controller.current().owner).toEqual(anotherOwner);
    expect(current.controller.current().callId).toBe(
      current.requests[1].callId,
    );
    expect(first.mayTransmitAudio()).toBe(false);
    expect(first.mayListenForWake()).toBe(false);
  });

  it("invalid owner input immediately fences an active call before rejecting", async () => {
    const current = rig();
    await current.controller.start();
    const first = current.requests[0];
    const invalid = current.controller.bindOwner({ ...owner, userId: "" });
    expect(first.mayTransmitAudio()).toBe(false);
    expect(first.mayPlayAudio()).toBe(false);
    expect(current.controller.current().owner).toBeNull();
    await expect(invalid).rejects.toThrow("verified account");
    expect(current.connections[0].close).toHaveBeenCalledTimes(1);
  });

  it("waits for an aborted pending join and its late resource close before a new call joins", async () => {
    const current = rig();
    const pending = deferred<VoxConnection>();
    current.transport.connect.mockImplementationOnce(async (request) => {
      current.requests.push(request);
      return pending.promise;
    });
    const firstStart = current.controller.start();
    await vi.waitFor(() => expect(current.requests).toHaveLength(1));
    let hangUpFinished = false;
    const ended = current.controller.hangUp().then(() => {
      hangUpFinished = true;
    });
    const newStart = current.controller.start();
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    expect(hangUpFinished).toBe(false);
    const late = connection(current.requests[0]);
    const closing = deferred<void>();
    late.close.mockReturnValue(closing.promise);
    pending.resolve(late);
    await vi.waitFor(() => expect(late.close).toHaveBeenCalledTimes(1));
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    expect(hangUpFinished).toBe(false);
    closing.resolve();
    await Promise.all([firstStart, ended, newStart]);
    expect(current.transport.connect).toHaveBeenCalledTimes(2);
    expect(current.controller.current().phase).toBe("active");
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(current.requests[1].callId).not.toBe(current.requests[0].callId);
  });

  it("fails closed if a late aborted connection cannot release its resources", async () => {
    const current = rig();
    const pending = deferred<VoxConnection>();
    current.transport.connect.mockImplementationOnce(async (request) => {
      current.requests.push(request);
      return pending.promise;
    });
    const firstStart = current.controller.start();
    await vi.waitFor(() => expect(current.requests).toHaveLength(1));
    const ended = current.controller.hangUp();
    const next = current.controller.start();
    const late = connection(current.requests[0]);
    late.close.mockRejectedValue(new Error("close failed"));
    pending.resolve(late);
    await Promise.all([firstStart, ended, next]);
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
    expect(current.controller.current()).toMatchObject({
      phase: "ended",
      endReason: "cleanup-incomplete",
    });
  });

  it("never reuses an earlier call identifier even after a completed hang-up", async () => {
    const current = rig();
    const controller = new VoxCallController({
      owner,
      transport: current.transport,
      newCallId: () => "30000000-0000-4000-8000-000000000001",
    });
    await controller.start();
    await controller.hangUp();
    await expect(controller.start()).rejects.toThrow("fresh call identifier");
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
  });

  it.each(["owner", "call"] as const)(
    "rejects a connection with a foreign %s binding and releases it",
    async (field) => {
      const current = rig();
      const wrong = connection({
        owner,
        callId: "foreign",
      } as VoxTransportRequest);
      current.transport.connect.mockImplementation(async (request) => {
        wrong.callId = field === "call" ? "foreign" : request.callId;
        wrong.owner = field === "owner" ? anotherOwner : owner;
        return wrong;
      });
      await current.controller.start();
      expect(current.controller.current()).toMatchObject({
        phase: "ended",
        endReason: "connection-failed",
      });
      expect(wrong.close).toHaveBeenCalledTimes(1);
      expect(wrong.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);
    },
  );

  it("suppresses raw transport errors and fails closed when cleanup fails", async () => {
    const current = rig();
    current.transport.connect.mockRejectedValueOnce(
      new Error("private token=secret"),
    );
    await current.controller.start();
    expect(JSON.stringify(current.controller.current())).not.toContain(
      "secret",
    );
    expect(current.controller.current().endReason).toBe("connection-failed");
    await current.controller.start();
    current.connections[0].close.mockRejectedValue(
      new Error("private transport body"),
    );
    await current.controller.hangUp();
    await current.controller.start();
    expect(current.controller.current()).toMatchObject({
      phase: "ended",
      endReason: "cleanup-incomplete",
    });
    expect(current.transport.connect).toHaveBeenCalledTimes(2);
  });

  it("continues cleanup after a control throws and revokes leases first", async () => {
    const current = rig();
    await current.controller.start();
    const request = current.requests[0];
    const joined = current.connections[0];
    joined.setMicrophoneEnabled.mockImplementation(() => {
      expect(request.mayTransmitAudio()).toBe(false);
      throw new Error("device vanished");
    });
    current.controller.setMicrophoneMuted(true);
    await vi.waitFor(() => expect(joined.close).toHaveBeenCalledTimes(1));
    expect(joined.setSpeakerEnabled).toHaveBeenLastCalledWith(false);
    expect(joined.setWakeEnabled).toHaveBeenLastCalledWith(false);
    expect(joined.stopPlayback).toHaveBeenCalled();
    expect(current.controller.current().endReason).toBe("connection-failed");
  });

  it("releases exactly once on repeated terminal calls and never wakes or reconnects after dispose", async () => {
    const current = rig();
    await current.controller.start();
    const request = current.requests[0];
    const listener = vi.fn();
    current.controller.subscribe(listener);
    await current.controller.dispose();
    await current.controller.dispose();
    const notifications = listener.mock.calls.length;
    current.controller.setSpeakerEnabled(true);
    current.controller.setMicrophoneMuted(false);
    current.controller.resume();
    current.controller.tick();
    request.onWake();
    request.onEvent({ type: "disconnected" });
    await current.controller.start();
    await current.controller.reconnect();
    expect(current.connections[0].close).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledTimes(notifications);
    expect(request.mayListenForWake()).toBe(false);
    expect(current.transport.connect).toHaveBeenCalledTimes(1);
  });
});

describe("Vox same-room recovery", () => {
  it("does not carry a queued wake question or listening claim into an ended or replacement call", async () => {
    const env = rig();
    await env.controller.start();
    env.requests[0].onEvent({ type: "wake-input", status: "queued" });
    expect(env.controller.current().wakeInputStatus).toBe("queued");
    await env.controller.hangUp();
    expect(env.controller.current()).toMatchObject({
      wakeInputStatus: null,
      wakeStatus: "off",
    });
    await env.controller.start();
    expect(env.controller.current()).toMatchObject({
      wakeInputStatus: null,
      wakeStatus: "off",
    });
    await env.controller.hangUp();
  });

  it("retains the call and connection while the SDK recovers, and waits for fresh owner verification", async () => {
    const env = rig();
    await env.controller.start();
    const request = env.requests[0];
    const before = env.controller.current();
    request.onEvent({ type: "reconnecting" });
    expect(env.controller.current()).toMatchObject({
      phase: "reconnecting",
      callId: before.callId,
    });
    expect(request.mayTransmitAudio()).toBe(false);
    expect(request.mayPlayAudio()).toBe(false);
    expect(request.mayRenewReservation?.()).toBe(true);
    expect(env.connections[0].close).not.toHaveBeenCalled();
    env.controller.suspendForSessionCheck();
    request.onEvent({ type: "reconnected" });
    expect(env.controller.current().phase).toBe("reconnecting");
    await env.controller.bindOwner(owner);
    expect(env.controller.current()).toMatchObject({
      phase: "active",
      callId: before.callId,
      startedAt: before.startedAt,
    });
    expect(request.mayTransmitAudio()).toBe(true);
    expect(env.transport.connect).toHaveBeenCalledTimes(1);
    await env.controller.hangUp();
  });

  it("restores standby after SDK recovery without sending audio or creating another conversation", async () => {
    const env = rig();
    await env.controller.start();
    env.advance(VOX_STANDBY_AFTER_MS);
    const request = env.requests[0];
    request.onEvent({ type: "reconnecting" });
    request.onEvent({ type: "reconnected" });
    expect(env.controller.current().phase).toBe("standby");
    expect(request.mayTransmitAudio()).toBe(false);
    expect(request.mayListenForWake()).toBe(true);
    expect(env.transport.connect).toHaveBeenCalledTimes(1);
    await env.controller.hangUp();
  });

  it("cannot reconnect or restore media after hang-up or an owner change during recovery", async () => {
    const env = rig();
    await env.controller.start();
    const request = env.requests[0];
    request.onEvent({ type: "reconnecting" });
    await env.controller.bindOwner(anotherOwner);
    request.onEvent({ type: "reconnected" });
    expect(env.controller.current()).toMatchObject({
      phase: "ended",
      owner: anotherOwner,
    });
    expect(request.mayTransmitAudio()).toBe(false);
    expect(env.transport.connect).toHaveBeenCalledTimes(1);
  });
});

describe("terminal loss after SDK reconnect starts", () => {
  it.each([false, true])(
    "creates a replacement with the same call and standby=%s after cleanup",
    async (standby) => {
      const env = rig();
      await env.controller.start();
      if (standby) env.advance(VOX_STANDBY_AFTER_MS);
      const before = env.controller.current();
      const old = env.requests[0];
      const closing = deferred<void>();
      env.connections[0].close.mockReturnValue(closing.promise);
      old.onEvent({ type: "reconnecting" });
      old.onEvent({ type: "disconnected" });
      expect(old.mayTransmitAudio()).toBe(false);
      expect(old.mayPlayAudio()).toBe(false);
      expect(env.transport.connect).toHaveBeenCalledTimes(1);
      closing.resolve();
      await vi.waitFor(() => expect(env.requests).toHaveLength(2));
      await vi.waitFor(() =>
        expect(env.controller.current().phase).toBe(
          standby ? "standby" : "active",
        ),
      );
      expect(env.controller.current()).toMatchObject({
        callId: before.callId,
        startedAt: before.startedAt,
      });
      expect(env.connections[0].close).toHaveBeenCalledTimes(1);
      old.onEvent({ type: "ended" });
      expect(env.controller.current().phase).toBe(
        standby ? "standby" : "active",
      );
      await env.controller.hangUp();
    },
  );

  it("waits for fresh account verification after terminal loss during SDK recovery", async () => {
    const env = rig();
    await env.controller.start();
    const old = env.requests[0];
    old.onEvent({ type: "reconnecting" });
    env.controller.suspendForSessionCheck();
    old.onEvent({ type: "disconnected" });
    old.onEvent({ type: "reconnected" });
    expect(env.transport.connect).toHaveBeenCalledTimes(1);
    expect(old.mayTransmitAudio()).toBe(false);
    await env.controller.bindOwner(owner);
    expect(env.requests).toHaveLength(2);
    expect(env.requests[1].callId).toBe(old.callId);
    await env.controller.hangUp();
  });

  it("hang-up during terminal recovery never creates a replacement", async () => {
    const env = rig();
    await env.controller.start();
    const closing = deferred<void>();
    env.connections[0].close.mockReturnValue(closing.promise);
    const old = env.requests[0];
    old.onEvent({ type: "reconnecting" });
    old.onEvent({ type: "disconnected" });
    const ended = env.controller.hangUp();
    closing.resolve();
    await ended;
    expect(env.transport.connect).toHaveBeenCalledTimes(1);
    expect(env.controller.current().phase).toBe("ended");
  });
});
