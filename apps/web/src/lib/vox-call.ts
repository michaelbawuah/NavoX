import type { VoxMemoryReview } from "./vox-memory-review";
import type { VoxPlanReview } from "./vox-plan-review";
import { VoxReservationError } from "./vox-reservations";
import type { VoxWebLookup } from "./vox-web-results";

/**
 * Transport-independent Vox lifecycle groundwork. This module opens no microphone,
 * calls no provider and retains no audio or conversation text. A future app-level
 * owner must supply an authorized transport and fresh account checks.
 */
export interface VoxOwner {
  userId: string;
  workspaceId: string;
}

export type VoxCallPhase =
  | "idle"
  | "connecting"
  | "active"
  | "reconnecting"
  | "standby"
  | "unavailable"
  | "ended";
export type VoxActivity = "quiet" | "listening" | "thinking" | "speaking";
export type VoxEndReason =
  | "hang-up"
  | "owner-changed"
  | "disposed"
  | "transport-ended"
  | "connection-failed"
  | "allowance-reached"
  | "cleanup-incomplete"
  | "transport-unavailable";

export interface VoxCallState {
  owner: VoxOwner | null;
  /** False while the app verifies the current session; it is never a provider grant. */
  ownerVerified: boolean;
  callId: string | null;
  phase: VoxCallPhase;
  activity: VoxActivity;
  microphoneMuted: boolean;
  speakerEnabled: boolean;
  /** Browser playback needs another user gesture; microphone authority is separate. */
  speakerBlocked: boolean;
  wakeStatus?: "off" | "starting" | "listening" | "unavailable";
  webLookup?: VoxWebLookup | null;
  planReview?: VoxPlanReview | null;
  memoryReview?: VoxMemoryReview | null;
  wakeInputStatus?: "queued" | "failed" | null;
  captionsEnabled: boolean;
  minimized: boolean;
  startedAt: number | null;
  elapsedMs: number;
  endReason: VoxEndReason | null;
}

export type VoxTransportEvent =
  | { type: "activity"; activity: VoxActivity }
  | { type: "disconnected" }
  | { type: "reconnecting" }
  | { type: "reconnected" }
  | { type: "playback-blocked" }
  | { type: "playback-ready" }
  | {
      type: "wake-status";
      status: "off" | "starting" | "listening" | "unavailable";
    }
  | { type: "web-lookup"; result: VoxWebLookup }
  | { type: "plan-review"; result: VoxPlanReview }
  | { type: "memory-review"; result: VoxMemoryReview }
  | { type: "wake-input"; status: "queued" | "failed" }
  | { type: "ended" };

export interface VoxTransportRequest {
  owner: Readonly<VoxOwner>;
  callId: string;
  signal: AbortSignal;
  /** Check before every frame; this lease is revoked before asynchronous cleanup. */
  mayTransmitAudio(): boolean;
  mayPlayAudio(): boolean;
  /** Local detection only. Standby must never send audio to a speech provider. */
  mayListenForWake(): boolean;
  /** Native metadata renewal only; independent of microphone, speaker and standby. */
  mayRenewReservation?(): boolean;
  /** Synchronous ownership/privacy notification, including while a join is pending. */
  observeReservationAuthority?(listener: () => void): () => void;
  onWake(): void;
  onEvent(event: VoxTransportEvent): void;
}

/**
 * Gate setters must take effect synchronously. close must release tracks, local
 * wake listeners, playback, and the owned connection even after an aborted join.
 * A transport must apply local voice detection before sending audio; permission
 * from mayTransmitAudio alone is not evidence that a frame contains speech.
 */
export interface VoxConnection {
  owner: Readonly<VoxOwner>;
  callId: string;
  setMicrophoneEnabled(enabled: boolean): void;
  setSpeakerEnabled(enabled: boolean): void;
  setWakeEnabled(enabled: boolean): void;
  stopPlayback(): void;
  close(): void | Promise<void>;
}

export interface VoxTransport {
  available: boolean;
  localWakeAvailable: boolean;
  /** Returning a connection means its authorized join has actually succeeded. */
  connect(request: VoxTransportRequest): Promise<VoxConnection>;
  /** Terminal owned-call cleanup, distinct from closing one reconnect attempt. */
  endCall?(owner: Readonly<VoxOwner>, callId: string): void | Promise<void>;
}

export const VOX_STANDBY_AFTER_MS = 2 * 60_000;
// A terminal reservation may need context then end; physical release uses the
// same bound and remains unsafe after timeout, even if a late close succeeds.
export const VOX_END_CALL_TIMEOUT_MS = 25_000;
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function owner(value: VoxOwner | null): VoxOwner | null {
  if (value === null) return null;
  if (!UUID.test(value.userId) || !UUID.test(value.workspaceId))
    throw new Error("Vox needs a verified account and workspace.");
  return { userId: value.userId, workspaceId: value.workspaceId };
}

function sameOwner(left: VoxOwner | null, right: VoxOwner | null): boolean {
  return (
    left?.userId === right?.userId && left?.workspaceId === right?.workspaceId
  );
}

/**
 * Keep one controller above page navigation. Minimizing changes presentation only;
 * account loss/change, reset, or logout must call bindOwner(null) or hangUp first.
 * No browser storage, durable session, provider grant or memory consent is created.
 */
const MAX_TERMINAL_RECOVERIES = 3;
const TERMINAL_RECOVERY_WINDOW_MS = 120_000;

export class VoxCallController {
  private state: VoxCallState;
  private epoch = 0;
  private abort: AbortController | null = null;
  private connection: VoxConnection | null = null;
  private cleanup = Promise.resolve(true);
  private releases = new WeakMap<VoxConnection, Promise<boolean>>();
  private usedCallIds = new Set<string>();
  private listeners = new Set<(state: VoxCallState) => void>();
  private reservationObservers = new Set<() => void>();
  private disposed = false;
  private lastActivityAt = 0;
  private lastNow = 0;
  private resumeStandby = false;
  private deferredJoin = false;
  private reconnectAfterCheck = false;
  private mediaReconnecting = false;
  private mediaReconnected = false;
  private mediaResumeStandby = false;
  private cleanupIncomplete = false;
  private terminalRecoveries: number[] = [];
  private callIntent: {
    owner: Readonly<VoxOwner>;
    callId: string;
    ended: boolean;
  } | null = null;

  constructor(
    private readonly options: {
      owner: VoxOwner | null;
      transport: VoxTransport;
      newCallId: () => string;
      now?: () => number;
    },
  ) {
    const checkedOwner = owner(options.owner);
    this.state = {
      owner: checkedOwner,
      ownerVerified: checkedOwner !== null,
      callId: null,
      phase: "idle",
      activity: "quiet",
      microphoneMuted: false,
      speakerEnabled: true,
      speakerBlocked: false,
      captionsEnabled: true,
      minimized: false,
      startedAt: null,
      elapsedMs: 0,
      endReason: null,
    };
  }

  current(): VoxCallState {
    return {
      ...this.state,
      owner: this.state.owner ? { ...this.state.owner } : null,
    };
  }

  subscribe(listener: (state: VoxCallState) => void): () => void {
    if (this.disposed) return () => {};
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private publish() {
    for (const listener of this.listeners) {
      try {
        listener(this.current());
      } catch {
        // A detached view must not prevent transport cleanup or other updates.
      }
    }
  }

  dismissMemoryReview(): void {
    this.state.memoryReview = null;
    this.publish();
  }

  dismissPlanReview(): void {
    this.state.planReview = null;
    this.publish();
  }

  private moment(): number {
    const value = (this.options.now ?? Date.now)();
    if (Number.isFinite(value)) this.lastNow = Math.max(this.lastNow, value);
    return this.lastNow;
  }

  private live(epoch: number): boolean {
    return (
      !this.disposed &&
      epoch === this.epoch &&
      this.abort !== null &&
      !this.abort.signal.aborted
    );
  }

  private attached(epoch: number, scope: VoxOwner, callId: string): boolean {
    return (
      this.live(epoch) &&
      sameOwner(scope, this.state.owner) &&
      callId === this.state.callId
    );
  }

  private bound(epoch: number, scope: VoxOwner, callId: string): boolean {
    return this.state.ownerVerified && this.attached(epoch, scope, callId);
  }

  private joined(): boolean {
    return this.state.phase === "active" || this.state.phase === "standby";
  }

  private ongoing(): boolean {
    return (
      this.joined() ||
      this.state.phase === "connecting" ||
      this.state.phase === "reconnecting"
    );
  }

  private release(connection: VoxConnection): Promise<boolean> {
    const previous = this.releases.get(connection);
    if (previous) return previous;
    let complete!: (safe: boolean) => void;
    const release = new Promise<boolean>((resolve) => {
      complete = resolve;
    });
    // Register before invoking adapter code: even a synchronous resource callback
    // that starts another call must wait for this connection to finish closing.
    this.releases.set(connection, release);
    this.cleanup = Promise.all([this.cleanup, release]).then(
      ([earlier, current]) => earlier && current,
    );
    let safe = true;
    for (const disable of [
      () => connection.setMicrophoneEnabled(false),
      () => connection.setSpeakerEnabled(false),
      () => connection.setWakeEnabled(false),
      () => connection.stopPlayback(),
    ]) {
      try {
        disable();
      } catch {
        safe = false;
      }
    }
    const timeout = setTimeout(() => complete(false), VOX_END_CALL_TIMEOUT_MS);
    void (async () => {
      try {
        await connection.close();
      } catch {
        safe = false;
      }
      clearTimeout(timeout);
      complete(safe);
    })();
    return release;
  }

  private syncControls(): boolean {
    try {
      for (const listener of this.reservationObservers) listener();
    } catch {
      void this.hangUp("connection-failed");
      return false;
    }
    const connection = this.connection;
    if (!connection) return true;
    try {
      connection.setMicrophoneEnabled(
        this.state.ownerVerified &&
          this.state.phase === "active" &&
          !this.state.microphoneMuted,
      );
      connection.setSpeakerEnabled(
        this.state.ownerVerified &&
          this.state.phase === "active" &&
          this.state.speakerEnabled,
      );
      connection.setWakeEnabled(
        this.state.ownerVerified &&
          this.state.phase === "standby" &&
          this.options.transport.localWakeAvailable &&
          !this.state.microphoneMuted,
      );
      return true;
    } catch {
      void this.hangUp("connection-failed");
      return false;
    }
  }

  async start(): Promise<void> {
    if (
      this.disposed ||
      !this.state.owner ||
      !this.state.ownerVerified ||
      !["idle", "ended", "unavailable"].includes(this.state.phase)
    )
      return;
    if (this.cleanupIncomplete) {
      this.state.phase = "ended";
      this.state.endReason = "cleanup-incomplete";
      this.publish();
      return;
    }
    if (!this.options.transport.available) {
      this.state.phase = "unavailable";
      this.state.endReason = "transport-unavailable";
      this.publish();
      return;
    }
    const callId = this.options.newCallId();
    if (!UUID.test(callId) || this.usedCallIds.has(callId))
      throw new Error("Vox needs a fresh call identifier.");
    this.usedCallIds.add(callId);
    this.callIntent = {
      owner: Object.freeze({ ...this.state.owner }),
      callId,
      ended: false,
    };
    this.state = {
      ...this.state,
      callId,
      phase: "connecting",
      activity: "quiet",
      minimized: false,
      startedAt: null,
      elapsedMs: 0,
      endReason: null,
      speakerBlocked: false,
      wakeStatus: "off",
      wakeInputStatus: null,
      webLookup: null,
      planReview: null,
      memoryReview: null,
    };
    this.resumeStandby = false;
    this.deferredJoin = false;
    this.reconnectAfterCheck = false;
    this.terminalRecoveries = [];
    await this.join(false);
  }

  async reconnect(): Promise<void> {
    if (
      this.disposed ||
      !this.state.ownerVerified ||
      !(
        this.joined() ||
        (this.mediaReconnecting && this.state.phase === "reconnecting")
      )
    )
      return;
    const now = this.moment();
    this.terminalRecoveries = this.terminalRecoveries.filter(
      (at) => now - at < TERMINAL_RECOVERY_WINDOW_MS,
    );
    if (this.terminalRecoveries.length >= MAX_TERMINAL_RECOVERIES) {
      // Persistent worker/provider failures must not create an endless paid loop.
      await this.hangUp("connection-failed");
      return;
    }
    this.terminalRecoveries.push(now);
    this.resumeStandby = this.mediaReconnecting
      ? this.mediaResumeStandby
      : this.state.phase === "standby";
    await this.join(true);
  }

  private async join(reconnecting: boolean): Promise<void> {
    const scope = this.state.owner;
    const callId = this.state.callId;
    if (!scope || !callId) return;
    this.deferredJoin = false;
    this.mediaReconnecting = this.mediaReconnected = false;
    const epoch = ++this.epoch;
    const previousAbort = this.abort;
    const controller = new AbortController();
    this.abort = controller;
    const previous = this.connection;
    this.connection = null;
    this.state.phase = reconnecting ? "reconnecting" : "connecting";
    this.state.activity = "quiet";
    if (previous) void this.release(previous);
    previousAbort?.abort();
    if (!this.live(epoch)) return;
    this.publish();
    if (!(await this.cleanup)) {
      if (this.live(epoch)) await this.hangUp("cleanup-incomplete");
      return;
    }
    if (!this.live(epoch)) return;
    if (!this.state.ownerVerified) {
      this.deferredJoin = true;
      return;
    }
    const request: VoxTransportRequest = {
      owner: Object.freeze({ ...scope }),
      callId,
      signal: controller.signal,
      mayTransmitAudio: () =>
        this.bound(epoch, scope, callId) &&
        this.state.phase === "active" &&
        !this.state.microphoneMuted,
      mayPlayAudio: () =>
        this.bound(epoch, scope, callId) &&
        this.state.phase === "active" &&
        this.state.speakerEnabled,
      mayListenForWake: () =>
        this.bound(epoch, scope, callId) &&
        this.state.phase === "standby" &&
        this.options.transport.localWakeAvailable &&
        !this.state.microphoneMuted,
      mayRenewReservation: () =>
        this.bound(epoch, scope, callId) && this.ongoing(),
      observeReservationAuthority: (listener) => {
        this.reservationObservers.add(listener);
        return () => this.reservationObservers.delete(listener);
      },
      onWake: () => {
        if (request.mayListenForWake()) this.resume();
      },
      onEvent: (event) => {
        if (!this.attached(epoch, scope, callId) || !this.ongoing()) return;
        if (event.type === "ended") {
          void this.hangUp("transport-ended");
          return;
        }
        if (event.type === "reconnecting") {
          if (this.mediaReconnecting || !this.joined()) return;
          this.mediaResumeStandby = this.state.phase === "standby";
          this.mediaReconnecting = true;
          this.mediaReconnected = false;
          this.state.phase = "reconnecting";
          this.state.activity = "quiet";
          if (this.syncControls()) this.publish();
          return;
        }
        if (event.type === "reconnected") {
          if (!this.mediaReconnecting) return;
          this.mediaReconnected = true;
          this.finishMediaReconnect();
          return;
        }
        if (event.type === "disconnected") {
          if (this.state.ownerVerified) void this.reconnect();
          else this.reconnectAfterCheck = true;
          return;
        }
        if (!this.joined()) return;
        if (event.type === "wake-status") {
          this.state.wakeStatus = event.status;
          this.publish();
          return;
        }
        if (event.type === "memory-review") {
          if (!this.state.ownerVerified) return;
          this.state.memoryReview = event.result;
          this.publish();
          return;
        }
        if (event.type === "plan-review") {
          if (!this.state.ownerVerified) return;
          this.state.planReview = event.result;
          this.publish();
          return;
        }
        if (event.type === "web-lookup") {
          if (!this.state.ownerVerified) return;
          this.state.webLookup = event.result;
          this.publish();
          return;
        }
        if (event.type === "wake-input") {
          this.state.wakeInputStatus = event.status;
          this.publish();
          return;
        }
        if (
          event.type === "playback-blocked" ||
          event.type === "playback-ready"
        ) {
          if (!this.state.ownerVerified) return;
          this.state.speakerBlocked = event.type === "playback-blocked";
          if (this.state.speakerBlocked && this.state.activity === "speaking") {
            this.state.activity = "quiet";
            this.lastActivityAt = this.moment();
          }
          this.publish();
        } else if (this.state.ownerVerified && this.state.phase === "active") {
          const activity =
            event.activity === "speaking" && this.state.speakerBlocked
              ? "quiet"
              : event.activity;
          if (activity !== "quiet" || this.state.activity !== "quiet")
            this.lastActivityAt = this.moment();
          this.state.activity = activity;
          if (activity !== "quiet") this.state.wakeInputStatus = null;
          this.publish();
        }
      },
    };
    // A new join must wait for aborted joins to return and release any resource
    // acquired before abort. Merely revoking their callbacks is insufficient.
    let completeJoin!: (safe: boolean) => void;
    let safelyReleased = true;
    const pendingJoin = new Promise<boolean>((resolve) => {
      completeJoin = resolve;
    });
    this.cleanup = Promise.all([this.cleanup, pendingJoin]).then(
      ([earlier, current]) => earlier && current,
    );
    try {
      const connection = await this.options.transport.connect(request);
      if (!this.live(epoch)) {
        safelyReleased = await this.release(connection);
        return;
      }
      if (!sameOwner(scope, connection.owner) || callId !== connection.callId) {
        void this.release(connection);
        void this.hangUp("connection-failed");
        return;
      }
      this.connection = connection;
      const now = this.moment();
      this.state.startedAt ??= now;
      this.lastActivityAt = now;
      this.state.phase = this.resumeStandby ? "standby" : "active";
      if (this.syncControls()) this.publish();
    } catch (error) {
      if (this.live(epoch))
        void this.hangUp(
          error instanceof VoxReservationError &&
            error.code === "allowance-exhausted"
            ? "allowance-reached"
            : "connection-failed",
        );
    } finally {
      completeJoin(safelyReleased);
    }
  }

  private finishMediaReconnect(): void {
    if (
      !this.mediaReconnecting ||
      !this.mediaReconnected ||
      !this.state.ownerVerified ||
      this.state.phase !== "reconnecting"
    )
      return;
    this.mediaReconnecting = this.mediaReconnected = false;
    this.state.phase = this.mediaResumeStandby ? "standby" : "active";
    this.state.activity = "quiet";
    this.lastActivityAt = this.moment();
    if (this.syncControls()) this.publish();
  }

  setMicrophoneMuted(muted: boolean): void {
    if (this.disposed || !this.ongoing()) return;
    this.state.microphoneMuted = muted;
    if (this.syncControls()) this.publish();
  }

  setSpeakerEnabled(enabled: boolean): void {
    if (this.disposed || !this.ongoing()) return;
    this.state.speakerEnabled = enabled;
    if (!enabled) {
      try {
        this.connection?.stopPlayback();
      } catch {
        void this.hangUp("connection-failed");
        return;
      }
    }
    if (this.syncControls()) this.publish();
  }

  setCaptionsEnabled(enabled: boolean): void {
    if (this.disposed || !this.ongoing()) return;
    this.state.captionsEnabled = enabled;
    this.publish();
  }

  setMinimized(minimized: boolean): void {
    if (this.disposed || !this.state.callId || this.state.phase === "ended")
      return;
    this.state.minimized = minimized;
    this.publish();
  }

  /** The future app owner supplies ticks; no hidden timer or silence timeout ends a call. */
  tick(): void {
    if (this.disposed || this.state.startedAt === null || !this.joined())
      return;
    const now = this.moment();
    this.state.elapsedMs = Math.max(0, now - this.state.startedAt);
    if (
      this.state.ownerVerified &&
      this.state.phase === "active" &&
      this.state.activity === "quiet" &&
      now - this.lastActivityAt >= VOX_STANDBY_AFTER_MS
    ) {
      this.state.phase = "standby";
      try {
        this.connection?.stopPlayback();
      } catch {
        void this.hangUp("connection-failed");
        return;
      }
      this.syncControls();
    }
    this.publish();
  }

  /** A screen tap works without a local wake engine; it never creates a new call. */
  resume(): void {
    if (
      this.disposed ||
      !this.state.ownerVerified ||
      this.state.phase !== "standby"
    )
      return;
    this.state.phase = "active";
    this.state.activity = "quiet";
    this.lastActivityAt = this.moment();
    if (this.syncControls()) this.publish();
  }

  /**
   * Revoke authority synchronously while retaining this call's identity and
   * preferences. Only a fresh verified bindOwner result can lift this gate.
   * The app must fence stale account-check results before calling bindOwner.
   */
  suspendForSessionCheck(): void {
    if (this.disposed) return;
    this.state.ownerVerified = false;
    this.state.memoryReview = null;
    // Playback/capture is stopped; a suppressed quiet callback must not leave
    // an old speaking/thinking label preventing standby after verification.
    this.state.activity = "quiet";
    if (!this.syncControls()) return;
    try {
      this.connection?.stopPlayback();
    } catch {
      void this.hangUp("connection-failed");
      return;
    }
    this.publish();
  }

  async bindOwner(next: VoxOwner | null): Promise<void> {
    let checked: VoxOwner | null = null;
    let invalid = false;
    try {
      checked = owner(next);
    } catch {
      invalid = true;
    }
    if (this.disposed) return;
    if (!invalid && sameOwner(this.state.owner, checked)) {
      if (!checked || this.state.ownerVerified) return;
      this.state.ownerVerified = true;
      this.lastActivityAt = this.moment();
      if (
        this.reconnectAfterCheck &&
        (this.joined() || this.mediaReconnecting)
      ) {
        this.reconnectAfterCheck = false;
        await this.reconnect();
      } else if (this.mediaReconnecting && this.mediaReconnected) {
        this.finishMediaReconnect();
      } else if (this.deferredJoin && this.ongoing()) {
        await this.join(this.state.phase === "reconnecting");
      } else if (this.syncControls()) this.publish();
      return;
    }
    await this.terminate("owner-changed", { owner: checked });
    if (invalid) throw new Error("Vox needs a verified account and workspace.");
  }

  async hangUp(reason: VoxEndReason = "hang-up"): Promise<void> {
    await this.terminate(reason);
  }

  private async terminate(
    reason: VoxEndReason,
    rebind?: { owner: VoxOwner | null },
  ): Promise<void> {
    const intent = this.callIntent;
    this.callIntent = null;
    const endCall = this.options.transport.endCall;
    // Register terminal cleanup before invoking any adapter or abort callbacks.
    // Reentrant starts must wait for it, even while an old join is unresolved.
    let finishEnd: (safe: boolean) => void = () => {};
    let mustEnd = false;
    if (intent && !intent.ended && endCall) {
      intent.ended = true;
      mustEnd = true;
      const terminal = new Promise<boolean>((resolve) => {
        finishEnd = resolve;
      });
      this.cleanup = Promise.all([this.cleanup, terminal]).then(
        ([earlier, current]) => earlier && current,
      );
    }
    const terminalEpoch = ++this.epoch;
    this.deferredJoin = false;
    this.reconnectAfterCheck = false;
    this.mediaReconnecting = this.mediaReconnected = false;
    const previousAbort = this.abort;
    this.abort = null;
    const connection = this.connection;
    this.connection = null;
    if (this.state.phase !== "ended") {
      if (this.state.startedAt !== null)
        this.state.elapsedMs = Math.max(
          0,
          this.moment() - this.state.startedAt,
        );
      this.state.phase = "ended";
      this.state.activity = "quiet";
      this.state.minimized = false;
      this.state.speakerBlocked = false;
      this.state.wakeStatus = "off";
      this.state.wakeInputStatus = null;
      this.state.webLookup = null;
      this.state.memoryReview = null;
      this.state.endReason = reason;
    }
    if (rebind) {
      // Rebind before resource callbacks or subscribers can reenter start().
      this.state.owner = rebind.owner;
      this.state.ownerVerified = rebind.owner !== null;
      this.state.callId = null;
      this.state.startedAt = null;
      this.state.elapsedMs = 0;
      this.state.microphoneMuted = false;
      this.state.speakerEnabled = true;
      this.state.speakerBlocked = false;
      this.state.captionsEnabled = true;
    }
    if (connection) void this.release(connection);
    previousAbort?.abort();
    if (mustEnd && intent) {
      const complete = finishEnd;
      const timeout = setTimeout(
        () => complete(false),
        VOX_END_CALL_TIMEOUT_MS,
      );
      try {
        Promise.resolve(
          endCall?.call(this.options.transport, intent.owner, intent.callId),
        ).then(
          () => {
            clearTimeout(timeout);
            complete(true);
          },
          () => {
            clearTimeout(timeout);
            complete(false);
          },
        );
      } catch {
        clearTimeout(timeout);
        complete(false);
      }
    }
    this.publish();
    const safe = await this.cleanup;
    if (!safe) this.cleanupIncomplete = true;
    if (terminalEpoch === this.epoch && this.state.phase === "ended") {
      if (!safe) this.state.endReason = "cleanup-incomplete";
      // Transport availability can change when asynchronous retirement finishes.
      // Refresh the UI after success too, so the next call does not require reload.
      this.publish();
    }
  }

  async dispose(): Promise<void> {
    this.disposed = true;
    this.state.ownerVerified = false;
    this.state.memoryReview = null;
    await this.hangUp("disposed");
    this.listeners.clear();
    this.reservationObservers.clear();
  }
}
