import type { WakeWordAdapter } from "@navox/assistant-runtime/voice";
import type { LocalAudioTrack, RemoteTrack } from "livekit-client";
import { VoxActivityTracker } from "./vox-activity";
import {
  parseVoxCalendarReview,
  VOX_CALENDAR_REVIEW_TOPIC,
} from "./vox-calendar-review";
import type {
  VoxConnection,
  VoxOwner,
  VoxTransport,
  VoxTransportRequest,
} from "./vox-call";
import { VoxStandbyWake, type VoxWakeFactory } from "./vox-local-wake";
import {
  parseVoxMemoryReview,
  VOX_MEMORY_REVIEW_TOPIC,
} from "./vox-memory-review";
import {
  parseVoxMemorySaveReview,
  VOX_MEMORY_SAVE_REVIEW_TOPIC,
} from "./vox-memory-save-review";
import { parseVoxPlanReview } from "./vox-plan-review";
import type { PorcupineWakeConfig } from "./vox-porcupine-wake";
import { VoxReservationError } from "./vox-reservations";
import { parseVoxWebLookup } from "./vox-web-results";

export interface VoxMediaAdmission {
  available: boolean;
  join(request: VoxTransportRequest): Promise<VoxMediaLease>;
}

export interface VoxMediaProof {
  connectionId: string;
  generation: number;
  expiresAt: number;
}

export interface VoxMediaLease extends VoxMediaProof {
  owner: VoxOwner;
  callId: string;
  roomName: string;
  participantIdentity: string;
  agentIdentity: string;
  serverUrl: string;
  token: string;
  /** Initial join JWT lifetime; renewal never creates another JWT. */
  tokenExpiresAt?: number;
  renew(signal: AbortSignal): Promise<VoxMediaProof>;
  /** Private licensed configuration is read only when this connection enters standby. */
  loadWakeConfig?(signal: AbortSignal): Promise<PorcupineWakeConfig | null>;
  /** Explicit local wake suffix, scoped to the same owned connection and conversation. */
  submitWakeText?(text: string, signal: AbortSignal): Promise<void>;
  /** Retire this media connection only; the app owner ends its reservation. */
  close(): Promise<void>;
}

export interface VoxAudioCapture {
  /** Locally VAD-gated processed audio, initially disabled; never the raw microphone track. */
  track: MediaStreamTrack;
  /** Must synchronously silence raw/processed output independently of SDK track.enabled. */
  setEnabled(enabled: boolean): void;
  stop(): void;
}

export interface VoxAudioCaptureFactory {
  /** Qualification requires a real on-device VAD and synchronous fail-closed gates. */
  qualified: boolean;
  create(options: {
    signal: AbortSignal;
    /** The local processor must check this before every output frame. */
    mayCapture(): boolean;
    onVoiceActivity(speech: boolean): void | Promise<void>;
  }): Promise<VoxAudioCapture>;
}

export interface VoxRemoteAudio {
  kind: string;
  attach(): HTMLMediaElement;
  detach(): HTMLMediaElement[];
}

export interface VoxRoomEvents {
  dataReceived?(data: Uint8Array, identity: string, topic?: string): void;
  participantJoined(identity: string): void;
  participantLeft(identity: string): void;
  trackPublished(identity: string): void;
  trackSubscribed(track: VoxRemoteAudio, identity: string): void;
  trackUnsubscribed(track: VoxRemoteAudio): void;
  playbackChanged(allowed: boolean): void;
  speakersChanged(identities: readonly string[]): void;
  reconnecting(): void;
  reconnected(): void;
  disconnected(): void;
}

/** Narrow test seam; the production implementation below uses the actual SDK. */
export interface VoxLiveKitRoom {
  name(): string;
  isConnected(): boolean;
  identity(): string;
  hasParticipant(identity: string): boolean;
  connect(url: string, token: string): Promise<void>;
  disconnect(): Promise<void>;
  startAudio(): Promise<void>;
  listen(events: VoxRoomEvents): () => void;
  subscribeAgent(identity: string): void;
  publishAudio(track: LocalAudioTrack, agentIdentity: string): Promise<void>;
  unpublishAudio(track: LocalAudioTrack): Promise<void>;
}

export interface VoxLiveKitSDK {
  room(): VoxLiveKitRoom;
  audioTrack(track: MediaStreamTrack): LocalAudioTrack;
}

async function loadLiveKit(): Promise<VoxLiveKitSDK> {
  // This import is reached only by an explicit, admitted connect. Never prewarm
  // a room, enumerate devices, open a microphone, or load a capture model on mount.
  const sdk = await import("livekit-client");
  sdk.setLogLevel(sdk.LogLevel.silent);
  return {
    audioTrack: (track) => new sdk.LocalAudioTrack(track, undefined, true),
    room() {
      const room = new sdk.Room({
        adaptiveStream: false,
        dynacast: false,
        reconnectPolicy: {
          nextRetryDelayInMs: ({ elapsedMs }: { elapsedMs: number }) =>
            elapsedMs >= 90_000
              ? null
              : Math.min(5_000, Math.max(250, elapsedMs / 4)),
        },
      });
      const tracks = new WeakMap<RemoteTrack, VoxRemoteAudio>();
      const remote = (track: RemoteTrack): VoxRemoteAudio => {
        let wrapped = tracks.get(track);
        if (!wrapped) {
          wrapped = {
            kind: track.kind,
            attach: () => track.attach(),
            detach: () => track.detach(),
          };
          tracks.set(track, wrapped);
        }
        return wrapped;
      };
      return {
        name: () => room.name,
        isConnected: () => room.state === sdk.ConnectionState.Connected,
        identity: () => room.localParticipant.identity,
        hasParticipant: (identity) => room.remoteParticipants.has(identity),
        connect: (url, token) =>
          room.connect(url, token, { autoSubscribe: false }),
        disconnect: () => room.disconnect(true),
        startAudio: () => room.startAudio(),
        subscribeAgent(identity) {
          const agent = room.remoteParticipants.get(identity);
          for (const publication of agent?.trackPublications.values() ?? []) {
            if (publication.kind === sdk.Track.Kind.Audio)
              publication.setSubscribed(true);
          }
        },
        async publishAudio(track, agentIdentity) {
          room.localParticipant.setTrackSubscriptionPermissions(false, [
            { participantIdentity: agentIdentity, allowAll: true },
          ]);
          await room.localParticipant.publishTrack(track, {
            source: sdk.Track.Source.Microphone,
          });
        },
        async unpublishAudio(track) {
          await room.localParticipant.unpublishTrack(track, true);
        },
        listen(events) {
          const dataReceived = (
            data: Uint8Array,
            participant?: { identity: string },
            _kind?: unknown,
            topic?: string,
          ) => events.dataReceived?.(data, participant?.identity ?? "", topic);
          room.on(sdk.RoomEvent.DataReceived, dataReceived);
          const joined = (participant: { identity: string }) =>
            events.participantJoined(participant.identity);
          const left = (participant: { identity: string }) =>
            events.participantLeft(participant.identity);
          const published = (_: unknown, participant: { identity: string }) =>
            events.trackPublished(participant.identity);
          const subscribed = (
            track: RemoteTrack,
            _: unknown,
            participant: { identity: string },
          ) => events.trackSubscribed(remote(track), participant.identity);
          const unsubscribed = (track: RemoteTrack) =>
            events.trackUnsubscribed(remote(track));
          const speakers = (participants: readonly { identity: string }[]) =>
            events.speakersChanged(
              participants.map((participant) => participant.identity),
            );
          room.on(sdk.RoomEvent.ActiveSpeakersChanged, speakers);
          room.on(sdk.RoomEvent.ParticipantConnected, joined);
          room.on(sdk.RoomEvent.ParticipantDisconnected, left);
          room.on(sdk.RoomEvent.TrackPublished, published);
          room.on(sdk.RoomEvent.TrackSubscribed, subscribed);
          room.on(sdk.RoomEvent.TrackUnsubscribed, unsubscribed);
          room.on(sdk.RoomEvent.Disconnected, events.disconnected);
          room.on(sdk.RoomEvent.Reconnecting, events.reconnecting);
          room.on(sdk.RoomEvent.SignalReconnecting, events.reconnecting);
          room.on(sdk.RoomEvent.Reconnected, events.reconnected);
          room.on(
            sdk.RoomEvent.AudioPlaybackStatusChanged,
            events.playbackChanged,
          );
          return () => {
            room.off(sdk.RoomEvent.DataReceived, dataReceived);
            room.off(sdk.RoomEvent.ActiveSpeakersChanged, speakers);
            room.off(sdk.RoomEvent.ParticipantConnected, joined);
            room.off(sdk.RoomEvent.ParticipantDisconnected, left);
            room.off(sdk.RoomEvent.TrackPublished, published);
            room.off(sdk.RoomEvent.TrackSubscribed, subscribed);
            room.off(sdk.RoomEvent.TrackUnsubscribed, unsubscribed);
            room.off(sdk.RoomEvent.Disconnected, events.disconnected);
            room.off(sdk.RoomEvent.Reconnecting, events.reconnecting);
            room.off(sdk.RoomEvent.SignalReconnecting, events.reconnecting);
            room.off(sdk.RoomEvent.Reconnected, events.reconnected);
            room.off(
              sdk.RoomEvent.AudioPlaybackStatusChanged,
              events.playbackChanged,
            );
          };
        },
      };
    },
  };
}

const uuid =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const identity = /^[a-zA-Z0-9:_-]{1,200}$/;
const unavailable = () => new Error("The Vox media connection is unavailable.");
const sameOwner = (a: VoxOwner, b: VoxOwner) =>
  a.userId === b.userId && a.workspaceId === b.workspaceId;

function validLease(
  lease: VoxMediaLease,
  request: VoxTransportRequest,
  now: number,
) {
  const url = new URL(lease.serverUrl);
  if (
    !sameOwner(lease.owner, request.owner) ||
    lease.callId !== request.callId ||
    !uuid.test(lease.connectionId) ||
    !Number.isInteger(lease.generation) ||
    lease.generation < 0 ||
    lease.generation > 2147483647 ||
    !identity.test(lease.roomName) ||
    !identity.test(lease.participantIdentity) ||
    !identity.test(lease.agentIdentity) ||
    lease.participantIdentity === lease.agentIdentity ||
    typeof lease.serverUrl !== "string" ||
    lease.serverUrl.length > 2048 ||
    url.protocol !== "wss:" ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    typeof lease.token !== "string" ||
    lease.token.length > 16_384 ||
    !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(lease.token) ||
    !Number.isFinite(lease.expiresAt) ||
    lease.expiresAt <= now ||
    lease.expiresAt > now + 120_000 ||
    !Number.isFinite(lease.tokenExpiresAt ?? lease.expiresAt) ||
    (lease.tokenExpiresAt ?? lease.expiresAt) <= now ||
    (lease.tokenExpiresAt ?? lease.expiresAt) > now + 120_000 ||
    typeof lease.renew !== "function" ||
    typeof lease.close !== "function"
  )
    throw unavailable();
  // Defense in depth, not JWT signature verification. The server admission and
  // LiveKit verify authority; the browser only rejects a mismatched room/identity.
  const claims: unknown = JSON.parse(
    atob(lease.token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/")),
  );
  if (!claims || typeof claims !== "object") throw unavailable();
  const value = claims as Record<string, unknown>;
  const video = value.video as Record<string, unknown> | undefined;
  const metadata: unknown =
    typeof value.metadata === "string" ? JSON.parse(value.metadata) : null;
  if (
    !metadata ||
    typeof metadata !== "object" ||
    (metadata as Record<string, unknown>).connection_id !==
      lease.connectionId ||
    (metadata as Record<string, unknown>).generation !== lease.generation ||
    value.sub !== lease.participantIdentity ||
    typeof value.exp !== "number" ||
    !Number.isFinite(value.exp) ||
    value.exp * 1000 <= now ||
    value.exp * 1000 > (lease.tokenExpiresAt ?? lease.expiresAt) ||
    typeof value.nbf !== "number" ||
    !Number.isFinite(value.nbf) ||
    value.nbf * 1000 > now ||
    !video ||
    video.room !== lease.roomName ||
    video.roomJoin !== true ||
    video.canPublishData !== false ||
    video.canPublish !== true ||
    video.canSubscribe !== true ||
    video.canUpdateOwnMetadata !== false ||
    !Array.isArray(video.canPublishSources) ||
    video.canPublishSources.length !== 1 ||
    video.canPublishSources[0] !== "microphone" ||
    Object.keys(video).some(
      (key) =>
        ![
          "room",
          "roomJoin",
          "canPublish",
          "canPublishSources",
          "canSubscribe",
          "canPublishData",
          "canUpdateOwnMetadata",
        ].includes(key),
    )
  )
    throw unavailable();
}

export function createLiveKitVoxTransport(options: {
  admission: VoxMediaAdmission;
  capture?: VoxAudioCaptureFactory;
  loadSdk?: () => Promise<VoxLiveKitSDK>;
  wakeFactory?: VoxWakeFactory;
  now?: () => number;
}): VoxTransport {
  // A rejected join must not permit a replacement if its resource teardown was
  // uncertain. The app must replace this adapter only after resolving cleanup.
  let wakeAdapter: WakeWordAdapter | null = null;
  let wakeUnavailable = false;
  let cleanupBlocked = false;
  let pendingCleanup = 0;
  let pendingConnections = 0;
  return {
    get available() {
      return (
        !cleanupBlocked &&
        pendingCleanup === 0 &&
        options.admission.available &&
        options.capture?.qualified === true
      );
    },
    get localWakeAvailable() {
      return wakeAdapter?.supported === true && !wakeUnavailable;
    },
    async connect(request): Promise<VoxConnection> {
      const captureFactory = options.capture;
      if (
        cleanupBlocked ||
        pendingCleanup !== 0 ||
        pendingConnections !== 0 ||
        !options.admission.available ||
        captureFactory?.qualified !== true ||
        request.signal.aborted ||
        !uuid.test(request.owner.userId) ||
        !uuid.test(request.owner.workspaceId) ||
        !uuid.test(request.callId)
      )
        throw unavailable();
      const owner = Object.freeze({ ...request.owner });
      const callId = request.callId;
      const now = options.now ?? Date.now;
      const abort = new AbortController();
      let lease: VoxMediaLease | null = null;
      let room: VoxLiveKitRoom | null = null;
      let closed = false;
      let joined = false;
      let microphone = false;
      let speaker = false;
      let speech = false;
      let agentSpeaking = false;
      let wake: VoxStandbyWake | null = null;
      let activity: VoxActivityTracker | null = null;
      let capture: VoxAudioCapture | null = null;
      let local: LocalAudioTrack | null = null;
      let captureEpoch = 0;
      let creating: Promise<void> | null = null;
      let upstream = Promise.resolve();
      let retiring = Promise.resolve();
      let closing: Promise<void> | null = null;
      let leaseClosing: Promise<void> | null = null;
      let cleanupFailed = false;
      let admissionStarted = false;
      let unlisten = () => {};
      let timer: ReturnType<typeof setInterval> | null = null;
      let agentTimer: ReturnType<typeof setTimeout> | null = null;
      let agentReady: (() => void) | null = null;
      let agentFailed: (() => void) | null = null;
      let renewing = false;
      let renewedAt = now();
      const remoteTracks = new Set<VoxRemoteAudio>();
      type Attachment = {
        element: HTMLMediaElement;
        epoch: number;
        playing: boolean;
      };
      const elements = new Map<VoxRemoteAudio, Attachment>();
      let playbackEpoch = 0;
      const cleanup = (operation: () => void) => {
        try {
          operation();
        } catch {
          cleanupFailed = cleanupBlocked = true;
        }
      };
      const attempt = (operation: () => unknown): Promise<unknown> => {
        try {
          return Promise.resolve(operation());
        } catch {
          return Promise.reject(unavailable());
        }
      };
      const closeMediaLease = (): Promise<void> => {
        if (!lease) return Promise.resolve();
        if (!leaseClosing) {
          const captured = lease;
          ++pendingCleanup;
          // Install the promise before calling the adapter: cleanup may throw
          // synchronously or reenter close, but this epoch is retired once.
          leaseClosing = Promise.resolve()
            .then(() => captured.close())
            .catch(() => {
              cleanupFailed = cleanupBlocked = true;
              throw unavailable();
            })
            .finally(() => {
              --pendingCleanup;
            });
        }
        return leaseClosing;
      };
      const visible = () =>
        typeof document === "undefined" ||
        document.visibilityState === "visible";
      const fresh = () =>
        !closed &&
        !cleanupBlocked &&
        options.admission.available &&
        captureFactory.qualified &&
        !request.signal.aborted &&
        sameOwner(owner, request.owner) &&
        callId === request.callId &&
        (!lease || lease.expiresAt > now());
      const mayCapture = () =>
        fresh() &&
        joined &&
        microphone &&
        visible() &&
        request.mayTransmitAudio();
      const mayPublish = () => mayCapture() && speech;
      const disableOutbound = () => {
        if (capture) capture.track.enabled = false;
        if (local) local.mediaStreamTrack.enabled = false;
      };
      // Local capture is revoked synchronously. Signalling a track removal while
      // offline can itself fail LiveKit negotiation and destroy a recoverable call.
      const signallingWaiters = new Set<() => void>();
      const canSignal = () =>
        (typeof navigator === "undefined" || navigator.onLine !== false) &&
        room?.isConnected() === true;
      const resumeSignalling = () => {
        if (!closed && !canSignal()) return;
        for (const resume of signallingWaiters) resume();
        signallingWaiters.clear();
      };
      const retireTrack = async (track: LocalAudioTrack) => {
        // The controller revokes capture immediately before closing the room.
        // Yield once so terminal close can supersede per-track negotiation;
        // otherwise SDK disconnect can cancel that negotiation and falsely
        // quarantine a fully retired call. Physical capture is already stopped.
        await Promise.resolve();
        if (closed) return;
        if (!canSignal()) {
          if (!closed)
            await new Promise<void>((resolve) =>
              signallingWaiters.add(resolve),
            );
          // Closing the whole room supersedes a deferred per-track removal.
          // close() still requires successful SDK disconnect and server lease close.
          if (closed) return;
        }
        await room?.unpublishAudio(track);
      };
      const silence = () => {
        ++captureEpoch;
        const retiredCapture = capture;
        const retired = local;
        // Revocation precedes callbacks: the real local processor reports quiet
        // synchronously while stopping, and that callback may reenter this code.
        capture = null;
        local = null;
        if (retiredCapture) {
          cleanup(() => {
            retiredCapture.track.enabled = false;
          });
          cleanup(() => retiredCapture.setEnabled(false));
          cleanup(() => retiredCapture.stop());
        }
        if (retired) {
          cleanup(() => {
            retired.mediaStreamTrack.enabled = false;
          });
          cleanup(() => retired.stop());
        }
        if (retired || cleanupFailed)
          retiring = Promise.allSettled([
            retiring,
            retired ? attempt(() => retireTrack(retired)) : undefined,
          ]).then((results) => {
            if (
              cleanupFailed ||
              results.some((result) => result.status === "rejected")
            ) {
              cleanupBlocked = true;
              throw unavailable();
            }
          });
        // Keep a rejection observed even before the controller awaits close.
        void retiring.catch(() => fail("ended"));
      };
      const detach = (track: VoxRemoteAudio, expected?: Attachment) => {
        const attachment = elements.get(track);
        if (expected && attachment !== expected) return;
        elements.delete(track);
        activity?.playback(
          [...elements.values()].some((attached) => attached.playing),
        );
        const element = attachment?.element;
        const stop = (attached: HTMLMediaElement) => {
          cleanup(() => {
            attached.muted = true;
          });
          cleanup(() => attached.pause());
          cleanup(() => {
            attached.srcObject = null;
          });
          cleanup(() => attached.remove());
        };
        if (element) stop(element);
        let attached: HTMLMediaElement[] = [];
        cleanup(() => {
          attached = track.detach();
        });
        for (const audio of attached) stop(audio);
      };
      const stopPlayback = () => {
        ++playbackEpoch;
        for (const track of remoteTracks) detach(track);
      };
      const close = (): Promise<void> => {
        if (closing) return closing;
        ++pendingCleanup;
        let complete = () => {};
        let reject = (_error: Error) => {};
        // Publish the shared result before any synchronous cleanup callback.
        closing = new Promise<void>((resolve, failed) => {
          complete = resolve;
          reject = failed;
        });
        closed = true;
        resumeSignalling();
        joined = false;
        microphone = speaker = speech = false;
        cleanup(() => abort.abort());
        cleanup(silence);
        cleanup(stopPlayback);
        cleanup(() => wake?.setEnabled(false));
        cleanup(() => activity?.reset());
        remoteTracks.clear();
        cleanup(unlisten);
        if (typeof window !== "undefined")
          cleanup(() => window.removeEventListener("online", resumeSignalling));
        cleanup(() => request.signal.removeEventListener("abort", aborted));
        if (typeof document !== "undefined")
          cleanup(() =>
            document.removeEventListener("visibilitychange", hidden),
          );
        cleanup(() => {
          if (timer !== null) clearInterval(timer);
        });
        cleanup(() => {
          if (agentTimer !== null) clearTimeout(agentTimer);
        });
        cleanup(() => agentFailed?.());
        void Promise.allSettled([
          attempt(() => room?.disconnect()),
          closeMediaLease(),
          creating,
          upstream,
          retiring,
          attempt(() => wake?.close()),
        ]).then((results) => {
          --pendingCleanup;
          if (
            cleanupFailed ||
            results.some((result) => result.status === "rejected")
          ) {
            cleanupBlocked = true;
            reject(unavailable());
          } else complete();
        });
        return closing;
      };
      const fail = (type: "ended" | "disconnected") => {
        const notify =
          !closed &&
          joined &&
          !request.signal.aborted &&
          sameOwner(owner, request.owner) &&
          callId === request.callId;
        void close().catch(() => {});
        if (notify) {
          try {
            request.onEvent({ type });
          } catch {
            /* Cleanup already revoked media. */
          }
        }
      };
      const aborted = () => {
        void close().catch(() => {});
      };
      const hidden = () => {
        if (visible()) return;
        microphone = speaker = speech = false;
        wake?.setEnabled(false);
        silence();
        stopPlayback();
        syncUpstream();
      };
      const syncUpstream = () => {
        activity?.local(speech && mayCapture());
        if (!mayCapture()) silence();
        else if (!mayPublish()) disableOutbound();
        upstream = upstream
          .then(async () => {
            const track = local;
            if (!track) return;
            if (track === local && mayPublish()) {
              await track.unmute();
              if (track === local && mayPublish()) await track.resumeUpstream();
              if (track === local && mayPublish()) {
                track.mediaStreamTrack.enabled = true;
                return;
              }
            }
            track.mediaStreamTrack.enabled = false;
            await track.mute();
            await track.pauseUpstream();
          })
          .catch(() => fail("ended"));
        return upstream;
      };
      const play = (track: VoxRemoteAudio) => {
        if (
          !fresh() ||
          !joined ||
          !speaker ||
          !visible() ||
          !request.mayPlayAudio() ||
          elements.has(track)
        )
          return;
        const element = track.attach();
        const epoch = playbackEpoch;
        const attachment = { element, epoch, playing: false };
        element.muted = true;
        element.autoplay = false;
        elements.set(track, attachment);
        if (!fresh() || !speaker || !request.mayPlayAudio()) {
          detach(track, attachment);
          return;
        }
        element.muted = false;
        void element
          .play()
          .then(() => {
            // SDK audio elements may be recycled. Only this exact attachment
            // owns its completion; an earlier play must never detach a newer one.
            if (elements.get(track) !== attachment) return;
            if (
              !fresh() ||
              !speaker ||
              !request.mayPlayAudio() ||
              epoch !== playbackEpoch ||
              elements.get(track) !== attachment
            ) {
              detach(track, attachment);
              return;
            }
            attachment.playing = true;
            activity?.playback(true);
            request.onEvent({ type: "playback-ready" });
          })
          .catch(() => {
            if (elements.get(track) !== attachment || epoch !== playbackEpoch)
              return;
            detach(track, attachment);
            if (fresh() && speaker && request.mayPlayAudio())
              request.onEvent({ type: "playback-blocked" });
          });
      };
      request.signal.addEventListener("abort", aborted, { once: true });
      if (typeof window !== "undefined")
        window.addEventListener("online", resumeSignalling);
      if (typeof document !== "undefined")
        document.addEventListener("visibilitychange", hidden);
      ++pendingConnections;
      try {
        admissionStarted = true;
        lease = await options.admission.join(request);
        if (!fresh() || !visible()) throw unavailable();
        validLease(lease, request, now());
        const admittedLease = lease;
        try {
          wakeAdapter =
            options.wakeFactory?.({
              loadConfig: async (signal) => {
                if (!fresh() || signal.aborted || !request.mayListenForWake())
                  throw unavailable();
                const config =
                  (await admittedLease.loadWakeConfig?.(signal)) ?? null;
                if (!fresh() || signal.aborted || !request.mayListenForWake())
                  throw unavailable();
                return config;
              },
            }) ?? null;
          wakeUnavailable = false;
        } catch {
          wakeAdapter = null;
        }
        const sdk = await (options.loadSdk ?? loadLiveKit)();
        if (!fresh() || !visible()) throw unavailable();
        room = sdk.room();
        unlisten = room.listen({
          dataReceived(data, id, topic) {
            if (
              !fresh() ||
              !joined ||
              id !== lease?.agentIdentity ||
              !request.mayRenewReservation?.()
            )
              return;
            if (topic === "navox.vox.web.v1") {
              const result = parseVoxWebLookup(data);
              if (result) request.onEvent({ type: "web-lookup", result });
            } else if (topic === VOX_MEMORY_REVIEW_TOPIC) {
              const result = parseVoxMemoryReview(data);
              if (result) request.onEvent({ type: "memory-review", result });
            } else if (topic === VOX_MEMORY_SAVE_REVIEW_TOPIC) {
              const result = parseVoxMemorySaveReview(data);
              if (result)
                request.onEvent({ type: "memory-save-review", result });
            } else if (topic === "navox.vox.plan-review.v1") {
              const result = parseVoxPlanReview(data);
              if (result) request.onEvent({ type: "plan-review", result });
            } else if (topic === VOX_CALENDAR_REVIEW_TOPIC) {
              const result = parseVoxCalendarReview(data);
              if (result) request.onEvent({ type: "calendar-review", result });
            }
          },
          participantJoined(id) {
            if (id !== lease?.agentIdentity || !fresh()) return;
            room?.subscribeAgent(id);
            agentReady?.();
          },
          participantLeft(id) {
            if (id === lease?.agentIdentity) fail("disconnected");
          },
          trackPublished(id) {
            if (fresh() && id === lease?.agentIdentity)
              room?.subscribeAgent(id);
          },
          trackSubscribed(track, id) {
            if (
              !fresh() ||
              id !== lease?.agentIdentity ||
              track.kind !== "audio"
            ) {
              detach(track);
              return;
            }
            remoteTracks.add(track);
            agentReady?.();
            play(track);
          },
          trackUnsubscribed(track) {
            detach(track);
            remoteTracks.delete(track);
          },
          playbackChanged(allowed) {
            if (!fresh() || !joined) return;
            if (!allowed) {
              // SDK status events have no play-attempt identity. A stale SDK
              // play failure must not tear down a newer owned attachment.
              if (!elements.size && speaker && request.mayPlayAudio())
                request.onEvent({ type: "playback-blocked" });
            } else if (speaker && request.mayPlayAudio()) {
              for (const track of remoteTracks) play(track);
            }
          },
          speakersChanged(identities) {
            if (lease) {
              agentSpeaking = identities.includes(lease.agentIdentity);
              activity?.remote(lease.agentIdentity, agentSpeaking);
            }
          },
          reconnecting() {
            if (!fresh() || !joined) return;
            microphone = speaker = speech = false;
            wake?.setEnabled(false);
            silence();
            stopPlayback();
            activity?.reset();
            request.onEvent({ type: "reconnecting" });
          },
          reconnected() {
            resumeSignalling();
            if (
              !fresh() ||
              !joined ||
              !lease ||
              !room?.hasParticipant(lease.agentIdentity)
            ) {
              fail(fresh() && joined ? "disconnected" : "ended");
              return;
            }
            room.subscribeAgent(lease.agentIdentity);
            request.onEvent({ type: "reconnected" });
          },
          disconnected() {
            // Fresh reservation admission authorizes any replacement room.
            // The old JWT and audio tracks are always retired first.
            fail(fresh() ? "disconnected" : "ended");
          },
        });
        await room.connect(lease.serverUrl, lease.token);
        if (
          !fresh() ||
          !visible() ||
          room.name() !== lease.roomName ||
          room.identity() !== lease.participantIdentity
        )
          throw unavailable();
        // Presence precedes worker warm-up and input setup. The leased agent
        // publishes its output only after those boundaries are ready; subscribe
        // before waiting so already-present publications are handled as well.
        const agentAudioReady = () =>
          fresh() &&
          visible() &&
          room?.isConnected() === true &&
          room.name() === lease?.roomName &&
          room.identity() === lease?.participantIdentity &&
          room.hasParticipant(lease?.agentIdentity ?? "") &&
          (!request.mayRenewReservation || request.mayRenewReservation()) &&
          remoteTracks.size > 0;
        room.subscribeAgent(lease.agentIdentity);
        if (!agentAudioReady()) {
          await new Promise<void>((resolve, reject) => {
            agentReady = () => {
              if (agentAudioReady()) resolve();
            };
            agentFailed = () => reject(unavailable());
            agentTimer = setTimeout(() => reject(unavailable()), 15_000);
          });
        }
        if (!agentAudioReady()) throw unavailable();
        if (agentTimer !== null) clearTimeout(agentTimer);
        agentReady = agentFailed = null;
        joined = true;
        activity = new VoxActivityTracker({
          agentIdentity: lease.agentIdentity,
          mayTransmitAudio: mayCapture,
          mayPlayAudio: () =>
            fresh() && joined && speaker && visible() && request.mayPlayAudio(),
          onChange: (value) =>
            request.onEvent({ type: "activity", activity: value }),
        });
        activity.remote(lease.agentIdentity, agentSpeaking);
        wake = new VoxStandbyWake({
          adapter: wakeAdapter,
          mayListen: () =>
            fresh() && joined && visible() && request.mayListenForWake(),
          onStatus: (status) => {
            if (status === "unavailable") wakeUnavailable = true;
            if (fresh() && joined)
              request.onEvent({ type: "wake-status", status });
          },
          onUnsafe: () => fail("ended"),
          onWake: (event) => {
            request.onWake();
            const text = event.request_text?.trim();
            if (!text) return;
            if (
              !lease?.submitWakeText ||
              text.length > 4000 ||
              !fresh() ||
              !request.mayTransmitAudio()
            ) {
              request.onEvent({ type: "wake-input", status: "failed" });
              return;
            }
            void lease
              .submitWakeText(text, abort.signal)
              .then(() => {
                if (fresh() && request.mayTransmitAudio())
                  request.onEvent({ type: "wake-input", status: "queued" });
              })
              .catch(() => {
                if (fresh())
                  request.onEvent({ type: "wake-input", status: "failed" });
              });
          },
        });
        if (!fresh() || !joined) throw unavailable();
        timer = setInterval(() => {
          resumeSignalling();
          wake?.refresh();
          activity?.refresh();
          if (!fresh()) {
            fail("ended");
            return;
          }
          if (capture && capture.track.readyState !== "live") {
            fail("ended");
            return;
          }
          if (capture && !mayCapture()) {
            silence();
            syncUpstream();
          }
          if (!speaker || !visible() || !request.mayPlayAudio()) stopPlayback();
          const renewAfter = Math.min(
            30_000,
            Math.max(1_000, ((lease?.expiresAt ?? now()) - now()) / 2),
          );
          if (request.mayRenewReservation && !request.mayRenewReservation())
            return;
          if (renewing || now() - renewedAt < renewAfter) return;
          renewing = true;
          void lease
            ?.renew(abort.signal)
            .then((value) => {
              if (!fresh()) {
                fail("ended");
                return;
              }
              if (
                value.connectionId !== lease?.connectionId ||
                value.generation !== lease?.generation ||
                !lease ||
                value.expiresAt < lease.expiresAt ||
                !Number.isFinite(value.expiresAt) ||
                value.expiresAt <= now() ||
                value.expiresAt > now() + 120_000
              )
                throw unavailable();
              if (lease) lease.expiresAt = value.expiresAt;
              renewedAt = now();
            })
            .catch((error: unknown) => {
              // Retry only a typed transport failure, never a rejected or malformed proof.
              if (
                !(
                  error instanceof VoxReservationError &&
                  error.code === "temporary" &&
                  fresh()
                )
              )
                fail("ended");
              else renewedAt = now() - renewAfter + 2_000;
            })
            .finally(() => {
              renewing = false;
            });
        }, 1_000);

        const ensureCapture = () => {
          if (creating || capture || !mayCapture()) return;
          const epoch = ++captureEpoch;
          creating = (async () => {
            await retiring;
            if (epoch !== captureEpoch || !mayCapture()) return;
            const acquired = await captureFactory.create({
              signal: abort.signal,
              mayCapture: () => epoch === captureEpoch && mayCapture(),
              onVoiceActivity(value) {
                if (epoch !== captureEpoch) return;
                speech = value === true && mayCapture();
                const ready = syncUpstream();
                activity?.local(speech);
                return ready;
              },
            });
            acquired.track.enabled = false;
            if (
              epoch !== captureEpoch ||
              !mayCapture() ||
              acquired.track.kind !== "audio" ||
              acquired.track.readyState !== "live"
            ) {
              acquired.setEnabled(false);
              acquired.stop();
              return;
            }
            capture = acquired;
            const track = sdk.audioTrack(acquired.track);
            local = track;
            await track.mute();
            if (track === local && mayCapture()) await track.pauseUpstream();
            if (track !== local || !mayCapture()) {
              if (track === local) silence();
              acquired.stop();
              track.stop();
              return;
            }
            await room?.publishAudio(track, lease?.agentIdentity ?? "");
            if (track !== local || !mayCapture()) {
              if (track === local) silence();
              acquired.stop();
              track.stop();
              await retireTrack(track);
              return;
            }
            acquired.setEnabled(true);
            syncUpstream();
          })()
            .catch(() => fail("ended"))
            .finally(() => {
              creating = null;
              // A mute/unmute may occur while a previous publish is pending.
              // Honor current permission after that obsolete attempt retires.
              if (!capture && mayCapture()) ensureCapture();
            });
        };
        return {
          owner,
          callId,
          setMicrophoneEnabled(enabled) {
            if (enabled) wake?.setEnabled(false);
            microphone = enabled === true;
            if (!mayCapture()) {
              speech = false;
              silence();
            } else capture?.setEnabled(true);
            ensureCapture();
            syncUpstream();
          },
          setSpeakerEnabled(enabled) {
            speaker = enabled === true;
            if (!speaker || !fresh() || !request.mayPlayAudio()) stopPlayback();
            else {
              const epoch = playbackEpoch;
              // Invoke in this setter's user gesture, before awaiting anything.
              void room
                ?.startAudio()
                .then(() => {
                  if (
                    fresh() &&
                    speaker &&
                    request.mayPlayAudio() &&
                    epoch === playbackEpoch
                  )
                    request.onEvent({ type: "playback-ready" });
                })
                .catch(() => {
                  if (
                    fresh() &&
                    speaker &&
                    request.mayPlayAudio() &&
                    epoch === playbackEpoch
                  ) {
                    stopPlayback();
                    request.onEvent({ type: "playback-blocked" });
                  }
                });
              for (const track of remoteTracks) play(track);
            }
          },
          setWakeEnabled(enabled) {
            wake?.setEnabled(enabled);
          },
          stopPlayback,
          close,
        };
      } catch (error) {
        const unallocatedAllowanceDenial =
          !lease &&
          error instanceof VoxReservationError &&
          error.code === "allowance-exhausted";
        // Only the server's explicit pre-allocation budget denial proves no room exists.
        // A rejected admission cannot prove that its external side effect was
        // absent. Quarantine this adapter if it never returned a cleanup handle.
        if (admissionStarted && !lease && !unallocatedAllowanceDenial)
          cleanupBlocked = true;
        // Admission or SDK join may settle after an abort's first close already
        // finished. Retire a late lease and disconnect a late room independently.
        const results = await Promise.allSettled([
          close(),
          closeMediaLease(),
          attempt(() => room?.disconnect()),
        ]);
        if (results.some((result) => result.status === "rejected"))
          cleanupBlocked = true;
        if (unallocatedAllowanceDenial && !cleanupBlocked) throw error;
        throw unavailable();
      } finally {
        // An abort's first close can finish before admission or SDK join does.
        // Keep replacement connects blocked until the entire attempt retires.
        --pendingConnections;
      }
    },
  };
}
