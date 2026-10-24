// @vitest-environment jsdom

import type { LocalAudioTrack } from "livekit-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  VOX_STANDBY_AFTER_MS,
  VoxCallController,
  type VoxTransportRequest,
} from "./vox-call";
import {
  createLiveKitVoxTransport,
  type VoxAudioCapture,
  type VoxAudioCaptureFactory,
  type VoxLiveKitRoom,
  type VoxMediaLease,
  type VoxRoomEvents,
} from "./vox-livekit";
import type { VoxWakeFactory } from "./vox-local-wake";
import { VoxReservationError } from "./vox-reservations";
import type { VoxWakeBinding } from "./vox-runtime-wake";

const owner = {
  userId: "11111111-1111-4111-8111-111111111111",
  workspaceId: "22222222-2222-4222-8222-222222222222",
};
const callId = "33333333-3333-4333-8333-333333333333";
const connectionId = "44444444-4444-4444-8444-444444444444";
const generation = 4;
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
    metadata: JSON.stringify({ connection_id: connectionId, generation }),
    sub: participant,
    exp: Math.floor((Date.now() + 120_000) / 1000),
    nbf: Math.floor(Date.now() / 1000) - 1,
    video: {
      room: "navox-room",
      roomJoin: true,
      canPublishData: false,
      canPublish: true,
      canSubscribe: true,
      canUpdateOwnMetadata: false,
      canPublishSources: ["microphone"],
    },
    ...changes,
  };
  return `e30.${btoa(JSON.stringify(value)).replaceAll("=", "")}.c2ln`;
}

class FakeRoom implements VoxLiveKitRoom {
  events: VoxRoomEvents | null = null;
  participants = new Set([agent]);
  name = vi.fn(() => "navox-room");
  isConnected = vi.fn(() => true);
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

function environment(options: { wakeFactory?: VoxWakeFactory } = {}) {
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
    connectionId,
    generation,
    roomName: "navox-room",
    participantIdentity: participant,
    agentIdentity: agent,
    serverUrl: "wss://media.example.com",
    token: token(),
    expiresAt: Date.now() + 120_000,
    renew: vi.fn(async () => ({
      connectionId,
      generation,
      expiresAt: lease.expiresAt,
    })),
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
    ...options,
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
    expect(env.lease.close).toHaveBeenCalledTimes(1);
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
      expect(env.lease.close).toHaveBeenCalledTimes(1);
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

  it.each([
    undefined,
    "not-json",
    null,
    JSON.stringify({ connection_id: callId, generation }),
    JSON.stringify({ connection_id: connectionId, generation: generation + 1 }),
    JSON.stringify({
      connection_id: connectionId,
      generation: String(generation),
    }),
    JSON.stringify({ connection_id: connectionId }),
    JSON.stringify([connectionId, generation]),
  ])(
    "rejects absent, forged or swapped JWT epoch metadata before loading media: %j",
    async (metadata) => {
      const env = environment();
      env.lease.token = token({ metadata });
      await expect(env.transport.connect(env.request)).rejects.toThrow(
        "unavailable",
      );
      expect(env.loadSdk).not.toHaveBeenCalled();
      expect(env.factory.create).not.toHaveBeenCalled();
      expect(env.room.connect).not.toHaveBeenCalled();
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it.each([
    { connectionId: callId },
    { generation: generation + 1 },
    { generation: String(generation) },
    { expiresAt: 1_800_000_151_000 },
  ])(
    "retires the exact active epoch and microphone for a swapped renewal proof: %j",
    async (change) => {
      const env = environment();
      const connection = await env.transport.connect(env.request);
      connection.setMicrophoneEnabled(true);
      await settle();
      await env.voice(true);
      expect(env.media.enabled).toBe(true);
      vi.mocked(env.lease.renew).mockResolvedValue({
        connectionId,
        generation,
        expiresAt: env.lease.expiresAt,
        ...change,
      } as Awaited<ReturnType<VoxMediaLease["renew"]>>);
      await vi.advanceTimersByTimeAsync(30_000);
      await settle();
      expect(env.lease.renew).toHaveBeenCalledTimes(1);
      expect(env.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
      expect(env.media.enabled).toBe(false);
      expect(env.capture.stop).toHaveBeenCalled();
      expect(env.local.stop).toHaveBeenCalled();
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(env.room.disconnect).toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
      await connection.close();
      expect(env.lease.close).toHaveBeenCalledTimes(1);
    },
  );

  it("checks the initial JWT against its separate admission-token expiry", async () => {
    const env = environment();
    env.lease.tokenExpiresAt = Date.now() + 60_000;
    await expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    expect(env.loadSdk).not.toHaveBeenCalled();
  });

  it("extends the live lease without changing the room, epoch or original join token", async () => {
    const env = environment();
    const originalToken = env.lease.token;
    const deadline = env.lease.expiresAt + 30_000;
    vi.mocked(env.lease.renew).mockResolvedValue({
      connectionId,
      generation,
      expiresAt: deadline,
    });
    const connection = await env.transport.connect(env.request);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(env.lease.expiresAt).toBe(deadline);
    expect(env.lease.token).toBe(originalToken);
    expect(env.admission.join).toHaveBeenCalledTimes(1);
    expect(env.request.onEvent).not.toHaveBeenCalledWith({ type: "ended" });
    await connection.close();
  });

  it("keeps an admitted agent's audible reply active past two minutes and starts standby only after it stops", async () => {
    const env = environment();
    vi.mocked(env.lease.renew).mockImplementation(async () => ({
      connectionId,
      generation,
      expiresAt: Date.now() + 120_000,
    }));
    const controller = new VoxCallController({
      owner,
      transport: env.transport,
      newCallId: () => callId,
      now: () => Date.now(),
    });
    await controller.start();
    const remote = remoteAudio();
    env.room.events?.trackSubscribed(remote.track, agent);
    env.room.events?.speakersChanged([agent]);
    await settle();
    expect(controller.current().activity).toBe("speaking");
    await vi.advanceTimersByTimeAsync(VOX_STANDBY_AFTER_MS + 1_000);
    controller.tick();
    expect(controller.current().phase).toBe("active");
    env.room.events?.speakersChanged([]);
    expect(controller.current().activity).toBe("quiet");
    await vi.advanceTimersByTimeAsync(VOX_STANDBY_AFTER_MS - 1);
    controller.tick();
    expect(controller.current().phase).toBe("active");
    await vi.advanceTimersByTimeAsync(1);
    controller.tick();
    expect(controller.current().phase).toBe("standby");
    await controller.hangUp();
  });

  it("wakes the same conversation and queues the complete local question without publishing standby audio", async () => {
    let listener:
      | ((
          event: import("@navox/assistant-runtime/voice").WakeWordEvent,
        ) => void)
      | null = null;
    let wakeActive = false;
    const wakeAdapter = {
      id: "local-test",
      supported: true,
      get active() {
        return wakeActive;
      },
      start: vi.fn(
        async (
          notify: (
            event: import("@navox/assistant-runtime/voice").WakeWordEvent,
          ) => void,
        ) => {
          listener = notify;
          wakeActive = true;
        },
      ),
      stop: vi.fn(async () => {
        wakeActive = false;
      }),
    };
    const env = environment({ wakeFactory: () => wakeAdapter });
    env.lease.submitWakeText = vi.fn(async () => {});
    const resumedTrack = {
      ...env.media,
      enabled: false,
      readyState: "live",
      stop: vi.fn(),
    };
    const resumedCapture = {
      ...env.capture,
      track: resumedTrack as unknown as MediaStreamTrack,
    };
    vi.mocked(env.factory.create)
      .mockResolvedValueOnce(env.capture)
      .mockResolvedValueOnce(resumedCapture);
    env.sdk.audioTrack
      .mockReturnValueOnce(env.local as unknown as LocalAudioTrack)
      .mockReturnValueOnce({
        ...env.local,
        mediaStreamTrack: resumedCapture.track,
      } as unknown as LocalAudioTrack);
    vi.mocked(env.lease.renew).mockImplementation(async () => ({
      connectionId,
      generation,
      expiresAt: Date.now() + 120_000,
    }));
    const controller = new VoxCallController({
      owner,
      transport: env.transport,
      newCallId: () => callId,
      now: () => Date.now(),
    });
    await controller.start();
    await vi.advanceTimersByTimeAsync(VOX_STANDBY_AFTER_MS);
    controller.tick();
    await settle();
    expect(controller.current().phase).toBe("standby");
    expect(controller.current().wakeStatus).toBe("listening");
    expect(env.media.enabled).toBe(false);
    expect(env.capture.stop).toHaveBeenCalled();
    if (!listener) throw new Error("Local standby listener missing");
    const notify = listener as (
      event: import("@navox/assistant-runtime/voice").WakeWordEvent,
    ) => void;
    notify({
      type: "wake",
      confidence: 0.99,
      detected_at: new Date().toISOString(),
      device_id: "local-test",
      request_text: "What were we discussing?",
    });
    await settle();
    expect(controller.current()).toMatchObject({
      phase: "active",
      callId,
      wakeInputStatus: "queued",
    });
    expect(env.lease.submitWakeText).toHaveBeenCalledExactlyOnceWith(
      "What were we discussing?",
      expect.any(AbortSignal),
    );
    expect(env.admission.join).toHaveBeenCalledTimes(1);
    expect(wakeActive).toBe(false);
    await controller.hangUp();
    notify({
      type: "wake",
      confidence: 0.99,
      detected_at: new Date().toISOString(),
      device_id: "local-test",
    });
    expect(controller.current().phase).toBe("ended");
  });

  it("keeps a valid live epoch through a temporary heartbeat failure but closes on a rejected authority", async () => {
    const env = environment();
    vi.mocked(env.lease.renew)
      .mockRejectedValueOnce(new VoxReservationError("temporary"))
      .mockResolvedValueOnce({
        connectionId,
        generation,
        expiresAt: Date.now() + 150_000,
      });
    const connection = await env.transport.connect(env.request);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(env.lease.close).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(env.lease.renew).toHaveBeenCalledTimes(2);
    expect(env.lease.expiresAt).toBe(Date.now() + 118_000);
    vi.mocked(env.lease.renew).mockRejectedValue(
      new VoxReservationError("scope-changed"),
    );
    await vi.advanceTimersByTimeAsync(30_000);
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
    await connection.close();
  });

  it("cannot revive a retired epoch when its in-flight renewal returns late", async () => {
    const env = environment();
    const renewal = deferred<Awaited<ReturnType<VoxMediaLease["renew"]>>>();
    vi.mocked(env.lease.renew).mockReturnValue(renewal.promise);
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    await env.voice(true);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(env.lease.renew).toHaveBeenCalledTimes(1);
    await connection.close();
    renewal.resolve({
      connectionId,
      generation,
      expiresAt: env.lease.expiresAt,
    });
    await settle();
    connection.setMicrophoneEnabled(true);
    await settle();
    expect(env.media.enabled).toBe(false);
    expect(env.factory.create).toHaveBeenCalledTimes(1);
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    expect(env.room.connect).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
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
    expect(env.lease.close).toHaveBeenCalledTimes(1);
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
    expect(env.lease.close).toHaveBeenCalledTimes(1);
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
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    await connection.close();
    const expired = environment();
    const active = await expired.transport.connect(expired.request);
    expired.lease.expiresAt = Date.now() + 500;
    await vi.advanceTimersByTimeAsync(1000);
    expect(expired.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
    await active.close();
  });

  it("retires terminal room loss before requesting fresh admission for the same call", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    env.room.events?.disconnected();
    await settle();
    expect(env.request.onEvent).toHaveBeenCalledTimes(1);
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "disconnected" });
    expect(env.room.events).toBeNull();
    expect(env.lease.close).toHaveBeenCalledTimes(1);
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

  it("stops capture immediately while offline and retires it after SDK recovery", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    await env.voice(true);
    env.room.isConnected.mockReturnValue(false);
    env.room.events?.reconnecting();
    expect(env.media.enabled).toBe(false);
    expect(env.capture.stop).toHaveBeenCalled();
    await settle();
    expect(env.room.unpublishAudio).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(env.request.onEvent).not.toHaveBeenCalledWith({ type: "ended" });
    expect(env.room.unpublishAudio).not.toHaveBeenCalled();
    env.room.isConnected.mockReturnValue(true);
    env.room.events?.reconnected();
    await settle();
    expect(env.room.unpublishAudio).toHaveBeenCalledTimes(1);
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "reconnected" });
    await connection.close();
  });

  it("defers removal on browser offline before SDK notification and keeps auth gates closed", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    vi.mocked(env.request.mayTransmitAudio).mockReturnValue(false);
    connection.setMicrophoneEnabled(false);
    await settle();
    expect(env.capture.stop).toHaveBeenCalled();
    expect(env.room.unpublishAudio).not.toHaveBeenCalled();
    online.mockReturnValue(true);
    env.room.isConnected.mockReturnValue(false);
    window.dispatchEvent(new Event("online"));
    await settle();
    expect(env.room.unpublishAudio).not.toHaveBeenCalled();
    env.room.isConnected.mockReturnValue(true);
    env.room.events?.reconnected();
    connection.setMicrophoneEnabled(true);
    await settle();
    expect(env.room.unpublishAudio).toHaveBeenCalledTimes(1);
    expect(env.factory.create).toHaveBeenCalledTimes(1);
    expect(env.media.enabled).toBe(false);
    await connection.close();
  });

  it("expires while signalling is down and cannot revive capture on a late online event", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    env.request.mayRenewReservation = () => false;
    env.room.isConnected.mockReturnValue(false);
    env.room.events?.reconnecting();
    await vi.advanceTimersByTimeAsync(120_001);
    await settle();
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    env.room.isConnected.mockReturnValue(true);
    window.dispatchEvent(new Event("online"));
    connection.setMicrophoneEnabled(true);
    await settle();
    expect(env.factory.create).toHaveBeenCalledTimes(1);
    expect(env.media.enabled).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([false, true])(
    "whole-room cleanup supersedes deferred removal; disconnect failure=%s still blocks replacement",
    async (disconnectFails) => {
      const env = environment();
      const connection = await env.transport.connect(env.request);
      connection.setMicrophoneEnabled(true);
      await settle();
      env.room.isConnected.mockReturnValue(false);
      env.room.events?.reconnecting();
      if (disconnectFails)
        env.room.disconnect.mockRejectedValue(
          new Error("synthetic cleanup failure"),
        );
      const closing = connection.close();
      if (disconnectFails) await expect(closing).rejects.toThrow("unavailable");
      else await closing;
      expect(env.room.unpublishAudio).not.toHaveBeenCalled();
      expect(env.room.disconnect).toHaveBeenCalledTimes(1);
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(env.transport.available).toBe(!disconnectFails);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

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
      if (failure === "unpublish") {
        // A genuine in-call removal failure must remain quarantined.
        connection.setMicrophoneEnabled(false);
        await settle();
      }
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

  it("retires the media lease once across duplicate close, abort and disconnect callbacks", async () => {
    const env = environment();
    const connection = await env.transport.connect(env.request);
    const disconnected = env.room.events?.disconnected;
    const first = connection.close();
    expect(connection.close()).toBe(first);
    env.abort.abort();
    disconnected?.();
    await first;
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    expect(env.room.disconnect).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("waits for media cleanup before allowing replacement admission", async () => {
    const env = environment();
    const cleanup = deferred<void>();
    vi.mocked(env.lease.close).mockReturnValue(cleanup.promise);
    const connection = await env.transport.connect(env.request);
    const closing = connection.close();
    expect(env.transport.available).toBe(false);
    await expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    expect(env.admission.join).toHaveBeenCalledTimes(1);
    cleanup.resolve();
    await closing;
    expect(env.transport.available).toBe(true);
    expect(env.lease.close).toHaveBeenCalledTimes(1);
  });

  it.each(["resolve", "reject"] as const)(
    "blocks replacement after abort until pending admission and its %s cleanup finish",
    async (outcome) => {
      const env = environment();
      const admission = deferred<VoxMediaLease>();
      const cleanup = deferred<void>();
      env.admission.join.mockReturnValue(admission.promise);
      vi.mocked(env.lease.close).mockReturnValue(cleanup.promise);
      const pending = expect(
        env.transport.connect(env.request),
      ).rejects.toThrow("unavailable");
      env.abort.abort();
      await settle();
      await expect(
        env.transport.connect(environment().request),
      ).rejects.toThrow("unavailable");
      expect(env.admission.join).toHaveBeenCalledTimes(1);
      admission.resolve(env.lease);
      await settle();
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(env.transport.available).toBe(false);
      await expect(
        env.transport.connect(environment().request),
      ).rejects.toThrow("unavailable");
      expect(env.admission.join).toHaveBeenCalledTimes(1);
      if (outcome === "resolve") cleanup.resolve();
      else cleanup.reject(new Error("synthetic unknown cleanup"));
      await pending;
      expect(env.transport.available).toBe(outcome === "resolve");
      expect(env.loadSdk).not.toHaveBeenCalled();
      expect(env.lease.close).toHaveBeenCalledTimes(1);
    },
  );

  it("quarantines an aborted admission rejection with no cleanup handle", async () => {
    const env = environment();
    const admission = deferred<VoxMediaLease>();
    env.admission.join.mockReturnValue(admission.promise);
    const pending = expect(env.transport.connect(env.request)).rejects.toThrow(
      "unavailable",
    );
    env.abort.abort();
    await settle();
    admission.reject(new Error("synthetic admission uncertainty"));
    await pending;
    expect(env.transport.available).toBe(false);
    await expect(env.transport.connect(environment().request)).rejects.toThrow(
      "unavailable",
    );
    expect(env.admission.join).toHaveBeenCalledTimes(1);
    expect(env.loadSdk).not.toHaveBeenCalled();
  });

  it.each([false, true])(
    "terminal close supersedes immediate microphone removal; disconnect failure=%s remains fenced",
    async (disconnectFails) => {
      const env = environment();
      const connection = await env.transport.connect(env.request);
      connection.setMicrophoneEnabled(true);
      await settle();
      const removing = deferred<void>();
      env.room.unpublishAudio.mockReturnValue(removing.promise);
      env.room.disconnect.mockImplementation(async () => {
        removing.reject(
          new Error("synthetic negotiation cancelled by disconnect"),
        );
        if (disconnectFails) throw new Error("synthetic disconnect failure");
      });
      // Match the controller: revoke capture, then close in the same turn.
      connection.setMicrophoneEnabled(false);
      const closing = connection.close();
      if (disconnectFails) await expect(closing).rejects.toThrow("unavailable");
      else await closing;
      // Observe the unused rejection when no per-track negotiation was started.
      await removing.promise.catch(() => {});
      expect(env.room.unpublishAudio).not.toHaveBeenCalled();
      expect(env.media.readyState).toBe("ended");
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(env.room.disconnect).toHaveBeenCalledTimes(1);
      expect(env.transport.available).toBe(!disconnectFails);
    },
  );

  it("blocks replacement until a late capture publication retires", async () => {
    const env = environment();
    const publication = deferred<void>();
    env.room.publishAudio.mockReturnValue(publication.promise);
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    const closing = connection.close();
    await settle();
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    expect(env.transport.available).toBe(false);
    await expect(env.transport.connect(environment().request)).rejects.toThrow(
      "unavailable",
    );
    expect(env.admission.join).toHaveBeenCalledTimes(1);
    publication.resolve();
    await closing;
    expect(env.transport.available).toBe(true);
    expect(env.media.enabled).toBe(false);
    expect(env.room.unpublishAudio).not.toHaveBeenCalled();
    expect(env.room.disconnect).toHaveBeenCalledTimes(1);
  });

  it.each(["load", "connect"] as const)(
    "retires admitted media after %s failure without exposing provider details",
    async (failure) => {
      const env = environment();
      const error = new Error("synthetic private media details");
      if (failure === "load") env.loadSdk.mockRejectedValue(error);
      else env.room.connect.mockRejectedValue(error);
      await expect(env.transport.connect(env.request)).rejects.toThrow(
        "The Vox media connection is unavailable.",
      );
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(env.factory.create).not.toHaveBeenCalled();
      expect(env.transport.available).toBe(true);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("retires admitted media when capture fails after connection", async () => {
    const env = environment();
    vi.mocked(env.factory.create).mockRejectedValue(
      new Error("synthetic microphone failure"),
    );
    const connection = await env.transport.connect(env.request);
    connection.setMicrophoneEnabled(true);
    await settle();
    expect(env.request.onEvent).toHaveBeenCalledWith({ type: "ended" });
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    expect(env.room.disconnect).toHaveBeenCalled();
    expect(env.room.publishAudio).not.toHaveBeenCalled();
    await connection.close();
    expect(env.lease.close).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(["resolve", "reject"] as const)(
    "still retires an aborted late admission when its media cleanup will %s",
    async (outcome) => {
      const env = environment();
      const admission = deferred<VoxMediaLease>();
      env.admission.join.mockReturnValue(admission.promise);
      if (outcome === "reject")
        vi.mocked(env.lease.close).mockRejectedValue(
          new Error("synthetic stale authority"),
        );
      const pending = expect(
        env.transport.connect(env.request),
      ).rejects.toThrow("unavailable");
      env.abort.abort();
      await settle();
      admission.resolve(env.lease);
      await pending;
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(env.loadSdk).not.toHaveBeenCalled();
      expect(env.transport.available).toBe(outcome === "resolve");
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it.each([
    "lease-reject",
    "lease-throw",
    "unlisten",
    "capture-disable",
    "capture-stop",
    "local-stop",
    "audio-pause",
    "audio-detach",
  ] as const)(
    "continues physical cleanup and quarantines admission after %s failure",
    async (failure) => {
      const env = environment();
      const connection = await env.transport.connect(env.request);
      connection.setMicrophoneEnabled(true);
      connection.setSpeakerEnabled(true);
      await settle();
      await env.voice(true);
      const audio = remoteAudio();
      env.room.events?.trackSubscribed(audio.track, agent);
      await settle();
      const error = new Error("synthetic private cleanup details");
      const throws = () => {
        throw error;
      };
      if (failure === "lease-reject")
        vi.mocked(env.lease.close).mockRejectedValue(error);
      else if (failure === "lease-throw")
        vi.mocked(env.lease.close).mockImplementation(throws);
      else if (failure === "unlisten")
        env.room.unlisten.mockImplementation(throws);
      else if (failure === "capture-disable")
        vi.mocked(env.capture.setEnabled).mockImplementation(throws);
      else if (failure === "capture-stop")
        vi.mocked(env.capture.stop).mockImplementation(throws);
      else if (failure === "local-stop")
        env.local.stop.mockImplementation(throws);
      else if (failure === "audio-pause")
        vi.mocked(audio.element.pause).mockImplementation(throws);
      else audio.track.detach.mockImplementation(throws);
      const closing = connection.close();
      expect(env.media.enabled).toBe(false);
      expect(env.capture.setEnabled).toHaveBeenCalledWith(false);
      expect(env.capture.stop).toHaveBeenCalled();
      expect(env.local.stop).toHaveBeenCalled();
      expect(audio.element.muted).toBe(true);
      expect(audio.element.pause).toHaveBeenCalled();
      expect(audio.element.srcObject).toBeNull();
      expect(env.room.unlisten).toHaveBeenCalled();
      expect(env.room.disconnect).toHaveBeenCalled();
      await expect(closing).rejects.toThrow(
        "The Vox media connection is unavailable.",
      );
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      expect(env.transport.available).toBe(false);
      await expect(connection.close()).rejects.toThrow("unavailable");
      await expect(env.transport.connect(env.request)).rejects.toThrow(
        "unavailable",
      );
      expect(env.admission.join).toHaveBeenCalledTimes(1);
      expect(env.lease.close).toHaveBeenCalledTimes(1);
      // Flush jsdom's media-element tasks; no adapter interval may remain.
      await vi.advanceTimersByTimeAsync(0);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("retains the same SDK room through bounded signal reconnect and cleans it only on close", async () => {
    const env = environment();
    const listeners = new Map<string, (...args: unknown[]) => void>();
    const connect = vi.fn(async () => {});
    const disconnect = vi.fn(async () => {});
    const Room = vi.fn(
      class {
        constructor(
          readonly options: {
            reconnectPolicy: {
              nextRetryDelayInMs(context: { elapsedMs: number }): number | null;
            };
          },
        ) {}
        name = "navox-room";
        state = "connected";
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
      ConnectionState: { Connected: "connected" },
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
        Reconnected: "reconnected",
        ActiveSpeakersChanged: "activeSpeakersChanged",
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
        Room.mock.calls[0][0].reconnectPolicy.nextRetryDelayInMs({
          elapsedMs: 0,
        }),
      ).toBe(250);
      expect(
        Room.mock.calls[0][0].reconnectPolicy.nextRetryDelayInMs({
          elapsedMs: 90_000,
        }),
      ).toBeNull();
      expect(listeners.has("signalReconnecting")).toBe(true);
      listeners.get("signalReconnecting")?.();
      await settle();
      expect(env.request.onEvent).toHaveBeenCalledWith({
        type: "reconnecting",
      });
      expect(disconnect).not.toHaveBeenCalled();
      listeners.get("reconnected")?.();
      expect(env.request.onEvent).toHaveBeenCalledWith({ type: "reconnected" });
      expect(connect).toHaveBeenCalledTimes(1);
      await connection.close();
      expect(disconnect).toHaveBeenCalledTimes(1);
      expect(listeners.size).toBe(0);
    } finally {
      vi.doUnmock("livekit-client");
    }
  });
});

describe("bounded Vox provider grants", () => {
  it.each([
    { exp: 1_800_000_121 },
    { nbf: 1_800_000_060 },
    { nbf: undefined },
    { video: { canPublishSources: ["microphone", "camera"] } },
    { video: { canPublishData: true } },
    { video: { canUpdateOwnMetadata: true } },
    { video: { roomAdmin: true } },
  ])("rejects broader or future provider claims %j", async (change) => {
    const env = environment();
    const original = JSON.parse(atob(env.lease.token.split(".")[1]));
    env.lease.token = token({
      ...change,
      ...(change.video
        ? { video: { ...original.video, ...change.video } }
        : {}),
    });
    await expect(env.transport.connect(env.request)).rejects.toThrow();
    expect(env.loadSdk).not.toHaveBeenCalled();
    expect(env.lease.close).toHaveBeenCalledTimes(1);
  });
  it("constructs wake for one admitted lease and reads its configuration only after standby begins", async () => {
    let captured: VoxWakeBinding | null = null;
    let active = false;
    const wakeFactory = vi.fn<VoxWakeFactory>((binding) => {
      captured = binding;
      return {
        id: "configured-local",
        supported: true,
        get active() {
          return active;
        },
        start: async () => {
          await binding.loadConfig(new AbortController().signal);
          active = true;
        },
        stop: async () => {
          active = false;
        },
      };
    });
    const env = environment({ wakeFactory });
    const load = vi.fn(async () => null);
    env.lease.loadWakeConfig = load;
    expect(wakeFactory).not.toHaveBeenCalled();
    const connection = await env.transport.connect(env.request);
    expect(wakeFactory).toHaveBeenCalledTimes(1);
    expect(load).not.toHaveBeenCalled();
    env.request.mayListenForWake = () => true;
    connection.setWakeEnabled(true);
    await settle();
    expect(load).toHaveBeenCalledTimes(1);
    await connection.close();
    await expect(
      (captured as VoxWakeBinding | null)?.loadConfig(
        new AbortController().signal,
      ),
    ).rejects.toThrow("unavailable");
    expect(load).toHaveBeenCalledTimes(1);
  });
});

it("accepts web results only from the admitted agent and current owned lease", async () => {
  const env = environment();
  env.request.mayRenewReservation = vi.fn(() => true);
  const connection = await env.transport.connect(env.request);
  const data = new TextEncoder().encode(
    JSON.stringify({ status: "searching" }),
  );
  const receive = env.room.events?.dataReceived;
  if (!receive) throw new Error("Expected a room data handler");
  receive(data, "someone-else", "navox.vox.web.v1");
  receive(data, agent, "wrong-topic");
  expect(env.request.onEvent).not.toHaveBeenCalledWith(
    expect.objectContaining({ type: "web-lookup" }),
  );
  receive(data, agent, "navox.vox.web.v1");
  expect(env.request.onEvent).toHaveBeenCalledWith({
    type: "web-lookup",
    result: { status: "searching" },
  });
  vi.mocked(env.request.onEvent).mockClear();
  env.request.mayRenewReservation = () => false;
  receive(data, agent, "navox.vox.web.v1");
  expect(env.request.onEvent).not.toHaveBeenCalled();
  await connection.close();
  vi.mocked(env.request.onEvent).mockClear();
  receive(data, agent, "navox.vox.web.v1");
  expect(env.request.onEvent).not.toHaveBeenCalled();
});

it("keeps admission usable after explicit budget denial without claiming cleanup for unknown errors", async () => {
  const env = environment();
  env.admission.join.mockRejectedValueOnce(
    new VoxReservationError("allowance-exhausted"),
  );
  await expect(env.transport.connect(env.request)).rejects.toMatchObject({
    code: "allowance-exhausted",
  });
  expect(env.loadSdk).not.toHaveBeenCalled();
  expect(env.factory.create).not.toHaveBeenCalled();
  expect(env.transport.available).toBe(true);
  const connection = await env.transport.connect(env.request);
  expect(env.admission.join).toHaveBeenCalledTimes(2);
  await connection.close();
});

it("accepts plan reviews only from the current agent, topic and owned lease", async () => {
  const env = environment();
  env.request.mayRenewReservation = () => true;
  const connection = await env.transport.connect(env.request);
  const result = {
    kind: "study-plan",
    preview_id: "11111111-1111-4111-8111-111111111111",
    preview_hash: "a".repeat(64),
    timezone: "America/New_York",
  };
  const data = new TextEncoder().encode(JSON.stringify(result));
  const receive = env.room.events?.dataReceived;
  if (!receive) throw new Error("Expected a data handler");
  vi.mocked(env.request.onEvent).mockClear();
  receive(data, "other-agent", "navox.vox.plan-review.v1");
  receive(data, agent, "wrong-topic");
  receive(
    new TextEncoder().encode(
      JSON.stringify({ ...result, timezone: "invented-zone" }),
    ),
    agent,
    "navox.vox.plan-review.v1",
  );
  expect(env.request.onEvent).not.toHaveBeenCalled();
  receive(data, agent, "navox.vox.plan-review.v1");
  expect(env.request.onEvent).toHaveBeenCalledWith({
    type: "plan-review",
    result,
  });
  vi.mocked(env.request.onEvent).mockClear();
  env.request.mayRenewReservation = () => false;
  receive(data, agent, "navox.vox.plan-review.v1");
  expect(env.request.onEvent).not.toHaveBeenCalled();
  await connection.close();
  vi.mocked(env.request.onEvent).mockClear();
  receive(data, agent, "navox.vox.plan-review.v1");
  expect(env.request.onEvent).not.toHaveBeenCalled();
});
