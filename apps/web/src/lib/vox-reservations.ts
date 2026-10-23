/** Owned native reservations and explicit local wake text; neither grants media or AI access. */
import type { VoxConnection, VoxOwner, VoxTransport } from "./vox-call";

export const VOX_RESERVATION_TIMEOUT_MS = 10_000;
export const VOX_RESERVATION_RESPONSE_BYTES = 8192;
export const VOX_RESERVATION_LIMIT = 200;
export const VOX_RESERVATION_LEASE_MS = 120_000;
export const VOX_RESERVATION_RENEW_BEFORE_MS = 30_000;

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DATE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;

export interface VoxReservationView {
  id: string;
  client_call_id: string;
  generation: number;
  status: "reserved" | "ended";
  mode: "off-record";
  created_at: string;
  expires_at: string;
  ended_at: string | null;
  end_reason: "hang-up" | "sign-out" | "expired" | null;
  media_enabled: false;
}

export interface VoxReservationContext {
  generation: number;
  call: VoxReservationView | null;
  media_enabled: false;
}

export interface VoxReservationClientOptions {
  apiBaseUrl?: string;
  fetch?: typeof globalThis.fetch;
  now?: () => number;
  monotonicNow?: () => number;
}

export class VoxReservationError extends Error {
  constructor(
    readonly code:
      | "unavailable"
      | "temporary"
      | "scope-changed"
      | "invalid-response"
      | "cancelled",
  ) {
    super(
      code === "scope-changed"
        ? "The Vox account or call changed. Start a new call."
        : code === "cancelled"
          ? "This Vox call has ended."
          : "The Vox reservation is unavailable. Try again later.",
    );
    this.name = "VoxReservationError";
  }
}

function invalid(): never {
  throw new VoxReservationError("invalid-response");
}

function record(
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== keys.length ||
    Object.keys(value).some((key) => !keys.includes(key))
  )
    invalid();
  return value as Record<string, unknown>;
}

function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) invalid();
  return value.toLowerCase();
}

function generation(value: unknown): number {
  if (
    !Number.isInteger(value) ||
    (value as number) < 0 ||
    (value as number) > 2147483647
  )
    invalid();
  return value as number;
}

function date(value: unknown): string {
  if (
    typeof value !== "string" ||
    !DATE.test(value) ||
    !Number.isFinite(Date.parse(value))
  )
    invalid();
  return value;
}

function view(raw: unknown): VoxReservationView {
  const value = record(raw, [
    "id",
    "client_call_id",
    "generation",
    "status",
    "mode",
    "created_at",
    "expires_at",
    "ended_at",
    "end_reason",
    "media_enabled",
  ]);
  if (
    !["reserved", "ended"].includes(value.status as string) ||
    value.mode !== "off-record" ||
    value.media_enabled !== false
  )
    invalid();
  const created = date(value.created_at);
  const expires = date(value.expires_at);
  const ended = value.ended_at === null ? null : date(value.ended_at);
  if (
    Date.parse(expires) < Date.parse(created) ||
    (value.status === "reserved" &&
      (ended !== null || value.end_reason !== null)) ||
    (value.status === "ended" &&
      (ended === null ||
        Date.parse(ended) < Date.parse(created) ||
        !["hang-up", "sign-out", "expired"].includes(
          value.end_reason as string,
        )))
  )
    invalid();
  return {
    id: uuid(value.id),
    client_call_id: uuid(value.client_call_id),
    generation: generation(value.generation),
    status: value.status as "reserved" | "ended",
    mode: "off-record",
    created_at: created,
    expires_at: expires,
    ended_at: ended,
    end_reason: value.end_reason as VoxReservationView["end_reason"],
    media_enabled: false,
  };
}

function context(raw: unknown): VoxReservationContext {
  const value = record(raw, ["generation", "call", "media_enabled"]);
  if (value.media_enabled !== false) invalid();
  const current = generation(value.generation);
  const call = value.call === null ? null : view(value.call);
  if (call && (call.status !== "reserved" || call.generation !== current))
    invalid();
  return { generation: current, call, media_enabled: false };
}

function scope(value: Readonly<VoxOwner>): Readonly<VoxOwner> {
  return Object.freeze({
    userId: uuid(value.userId),
    workspaceId: uuid(value.workspaceId),
  });
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(new VoxReservationError("cancelled"));
    signal.addEventListener("abort", abort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", abort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", abort);
        reject(
          error instanceof VoxReservationError
            ? error
            : new VoxReservationError("unavailable"),
        );
      },
    );
    if (signal.aborted) abort();
  });
}

interface Intent {
  owner: Readonly<VoxOwner>;
  callId: string;
  generation: number | null;
  ended: boolean;
  failed: boolean;
  proof: Promise<void>;
  creating: Promise<VoxReservationView> | null;
  ending: Promise<void> | null;
  reservation: VoxReservationView | null;
  deadline: number;
  renewing: Promise<VoxReservationView> | null;
  renewalSignal: AbortSignal | null;
}

/** One instance per transport; no timers, fetches or storage until an explicit call. */
export class VoxReservationClient {
  private readonly intents = new Map<string, Intent>();

  constructor(private readonly options: VoxReservationClientOptions = {}) {}

  private monotonic(): number {
    const value = (this.options.monotonicNow ?? (() => performance.now()))();
    if (!Number.isFinite(value)) invalid();
    return value;
  }

  private remaining(intent: Intent): number {
    if (intent.ended || intent.failed || !intent.reservation) return 0;
    const wall = (this.options.now ?? Date.now)();
    if (!Number.isFinite(wall)) return 0;
    return Math.max(
      0,
      Math.min(
        Date.parse(intent.reservation.expires_at) - wall,
        intent.deadline - this.monotonic(),
      ),
    );
  }

  leaseRemaining(owner: Readonly<VoxOwner>, callId: string): number {
    const intent = this.intents.get(this.key(scope(owner), uuid(callId)));
    return intent ? this.remaining(intent) : 0;
  }

  /** A media intent must use the already-owned live reservation generation. */
  mediaAuthority(
    owner: Readonly<VoxOwner>,
    callId: string,
  ): {
    generation: number;
    expiresAt: number;
  } {
    const intent = this.intents.get(this.key(scope(owner), uuid(callId)));
    if (!intent?.reservation || intent.generation === null)
      throw new VoxReservationError("scope-changed");
    this.fresh(intent);
    if (this.remaining(intent) <= 0)
      throw new VoxReservationError("scope-changed");
    return {
      generation: intent.generation,
      expiresAt: Date.parse(intent.reservation.expires_at),
    };
  }

  /** Reuse the same bounded, private cookie transport for a captured media epoch. */
  async mediaRequest(
    owner: Readonly<VoxOwner>,
    callId: string,
    capturedGeneration: number,
    operation:
      | { action: "join"; requestId: string }
      | { action: "renew" | "close" | "wake-config"; connectionId: string }
      | {
          action: "wake";
          connectionId: string;
          requestId: string;
          text: string;
        },
    signal?: AbortSignal,
  ): Promise<unknown> {
    const checked = scope(owner);
    const id = uuid(callId);
    const gen = generation(capturedGeneration);
    if (
      operation.action === "wake" &&
      (typeof operation.text !== "string" ||
        !operation.text.trim() ||
        operation.text.length > 4000)
    )
      throw new VoxReservationError("unavailable");
    const path =
      operation.action === "join"
        ? `/${id}/media`
        : `/${id}/media/${uuid(operation.connectionId)}/${operation.action}`;
    return this.request(
      checked,
      path,
      "POST",
      operation.action === "join"
        ? { generation: gen, request_id: uuid(operation.requestId) }
        : operation.action === "wake"
          ? {
              generation: gen,
              request_id: uuid(operation.requestId),
              text: operation.text.trim(),
            }
          : { generation: gen },
      operation.action === "close" ? 204 : 200,
      signal,
      operation.action === "wake-config",
    );
  }

  private sameReservation(
    left: VoxReservationView,
    right: VoxReservationView,
  ): boolean {
    return (
      left.id === right.id &&
      left.client_call_id === right.client_call_id &&
      left.generation === right.generation &&
      left.mode === right.mode &&
      left.created_at === right.created_at
    );
  }

  private installLease(
    intent: Intent,
    reserved: VoxReservationView,
    started: number,
  ): void {
    const wall = (this.options.now ?? Date.now)();
    const remaining = Date.parse(reserved.expires_at) - wall;
    if (
      !Number.isFinite(wall) ||
      remaining <= 0 ||
      remaining > VOX_RESERVATION_LEASE_MS
    )
      invalid();
    intent.reservation = reserved;
    intent.deadline = Math.min(
      started + VOX_RESERVATION_LEASE_MS,
      this.monotonic() + remaining,
    );
    if (this.remaining(intent) <= 0) invalid();
  }

  private key(owner: Readonly<VoxOwner>, callId: string): string {
    return `${owner.workspaceId}:${owner.userId}:${callId}`;
  }

  private async request(
    owner: Readonly<VoxOwner>,
    path: string,
    method: "GET" | "POST",
    payload: unknown,
    expectedStatus: number,
    signal?: AbortSignal,
    missingAsNull = false,
  ): Promise<unknown> {
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), VOX_RESERVATION_TIMEOUT_MS);
    const combined = signal
      ? AbortSignal.any([signal, timeout.signal])
      : timeout.signal;
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let response: Response | undefined;
    try {
      if (combined.aborted) throw new VoxReservationError("cancelled");
      const base =
        this.options.apiBaseUrl ??
        process.env.NEXT_PUBLIC_API_BASE_URL ??
        "http://localhost:8000/api/v1";
      response = await abortable<Response>(
        (this.options.fetch ?? globalThis.fetch)(
          `${base.replace(/\/$/, "")}/vox/calls${path}`,
          {
            method,
            credentials: "include",
            cache: "no-store",
            redirect: "error",
            headers: {
              "X-NavoX-Account": `${owner.workspaceId}:${owner.userId}`,
              ...(method === "POST"
                ? { "Content-Type": "application/json" }
                : {}),
            },
            ...(method === "POST" ? { body: JSON.stringify(payload) } : {}),
            signal: combined,
          },
        )
          .then((received) => {
            // An abort-ignoring fetch must not leave a late response body open.
            if (combined.aborted) void received.body?.cancel().catch(() => {});
            return received;
          })
          .catch(() => {
            throw new VoxReservationError("temporary");
          }),
        combined,
      );
      if (response.status === 404 && missingAsNull) {
        void response.body?.cancel().catch(() => {});
        return null;
      }
      if (response.status === 401 || response.status === 409)
        throw new VoxReservationError("scope-changed");
      if (response.status === 429 || response.status >= 500)
        throw new VoxReservationError("temporary");
      if (response.status !== expectedStatus)
        throw new VoxReservationError("unavailable");
      if (expectedStatus === 204) return null;
      if (
        response.headers
          .get("content-type")
          ?.split(";", 1)[0]
          .trim()
          .toLowerCase() !== "application/json"
      )
        invalid();
      const declared = response.headers.get("content-length");
      if (
        declared !== null &&
        (!/^\d+$/.test(declared) ||
          Number(declared) > VOX_RESERVATION_RESPONSE_BYTES)
      )
        invalid();
      if (!response.body) invalid();
      reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      for (;;) {
        const { value, done } = await abortable(reader.read(), combined);
        if (done) break;
        length += value.length;
        if (length > VOX_RESERVATION_RESPONSE_BYTES) invalid();
        chunks.push(value);
      }
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
      }
      return JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(bytes),
      );
    } catch (error) {
      if (reader) void reader.cancel().catch(() => {});
      else if (response) void response.body?.cancel().catch(() => {});
      if (combined.aborted && !signal?.aborted && path.endsWith("/renew"))
        throw new VoxReservationError("temporary");
      if (error instanceof VoxReservationError) throw error;
      throw new VoxReservationError(
        !response && !signal?.aborted ? "temporary" : "unavailable",
      );
    } finally {
      clearTimeout(timer);
    }
  }

  private fresh(intent: Intent): void {
    if (intent.ended) throw new VoxReservationError("cancelled");
    if (intent.failed) throw new VoxReservationError("scope-changed");
  }

  async reserve(
    owner: Readonly<VoxOwner>,
    callId: string,
    signal: AbortSignal,
  ): Promise<VoxReservationView> {
    const checked = scope(owner);
    const id = uuid(callId);
    if (signal.aborted) throw new VoxReservationError("cancelled");
    const key = this.key(checked, id);
    let intent = this.intents.get(key);
    if (!intent) {
      if (this.intents.size >= VOX_RESERVATION_LIMIT)
        throw new VoxReservationError("unavailable");
      intent = {
        owner: checked,
        callId: id,
        generation: null,
        ended: false,
        failed: false,
        proof: Promise.resolve(),
        creating: null,
        ending: null,
        reservation: null,
        deadline: 0,
        renewing: null,
        renewalSignal: null,
      };
      this.intents.set(key, intent);
      const captured = intent;
      // Context deliberately has its own bounded lifetime: end can wait for its
      // generation without allowing an aborted caller to create a reservation.
      captured.proof = (async () => {
        const proof = context(
          await this.request(captured.owner, "/context", "GET", null, 200),
        );
        if (proof.call && proof.call.client_call_id !== captured.callId)
          throw new VoxReservationError("scope-changed");
        captured.generation = proof.generation;
      })();
    }
    const captured = intent;
    try {
      await captured.proof;
      this.fresh(captured);
      if (captured.reservation && this.remaining(captured) <= 0) {
        captured.failed = true;
        throw new VoxReservationError("scope-changed");
      }
      if (signal.aborted) throw new VoxReservationError("cancelled");
      if (!captured.creating) {
        captured.creating = (async () => {
          const started = this.monotonic();
          const reserved = view(
            await this.request(
              captured.owner,
              "",
              "POST",
              {
                client_call_id: captured.callId,
                generation: captured.generation,
                mode: "off-record",
              },
              201,
              signal,
            ),
          );
          if (
            reserved.client_call_id !== captured.callId ||
            reserved.generation !== captured.generation ||
            reserved.status !== "reserved"
          )
            invalid();
          this.fresh(captured);
          if (signal.aborted) throw new VoxReservationError("cancelled");
          if (captured.reservation) {
            if (
              !this.sameReservation(captured.reservation, reserved) ||
              captured.reservation.expires_at !== reserved.expires_at ||
              this.remaining(captured) <= 0
            )
              invalid();
          } else this.installLease(captured, reserved, started);
          return reserved;
        })();
      }
      const reserved = await captured.creating;
      this.fresh(captured);
      if (signal.aborted) throw new VoxReservationError("cancelled");
      return { ...reserved };
    } catch (error) {
      if (
        error instanceof VoxReservationError &&
        error.code === "scope-changed"
      )
        captured.failed = true;
      if (
        captured.generation === null &&
        !captured.ended &&
        this.intents.get(key) === captured
      )
        this.intents.delete(key);
      throw error;
    } finally {
      captured.creating = null;
    }
  }

  async renew(
    owner: Readonly<VoxOwner>,
    callId: string,
    signal: AbortSignal,
  ): Promise<VoxReservationView> {
    const intent = this.intents.get(this.key(scope(owner), uuid(callId)));
    if (!intent?.reservation || intent.generation === null)
      throw new VoxReservationError("scope-changed");
    this.fresh(intent);
    if (signal.aborted) throw new VoxReservationError("cancelled");
    if (this.remaining(intent) <= 0) {
      intent.failed = true;
      throw new VoxReservationError("scope-changed");
    }
    if (intent.renewing && intent.renewalSignal?.aborted) {
      // Fresh verification may arrive synchronously after privacy suspension.
      // Retire the cancelled operation before creating its successor, without
      // coalescing fresh authority into the old rejected promise.
      const retired = intent.renewing;
      await retired.catch(() => {});
      if (intent.renewing === retired) {
        intent.renewing = null;
        intent.renewalSignal = null;
      }
      this.fresh(intent);
      if (signal.aborted) throw new VoxReservationError("cancelled");
      if (this.remaining(intent) <= 0) {
        intent.failed = true;
        throw new VoxReservationError("scope-changed");
      }
    }
    if (!intent.renewing) {
      const previous = intent.reservation;
      const started = this.monotonic();
      intent.renewalSignal = signal;
      const pending = (async () => {
        try {
          const renewed = view(
            await this.request(
              intent.owner,
              `/${intent.callId}/renew`,
              "POST",
              { generation: intent.generation },
              200,
              signal,
            ),
          );
          this.fresh(intent);
          if (signal.aborted) throw new VoxReservationError("cancelled");
          if (
            this.remaining(intent) <= 0 ||
            !this.sameReservation(previous, renewed) ||
            renewed.status !== "reserved" ||
            Date.parse(renewed.expires_at) <= Date.parse(previous.expires_at)
          )
            invalid();
          this.installLease(intent, renewed, started);
          return renewed;
        } catch (error) {
          if (
            !signal.aborted &&
            !(
              error instanceof VoxReservationError && error.code === "temporary"
            )
          )
            intent.failed = true;
          throw error;
        }
      })();
      intent.renewing = pending;
      void pending
        .finally(() => {
          if (intent.renewing === pending) {
            intent.renewing = null;
            intent.renewalSignal = null;
          }
        })
        .catch(() => {});
    }
    const renewed = await abortable(intent.renewing, signal);
    this.fresh(intent);
    if (signal.aborted || this.remaining(intent) <= 0)
      throw new VoxReservationError("cancelled");
    return { ...renewed };
  }

  async end(owner: Readonly<VoxOwner>, callId: string): Promise<void> {
    const checked = scope(owner);
    const id = uuid(callId);
    const key = this.key(checked, id);
    const intent = this.intents.get(key);
    if (!intent) return;
    intent.ended = true;
    if (!intent.ending) {
      intent.ending = (async () => {
        await intent.proof;
        if (intent.generation === null) return;
        await this.request(
          intent.owner,
          `/${intent.callId}/end`,
          "POST",
          { generation: intent.generation },
          204,
        );
      })().finally(() => {
        // A late old-owner proof cannot delete a replacement binding.
        if (this.intents.get(key) === intent) this.intents.delete(key);
      });
    }
    await intent.ending;
  }
}

/** Reservation metadata is only a precondition; inner owns independent media authorization. */
export function wrapVoxReservations(
  inner: VoxTransport,
  client: VoxReservationClient,
): VoxTransport {
  let cleanupBlocked = false;
  const attempts = new Set<{
    owner: Readonly<VoxOwner>;
    callId: string;
    stop(): Promise<void>;
  }>();
  return {
    get available() {
      return inner.available && !cleanupBlocked;
    },
    get localWakeAvailable() {
      return inner.localWakeAvailable;
    },
    async connect(request) {
      if (!inner.available || cleanupBlocked)
        throw new VoxReservationError("unavailable");
      const abort = new AbortController();
      let live = true;
      let reserved = false;
      let connection: VoxConnection | null = null;
      let closing: Promise<void> | null = null;
      let timer: ReturnType<typeof setTimeout> | null = null;
      let renewal: AbortController | null = null;
      let unsubscribe: (() => void) | undefined;
      let cleanupError = false;
      const remaining = () =>
        client.leaseRemaining(request.owner, request.callId);
      // A legacy transport request without synchronous privacy notifications
      // gets its original bounded lease, never automatic renewal.
      const mayRenew = () =>
        Boolean(
          request.observeReservationAuthority &&
            request.mayRenewReservation?.(),
        );
      const clearTimer = () => {
        if (timer !== null) clearTimeout(timer);
        timer = null;
      };
      const stop = (): Promise<void> => {
        live = false;
        clearTimer();
        renewal?.abort();
        renewal = null;
        abort.abort();
        const unobserve = unsubscribe;
        unsubscribe = undefined;
        try {
          unobserve?.();
        } catch {
          cleanupBlocked = true;
          cleanupError = true;
        }
        request.signal.removeEventListener("abort", aborted);
        attempts.delete(attempt);
        if (connection && !closing) {
          const captured = connection;
          let safe = !cleanupError;
          let complete!: () => void;
          let failed!: (error: unknown) => void;
          // Register release before adapter callbacks can reenter close/start.
          closing = new Promise<void>((resolve, reject) => {
            complete = resolve;
            failed = reject;
          });
          for (const disable of [
            () => captured.setMicrophoneEnabled(false),
            () => captured.setSpeakerEnabled(false),
            () => captured.setWakeEnabled(false),
            () => captured.stopPlayback(),
          ]) {
            try {
              disable();
            } catch {
              safe = false;
              cleanupBlocked = true;
            }
          }
          void Promise.resolve()
            .then(() => captured.close())
            .then(() => {
              if (!safe) throw new VoxReservationError("unavailable");
              complete();
            })
            .catch((error) => {
              cleanupBlocked = true;
              failed(error);
            });
        }
        return (
          closing ??
          (cleanupError
            ? Promise.reject(new VoxReservationError("unavailable"))
            : Promise.resolve())
        );
      };
      const attempt = { owner: request.owner, callId: request.callId, stop };
      const aborted = () => {
        void stop().catch(() => {});
      };
      const ended = () => {
        if (!live) return;
        void stop().catch(() => {});
        request.onEvent({ type: "ended" });
      };
      const alive = () => {
        if (!live || request.signal.aborted) return false;
        if (!reserved || remaining() <= 0) {
          ended();
          return false;
        }
        return true;
      };
      const renew = async () => {
        if (!alive() || !mayRenew() || renewal) return;
        const current = new AbortController();
        renewal = current;
        reconcile();
        try {
          await client.renew(request.owner, request.callId, current.signal);
          if (!live || renewal !== current) return;
          renewal = null;
          if (!mayRenew()) return reconcile();
          reconcile();
        } catch (error) {
          if (!live || renewal !== current) return;
          renewal = null;
          if (
            error instanceof VoxReservationError &&
            error.code === "temporary" &&
            remaining() > 0
          ) {
            clearTimer();
            timer = setTimeout(reconcile, Math.min(2_000, remaining()));
          } else ended();
        }
      };
      const reconcile = () => {
        clearTimer();
        if (!live || !reserved) return;
        if (!alive()) return;
        if (!mayRenew()) {
          renewal?.abort();
          renewal = null;
        }
        const left = remaining();
        if (!renewal && mayRenew() && left <= VOX_RESERVATION_RENEW_BEFORE_MS) {
          void renew();
          return;
        }
        const delay =
          !renewal && mayRenew()
            ? left - VOX_RESERVATION_RENEW_BEFORE_MS
            : left;
        timer = setTimeout(reconcile, Math.max(0, delay));
      };
      attempts.add(attempt);
      request.signal.addEventListener("abort", aborted, { once: true });
      try {
        try {
          unsubscribe = request.observeReservationAuthority?.(reconcile);
        } catch (error) {
          cleanupBlocked = true;
          throw error;
        }
        if (request.signal.aborted) aborted();
        await client.reserve(request.owner, request.callId, abort.signal);
        if (!live || request.signal.aborted)
          throw new VoxReservationError("cancelled");
        reserved = true;
        reconcile();
        if (!alive()) throw new VoxReservationError("cancelled");
        connection = await inner.connect({
          ...request,
          signal: abort.signal,
          mayTransmitAudio: () => alive() && request.mayTransmitAudio(),
          mayPlayAudio: () => alive() && request.mayPlayAudio(),
          mayListenForWake: () => alive() && request.mayListenForWake(),
          onEvent: (event) => {
            if (alive()) request.onEvent(event);
          },
        });
        // Keep an abort-ignoring join owned until its resource is released.
        if (!alive()) {
          await stop();
          throw new VoxReservationError("cancelled");
        }
        return {
          owner: connection.owner,
          callId: connection.callId,
          setMicrophoneEnabled(enabled) {
            reconcile();
            connection?.setMicrophoneEnabled(enabled && alive());
          },
          setSpeakerEnabled(enabled) {
            reconcile();
            connection?.setSpeakerEnabled(enabled && alive());
          },
          setWakeEnabled(enabled) {
            reconcile();
            connection?.setWakeEnabled(enabled && alive());
          },
          stopPlayback() {
            connection?.stopPlayback();
          },
          close: stop,
        };
      } catch (error) {
        await stop();
        throw error;
      }
    },
    async endCall(owner, callId) {
      const closing = [...attempts]
        .filter(
          (attempt) =>
            attempt.callId === callId &&
            attempt.owner.userId === owner.userId &&
            attempt.owner.workspaceId === owner.workspaceId,
        )
        .map((attempt) => attempt.stop());
      const results = await Promise.allSettled([
        ...closing,
        client.end(owner, callId),
        Promise.resolve().then(() => inner.endCall?.(owner, callId)),
      ]);
      if (results.some((result) => result.status === "rejected")) {
        cleanupBlocked = true;
        throw new VoxReservationError("unavailable");
      }
    },
  };
}
