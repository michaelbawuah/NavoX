// @vitest-environment jsdom

import type { LocalAudioTrack } from "livekit-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VoxTransportRequest } from "./vox-call";
import {
  createLiveKitVoxTransport,
  type VoxAudioCapture,
  type VoxAudioCaptureFactory,
  type VoxLiveKitRoom,
  type VoxMediaLease,
  type VoxRoomEvents,
} from "./vox-livekit";

const owner = {
  userId: "11111111-1111-4111-8111-111111111111",
  workspaceId: "22222222-2222-4222-8222-222222222222",
};
const callId = "33333333-3333-4333-8333-333333333333";
const agent = "navox-agent";
const participant = "navox-student";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

async function settle() {
  for (let index = 0; index < 30; index++) await Promise.resolve();
}

function token(changes: Record<string, unknown> = {}) {
  const value = {
    sub: participant,
    exp: Math.floor((Date.now() + 120_000) / 1000),
    video: { room: "navox-room", roomJoin: true, canPublishData: false },
    ...changes,
  };
  return `e30.${btoa(JSON.stringify(value)).replaceAll("=", "")}.c2ln`;
}

class FakeRoom implements VoxLiveKitRoom {
  events: VoxRoomEvents | null = null;
  participants = new Set([agent]);
  name = vi.fn(() => "navox-room");
  identity = vi.fn(() => participant);
  hasParticipant = (identity: string) => this.participants.has(identity);
  connect = vi.fn(async () => {});
  disconnect = vi.fn(async () => {});
  startAudio = vi.fn(async () => {});
  unlisten = vi.fn(() => {
    this.events = null;
  });
  listen(events: VoxRoomEvents) {
    this.events = events;
    return this.unlisten;
  }
  subscribeAgent = vi.fn();
  publishAudio = vi.fn(
    async (_track: LocalAudioTrack, _identity: string) => {},
  );
  unpublishAudio = vi.fn(async (_track: LocalAudioTrack) => {});
}

function environment() {
  const abort = new AbortController();
  const request: VoxTransportRequest = {
    owner: { ...owner },
    callId,
    signal: abort.signal,
    mayTransmitAudio: vi.fn(() => true),
    mayPlayAudio: vi.fn(() => true),
    mayListenForWake: () => false,
    onWake: vi.fn(),
    onEvent: vi.fn(),
  };
  const lease: VoxMediaLease = {
    owner: { ...owner },
    callId,
    roomName: "navox-room",
    participantIdentity: participant,
    agentIdentity: agent,
    serverUrl: "wss://media.example.com",
    token: token(),
    expiresAt: Date.now() + 120_000,
    renew: vi.fn(async () => ({ expiresAt: Date.now() + 120_000 })),
    close: vi.fn(async () => {}),
  };
  const room = new FakeRoom();
  const media = {
    kind: "audio",
    readyState: "live",
    enabled: false,
    stop: vi.fn(() => {
      media.enabled = false;
      media.readyState = "ended";
    }),
  };
  const capture: VoxAudioCapture = {
    track: media as unknown as MediaStreamTrack,
    setEnabled: vi.fn((value) => {
      if (!value) media.enabled = false;
    }),
    stop: vi.fn(() => media.stop()),
  };
  const local = {
    mediaStreamTrack: capture.track,
    mute: vi.fn(async () => {
      media.enabled = false;
    }),
    unmute: vi.fn(async () => {
      media.enabled = true;
    }),
    pauseUpstream: vi.fn(async () => {}),
    resumeUpstream: vi.fn(async () => {}),
    stop: vi.fn(() => media.stop()),
  };
  const sdk = {
    room: vi.fn(() => room),
    audioTrack: vi.fn(() => local as unknown as LocalAudioTrack),
  };
  const factory: VoxAudioCaptureFactory = {
    qualified: true,
    create: vi.fn(async () => capture),
  };
  const admission = { available: true, join: vi.fn(async () => lease) };
  const loadSdk = vi.fn(async () => sdk);
  const transport = createLiveKitVoxTransport({
    admission,
    capture: factory,
    loadSdk,
  });
  const voice = async (speech: boolean) => {
    const create = vi.mocked(factory.create);
    const options = create.mock.calls[create.mock.calls.length - 1]?.[0];
    if (!options) throw new Error("Capture not opened");
    await options.onVoiceActivity(speech);
    await settle();
  };
  return {
    abort,
    request,
    lease,
    room,
    media,
    capture,
    local,
    factory,
    admission,
    loadSdk,
    sdk,
    transport,
    voice,
  };
}

function remoteAudio() {
  const element = document.createElement("audio");
  element.play = vi.fn(async () => {});
  element.pause = vi.fn();
  const track = {
    kind: "audio",
    attach: vi.fn(() => element),
    detach: vi.fn(() => [element]),
  };
  return { track, element };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(1_800_000_000_000);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("qualified LiveKit Vox adapter", () => {
  it("stays unavailable without qualified local VAD and performs no work on construction", () => {
    const env = environment();
    expect(
      createLiveKitVoxTransport({ admission: env.admission }).available,
    ).toBe(false);
    env.factory.qualified = false;
    expect(env.transport.available).toBe(false);
    expect(env.transport.localWakeAvailable).toBe(false);
    expect(env.admission.join).not.toHaveBeenCalled();
    expect(env.loadSdk).not.toHaveBeenCalled();
    expect(env.factory.create).not.toHaveBeenCalled();
  });

  it("joins the admitted room and expected agent before any capture or publication", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    expect(connection.owner).toEqual(owner);
    expect(connection.callId).toBe(callId);
    expect(env.room.connect).toHaveBeenCalledWith(
      env.lease.serverUrl,
      env.lease.token,
    );
    expect(env.room.subscribeAgent).toHaveBeenCalledWith(agent);
    expect(env.factory.create).not.toHaveBeenCalled();
    expect(env.room.publishAudio).not.toHaveBeenCalled();
    await connection.close();
    expect(env.lease.close).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("waits for the admitted agent and refuses a room with no agent", async () => {
    const env = environment();
    env.room.participants.clear();
    let resolved = false;
    const pending = env.transport.connect(env.request).then((value) => {
      resolved = true;
      return value;
    });
    await settle();
    expect(resolved).toBe(false);
    env.room.events?.participantJoined("foreign-agent");
    await settle();
    expect(resolved).toBe(false);
    env.room.participants.add(agent);
    env.room.events?.participantJoined(agent);
    const connection = await pending;
    await connection.close();
    const absent = environment();
    absent.room.participants.clear();
    const rejected = expect(
      absent.transport.connect(absent.request),
    ).rejects.toThrow("unavailable");
    await vi.advanceTimersByTimeAsync(15_000);
    await rejected;
    expect(absent.factory.create).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    { serverUrl: "ws://media.example.com" },
    { serverUrl: "wss://user:password@media.example.com" },
    { callId: "44444444-4444-4444-8444-444444444444" },
    { owner: { ...owner, userId: "44444444-4444-4444-8444-444444444444" } },
    { token: "private-invalid-grant" },
    { expiresAt: 1 },
  ])(
    "rejects mismatched or unsafe grants before SDK loading: %j",
    async (change) => {
      const env = environment();
      Object.assign(env.lease, change);
      await expect(env.transport.connect(env.request)).rejects.toThrow(
        "unavailable",
      );
      expect(env.loadSdk).not.toHaveBeenCalled();
      expect(env.factory.create).not.toHaveBeenCalled();
      expect(env.lease.close).not.toHaveBeenCalled();
    },
  );

  it("rejects mismatched JWT room/identity and actual SDK room identity", async () => {
    for (const claims of [
      { sub: "foreign" },
      { video: { room: "foreign", roomJoin: true, canPublishData: false } },
    ]) {
      const env = environment();
      env.lease.token = token(claims);
      await expect(env.transport.connect(env.request)).rejects.toThrow(
        "unavailable",
      );
      expect(env.loadSdk).not.toHaveBeenCalled();
    }
    const env = environment();
    env.room.identity.mockReturnValue("foreign");
    await expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    expect(env.room.disconnect).toHaveBeenCalled();
    expect(env.factory.create).not.toHaveBeenCalled();
  });

  it("does not load the SDK after a late admission resolves for an aborted attempt", async () => {
    const env = environment();
    const admission = deferred<VoxMediaLease>();
    env.admission.join.mockReturnValue(admission.promise);
    const pending = expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    env.abort.abort();
    admission.resolve(env.lease);
    await pending;
    expect(env.loadSdk).not.toHaveBeenCalled();
    expect(env.lease.close).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("disconnects again when an aborted SDK join settles late", async () => {
    const env = environment();
    const join = deferred<void>();
    env.room.connect.mockReturnValue(join.promise);
    const pending = expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    await settle();
    env.abort.abort();
    expect(env.room.disconnect).toHaveBeenCalledTimes(1);
    join.resolve();
    await pending;
    expect(env.room.disconnect).toHaveBeenCalledTimes(2);
    expect(env.factory.create).not.toHaveBeenCalled();
    expect(env.room.events).toBeNull();
  });

  it("publishes only qualified processed audio and pauses upstream outside local VAD speech", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    expect(env.room.publishAudio).toHaveBeenCalledWith(env.local, agent);
    expect(env.media.enabled).toBe(false);
    expect(env.local.resumeUpstream).not.toHaveBeenCalled();
    await env.voice(true);
    expect(env.local.resumeUpstream).toHaveBeenCalled();
    expect(env.media.enabled).toBe(true);
    await env.voice(false);
    expect(env.media.enabled).toBe(false);
    expect(env.local.pauseUpstream).toHaveBeenCalled();
    expect(env.capture.setEnabled).not.toHaveBeenCalledWith(false);
    connection.setMicrophoneEnabled(false);
    expect(env.media.enabled).toBe(false);
    expect(env.capture.setEnabled).toHaveBeenCalledWith(false);
    expect(env.capture.stop).toHaveBeenCalled();
    await connection.close();
  });

  it("stops a capture that settles after mute without publishing it", async () => {
    const env = environment();
    const acquired = deferred<VoxAudioCapture>();
    vi.mocked(env.factory.create).mockReturnValue(acquired.promise);
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    connection.setMicrophoneEnabled(false);
    acquired.resolve(env.capture);
    await settle();
    expect(env.capture.stop).toHaveBeenCalled();
    expect(env.media.enabled).toBe(false);
    expect(env.room.publishAudio).not.toHaveBeenCalled();
    await connection.close();
  });

  it("physically gates audio during a late publish and a late SDK unmute", async () => {
    const env = environment();
    const published = deferred<void>();
    env.room.publishAudio.mockReturnValue(published.promise);
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    connection.setMicrophoneEnabled(false);
    expect(env.media.enabled).toBe(false);
    published.resolve();
    await settle();
    expect(env.room.unpublishAudio).toHaveBeenCalled();
    await connection.close();

    const race = environment();
    const active = await race.transport.connect(race.request);
    active.setMicrophoneEnabled(true);
    await settle();
    const unmute = deferred<void>();
    race.local.unmute.mockImplementation(async () => {
      await unmute.promise;
      race.media.enabled = true;
    });
    const speech = race.voice(true);
    await settle();
    active.setMicrophoneEnabled(false);
    expect(race.media.enabled).toBe(false);
    unmute.resolve();
    await speech;
    expect(race.media.enabled).toBe(false);
    expect(race.local.resumeUpstream).not.toHaveBeenCalled();
    await active.close();
  });

  it("accepts remote audio only from the expected agent and physically pauses on speaker-off", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setSpeakerEnabled(true);
    const foreign = remoteAudio();
    const own = remoteAudio();
    env.room.events?.trackSubscribed(foreign.track, "foreign");
    env.room.events?.trackSubscribed(own.track, agent);
    await settle();
    expect(foreign.track.attach).not.toHaveBeenCalled();
    expect(foreign.track.detach).toHaveBeenCalled();
    expect(own.element.play).toHaveBeenCalled();
    connection.setSpeakerEnabled(false);
    expect(own.element.muted).toBe(true);
    expect(own.element.pause).toHaveBeenCalled();
    expect(own.element.srcObject).toBeNull();
    await connection.close();
  });

  it("keeps autoplay denial recoverable and rejects late playback-ready after speaker-off", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    const own = remoteAudio();
    vi.mocked(own.element.play).mockRejectedValue(
      new DOMException("blocked", "NotAllowedError"),
    );
    connection.setSpeakerEnabled(true);
    env.room.events?.trackSubscribed(own.track, agent);
    await settle();
    expect(env.request.onEvent).toHaveBeenCalledWith({
      type: "playback-blocked",
    });
    expect(env.room.disconnect).not.toHaveBeenCalled();
    const played = deferred<void>();
    vi.mocked(own.element.play).mockReturnValue(played.promise);
    connection.setSpeakerEnabled(true);
    await settle();
    vi.mocked(env.request.onEvent).mockClear();
    connection.setSpeakerEnabled(false);
    played.resolve();
    await settle();
    expect(env.request.onEvent).not.toHaveBeenCalledWith({
      type: "playback-ready",
    });
    expect(own.element.muted).toBe(true);
    await connection.close();
  });

  it("stops capture and playback synchronously on document hiding", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    connection.setSpeakerEnabled(true);
    await settle();
    await env.voice(true);
    const audio = remoteAudio();
    env.room.events?.trackSubscribed(audio.track, agent);
    await settle();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(env.media.enabled).toBe(false);
    expect(env.capture.stop).toHaveBeenCalled();
    expect(audio.element.muted).toBe(true);
    expect(audio.element.pause).toHaveBeenCalled();
    await connection.close();
  });

  it("renews at30seconds and expires or fails closed with terminal events and no timers", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(env.lease.renew).toHaveBeenCalledTimes(1);
    vi.mocked(env.lease.renew).mockRejectedValue(
      new Error("synthetic provider detail"),
    );
    await vi.advanceTimersByTimeAsync(30_000);
    await settle();
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
    expect(vi.getTimerCount()).toBe(0);
    expect(env.lease.close).not.toHaveBeenCalled();
    await connection.close();
    const expired = environment();
    const active = await expired.transport.connect(expired.request);
    expired.lease.expiresAt = Date.now() + 500;
    await vi.advanceTimersByTimeAsync(1000);
    expect(expired.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
    await active.close();
  });

  it("hands reconnection to the controller and cleans listeners without ending the reservation", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    env.room.events?.disconnected();
    await settle();
    expect(env.request.onEvent).toHaveBeenCalledTimes(1);
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "disconnected" });
    expect(env.room.events).toBeNull();
    expect(env.lease.close).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    await connection.close();
  });

  it("ends when the processed capture fails closed instead of claiming a working microphone", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    env.media.stop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
    expect(env.room.disconnect).toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    await connection.close();
  });

  it("retires state before a real-factory-style synchronous quiet callback on mute", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    await env.voice(true);
    const options = vi.mocked(env.factory.create).mock.calls[0][0];
    vi.mocked(env.capture.setEnabled).mockImplementation((enabled) => {
      if (!enabled) {
        env.media.enabled = false;
        void options.onVoiceActivity(false);
      }
    });
    expect(() => connection.setMicrophoneEnabled(false)).not.toThrow();
    await settle();
    expect(env.capture.stop).toHaveBeenCalled();
    expect(env.media.enabled).toBe(false);
    expect(env.request.onEvent).not.toHaveBeenCalledWith({ type: "ended" });
    await connection.close();
  });

  it.each(["resolve", "reject"] as const)(
    "ignores a stale play %s for an SDK-recycled audio element after off/on",
    async (outcome) => {
      const env = environment();
      const connection = await env.transport.connect(env.request);
      const audio = remoteAudio();
      const old = deferred<void>();
      vi.mocked(audio.element.play)
        .mockReturnValueOnce(old.promise)
        .mockResolvedValue(undefined);
      connection.setSpeakerEnabled(true);
      env.room.events?.trackSubscribed(audio.track, agent);
      connection.setSpeakerEnabled(false);
      connection.setSpeakerEnabled(true);
      await settle();
      const detachCount = audio.track.detach.mock.calls.length;
      vi.mocked(env.request.onEvent).mockClear();
      if (outcome === "resolve") old.resolve();
      else
        old.reject(new DOMException("Old autoplay denial", "NotAllowedError"));
      await settle();
      // The SDK also emits an unscoped playback failure for an old play promise.
      env.room.events?.playbackChanged(false);
      expect(audio.element.muted).toBe(false);
      expect(audio.track.detach).toHaveBeenCalledTimes(detachCount);
      expect(env.request.onEvent).not.toHaveBeenCalledWith({
        type: "playback-blocked",
      });
      await connection.close();
    },
  );

  it("recreates capture after off/on while the prior publication is still pending", async () => {
    const env = environment();
    const next = environment();
    vi.mocked(env.factory.create)
      .mockResolvedValueOnce(env.capture)
      .mockResolvedValueOnce(next.capture);
    env.sdk.audioTrack
      .mockReturnValueOnce(env.local as unknown as LocalAudioTrack)
      .mockReturnValueOnce(next.local as unknown as LocalAudioTrack);
    const published = deferred<void>();
    env.room.publishAudio
      .mockReturnValueOnce(published.promise)
      .mockResolvedValue(undefined);
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    connection.setMicrophoneEnabled(false);
    connection.setMicrophoneEnabled(true);
    expect(env.factory.create).toHaveBeenCalledTimes(1);
    published.resolve();
    await settle();
    expect(env.factory.create).toHaveBeenCalledTimes(2);
    expect(env.room.publishAudio).toHaveBeenLastCalledWith(next.local, agent);
    await env.voice(true);
    expect(next.media.enabled).toBe(true);
    expect(env.request.onEvent).not.toHaveBeenCalledWith({ type: "ended" });
    await connection.close();
  });

  it("ignores retired capture VAD callbacks after a replacement capture starts", async () => {
    const env = environment();
    const next = environment();
    vi.mocked(env.factory.create)
      .mockResolvedValueOnce(env.capture)
      .mockResolvedValueOnce(next.capture);
    env.sdk.audioTrack
      .mockReturnValueOnce(env.local as unknown as LocalAudioTrack)
      .mockReturnValueOnce(next.local as unknown as LocalAudioTrack);
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    const retired = vi.mocked(env.factory.create).mock.calls[0][0];
    connection.setMicrophoneEnabled(false);
    connection.setMicrophoneEnabled(true);
    await settle();
    await env.voice(true);
    vi.mocked(env.request.onEvent).mockClear();
    await retired.onVoiceActivity(false);
    await retired.onVoiceActivity(true);
    await settle();
    expect(next.media.enabled).toBe(true);
    expect(env.request.onEvent).not.toHaveBeenCalled();
    expect(retired.mayCapture()).toBe(false);
    await connection.close();
  });

  it.each(["disconnect", "unpublish"] as const)(
    "surfaces %s cleanup failure after synchronously stopping local capture",
    async (failure) => {
      const env = environment();
      const connection = await env.transport.connect(env.request);
      connection.setMicrophoneEnabled(true);
      await settle();
      await env.voice(true);
      if (failure === "disconnect")
        env.room.disconnect.mockRejectedValue(new Error("private SDK details"));
      else
        env.room.unpublishAudio.mockRejectedValue(
          new Error("private SDK details"),
        );
      const closing = connection.close();
      expect(env.media.enabled).toBe(false);
      expect(env.capture.stop).toHaveBeenCalled();
      await expect(closing).rejects.toThrow("unavailable");
      expect(env.transport.available).toBe(false);
      await expect(env.transport.connect(env.request)).rejects.toThrow(
        "unavailable",
      );
      expect(env.admission.join).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("poisons admission after failed-connect teardown uncertainty, even when no connection returned", async () => {
    const env = environment();
    env.room.identity.mockReturnValue("unexpected-identity");
    env.room.disconnect.mockRejectedValue(
      new Error("synthetic incomplete teardown"),
    );
    await expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    expect(env.transport.available).toBe(false);
    await expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    expect(env.admission.join).toHaveBeenCalledTimes(1);
    expect(env.room.connect).toHaveBeenCalledTimes(1);
    expect(env.factory.create).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("binds actual SDK signal reconnects to synchronous controller-owned cleanup", async () => {
    const env = environment();
    const listeners = new Map<string, (...args: unknown[]) => void>();
    const connect = vi.fn(async () => {});
    const disconnect = vi.fn(async () => {});
    const Room = vi.fn(
      class {
        constructor(
          readonly options: { reconnectPolicy: { nextRetryDelayInMs(): null } },
        ) {}
        name = "navox-room";
        localParticipant = { identity: participant };
        remoteParticipants = new Map([
          [agent, { trackPublications: new Map() }],
        ]);
        connect = connect;
        disconnect = disconnect;
        startAudio = async () => {};
        on(event: string, listener: (...args: unknown[]) => void) {
          listeners.set(event, listener);
          return this;
        }
        off(event: string) {
          listeners.delete(event);
          return this;
        }
      },
    );
    vi.doMock("livekit-client", () => ({
      Room,
      LocalAudioTrack: class {},
      setLogLevel: vi.fn(),
      LogLevel: { silent: 5 },
      Track: { Kind: { Audio: "audio" }, Source: { Microphone: "microphone" } },
      RoomEvent: {
        ParticipantConnected: "participantConnected",
        ParticipantDisconnected: "participantDisconnected",
        TrackPublished: "trackPublished",
        TrackSubscribed: "trackSubscribed",
        TrackUnsubscribed: "trackUnsubscribed",
        Disconnected: "disconnected",
        Reconnecting: "reconnecting",
        SignalReconnecting: "signalReconnecting",
        AudioPlaybackStatusChanged: "audioPlaybackChanged",
      },
    }));
    try {
      const transport = createLiveKitVoxTransport({
        admission: env.admission,
        capture: env.factory,
      });
      const connection = await transport.connect(env.request);
      expect(connect).toHaveBeenCalledWith(
        env.lease.serverUrl,
        env.lease.token,
        { autoSubscribe: false },
      );
      expect(
        Room.mock.calls[0][0].reconnectPolicy.nextRetryDelayInMs(),
      ).toBeNull();
      expect(listeners.has("signalReconnecting")).toBe(true);
      listeners.get("signalReconnecting")?.();
      await settle();
      expect(env.request.onEvent).toHaveBeenCalledWith({
        type: "disconnected",
      });
      expect(disconnect).toHaveBeenCalled();
      expect(listeners.size).toBe(0);
      await connection.close();
    } finally {
      vi.doUnmock("livekit-client");
    }
  });
});
