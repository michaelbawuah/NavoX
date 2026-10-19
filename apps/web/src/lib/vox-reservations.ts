/** Owned, content-free native reservations. These never grant media or AI access. */
import type { VoxOwner, VoxTransport } from "./vox-call";

export const VOX_RESERVATION_TIMEOUT_MS = 10_000;
export const VOX_RESERVATION_RESPONSE_BYTES = 8192;
export const VOX_RESERVATION_LIMIT = 200;

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
}

export class VoxReservationError extends Error {
  constructor(
    readonly code:
      | "unavailable"
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
      () => {
        signal.removeEventListener("abort", abort);
        reject(new VoxReservationError("unavailable"));
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
}

/** One instance per transport; no timers, fetches or storage until an explicit call. */
export class VoxReservationClient {
  private readonly intents = new Map<string, Intent>();

  constructor(private readonly options: VoxReservationClientOptions = {}) {}

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
        ).then((received) => {
          // An abort-ignoring fetch must not leave a late response body open.
          if (combined.aborted) void received.body?.cancel().catch(() => {});
          return received;
        }),
        combined,
      );
      if (response.status === 401 || response.status === 409)
        throw new VoxReservationError("scope-changed");
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
      if (error instanceof VoxReservationError) throw error;
      throw new VoxReservationError("unavailable");
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
      if (signal.aborted) throw new VoxReservationError("cancelled");
      if (!captured.creating) {
        captured.creating = (async () => {
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
          const now = (this.options.now ?? Date.now)();
          if (!Number.isFinite(now) || Date.parse(reserved.expires_at) <= now)
            invalid();
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
  return {
    get available() {
      return inner.available;
    },
    get localWakeAvailable() {
      return inner.localWakeAvailable;
    },
    async connect(request) {
      if (!inner.available) throw new VoxReservationError("unavailable");
      await client.reserve(request.owner, request.callId, request.signal);
      if (request.signal.aborted) throw new VoxReservationError("cancelled");
      return inner.connect(request);
    },
    async endCall(owner, callId) {
      const results = await Promise.allSettled([
        client.end(owner, callId),
        Promise.resolve().then(() => inner.endCall?.(owner, callId)),
      ]);
      if (results.some((result) => result.status === "rejected"))
        throw new VoxReservationError("unavailable");
    },
  };
}
