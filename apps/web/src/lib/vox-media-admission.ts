/** Cookie-bound media admission. Construction never opens a room or microphone. */
import type { VoxTransportRequest } from "./vox-call";
import type { VoxMediaAdmission, VoxMediaLease } from "./vox-livekit";
import type { VoxReservationClient } from "./vox-reservations";

const uuid =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const opaque = {
  room_name: /^vx-[0-9a-f]{32}$/,
  participant_identity: /^vc-[0-9a-f]{32}$/,
  agent_identity: /^va-[0-9a-f]{32}$/,
};
const date =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;
const unavailable = () => new Error("The Vox media connection is unavailable.");
const bindingKeys = [
  "connection_id",
  "client_call_id",
  "generation",
  "room_name",
  "participant_identity",
  "agent_identity",
  "expires_at",
] as const;

type Binding = {
  connection_id: string;
  client_call_id: string;
  generation: number;
  room_name: string;
  participant_identity: string;
  agent_identity: string;
  expires_at: string;
};

function record(
  raw: unknown,
  keys: readonly string[],
): Record<string, unknown> {
  if (
    !raw ||
    typeof raw !== "object" ||
    Array.isArray(raw) ||
    Object.keys(raw).length !== keys.length ||
    Object.keys(raw).some((key) => !keys.includes(key))
  )
    throw unavailable();
  return raw as Record<string, unknown>;
}

function binding(
  value: Record<string, unknown>,
  request: VoxTransportRequest,
  generation: number,
  deadline: number,
  now: number,
): Binding {
  if (
    typeof value.connection_id !== "string" ||
    !uuid.test(value.connection_id) ||
    value.client_call_id !== request.callId ||
    value.generation !== generation ||
    typeof value.expires_at !== "string" ||
    !date.test(value.expires_at)
  )
    throw unavailable();
  const expiry = Date.parse(value.expires_at);
  if (
    !Number.isFinite(now) ||
    !Number.isFinite(expiry) ||
    expiry <= now ||
    expiry > deadline ||
    expiry > now + 120_000
  )
    throw unavailable();
  for (const key of [
    "room_name",
    "participant_identity",
    "agent_identity",
  ] as const) {
    if (typeof value[key] !== "string" || !opaque[key].test(value[key]))
      throw unavailable();
  }
  return value as unknown as Binding;
}

/** The server and a separately qualified capture stack must both allow the call. */
export function createVoxMediaAdmission(options: {
  reservations: VoxReservationClient;
  qualified: boolean;
  now?: () => number;
  requestId?: () => string;
}): VoxMediaAdmission {
  return {
    available: options.qualified === true,
    async join(request) {
      if (!options.qualified || request.signal.aborted) throw unavailable();
      const now = options.now ?? Date.now;
      const owner = Object.freeze({ ...request.owner });
      const callId = request.callId;
      const authority = options.reservations.mediaAuthority(owner, callId);
      const requestId = (options.requestId ?? (() => crypto.randomUUID()))();
      const value = record(
        await options.reservations.mediaRequest(
          owner,
          callId,
          authority.generation,
          { action: "join", requestId },
          request.signal,
        ),
        [...bindingKeys, "server_url", "token", "token_expires_at"],
      );
      const admitted = binding(
        value,
        request,
        authority.generation,
        authority.expiresAt,
        now(),
      );
      if (
        typeof value.server_url !== "string" ||
        value.server_url.length > 2048 ||
        typeof value.token !== "string" ||
        value.token.length > 8192 ||
        !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value.token)
      )
        throw unavailable();
      const url = new URL(value.server_url);
      if (
        url.protocol !== "wss:" ||
        !url.hostname ||
        url.username ||
        url.password ||
        url.search ||
        url.hash
      )
        throw unavailable();
      const expiresAt = Date.parse(admitted.expires_at);
      const tokenExpiresAt =
        typeof value.token_expires_at === "string" &&
        date.test(value.token_expires_at)
          ? Date.parse(value.token_expires_at)
          : NaN;
      if (
        !Number.isFinite(tokenExpiresAt) ||
        tokenExpiresAt <= now() ||
        tokenExpiresAt > now() + 120_000 ||
        tokenExpiresAt > expiresAt
      )
        throw unavailable();
      let liveExpiresAt = expiresAt;
      let closing: Promise<void> | null = null;
      const lease: VoxMediaLease = {
        owner,
        callId,
        connectionId: admitted.connection_id,
        generation: admitted.generation,
        roomName: admitted.room_name,
        participantIdentity: admitted.participant_identity,
        agentIdentity: admitted.agent_identity,
        serverUrl: value.server_url,
        token: value.token,
        tokenExpiresAt,
        expiresAt,
        async renew(signal) {
          if (closing || signal.aborted || liveExpiresAt <= now())
            throw unavailable();
          const current = options.reservations.mediaAuthority(owner, callId);
          if (current.generation !== admitted.generation) throw unavailable();
          const renewed = binding(
            record(
              await options.reservations.mediaRequest(
                owner,
                callId,
                admitted.generation,
                { action: "renew", connectionId: admitted.connection_id },
                signal,
              ),
              bindingKeys,
            ),
            request,
            admitted.generation,
            current.expiresAt,
            now(),
          );
          const nextExpiresAt = Date.parse(renewed.expires_at);
          const latest = options.reservations.mediaAuthority(owner, callId);
          if (
            closing ||
            signal.aborted ||
            liveExpiresAt <= now() ||
            latest.generation !== admitted.generation ||
            nextExpiresAt < liveExpiresAt ||
            nextExpiresAt > latest.expiresAt ||
            bindingKeys.some(
              (key) => key !== "expires_at" && renewed[key] !== admitted[key],
            )
          )
            throw unavailable();
          liveExpiresAt = nextExpiresAt;
          return {
            connectionId: admitted.connection_id,
            generation: admitted.generation,
            expiresAt: nextExpiresAt,
          };
        },
        async loadWakeConfig(signal) {
          const current = options.reservations.mediaAuthority(owner, callId);
          if (
            closing ||
            signal.aborted ||
            request.signal.aborted ||
            liveExpiresAt <= now() ||
            !request.mayListenForWake() ||
            current.generation !== admitted.generation
          )
            throw unavailable();
          const raw = await options.reservations.mediaRequest(
            owner,
            callId,
            admitted.generation,
            { action: "wake-config", connectionId: admitted.connection_id },
            signal,
          );
          const latest = options.reservations.mediaAuthority(owner, callId);
          if (
            closing ||
            signal.aborted ||
            request.signal.aborted ||
            liveExpiresAt <= now() ||
            !request.mayListenForWake() ||
            latest.generation !== admitted.generation
          )
            throw unavailable();
          if (raw === null) return null;
          const config = record(raw, [
            "client_call_id",
            "connection_id",
            "generation",
            "expires_at",
            "engine",
            "access_key",
            "keyword_public_path",
            "model_public_path",
            "label",
          ]);
          const expiry =
            typeof config.expires_at === "string" &&
            date.test(config.expires_at)
              ? Date.parse(config.expires_at)
              : NaN;
          const asset = (value: unknown, suffix: string) => {
            if (
              typeof value !== "string" ||
              value.length > 512 ||
              !/^\/[A-Za-z0-9_./-]+$/.test(value) ||
              value.includes("//") ||
              !value.endsWith(suffix)
            )
              return false;
            const parts = value.slice(1).split("/");
            return (
              parts.every((part) => !["", ".", ".."].includes(part)) &&
              /^[A-Za-z0-9]/.test(parts[parts.length - 1] ?? "") &&
              parts.some((part) =>
                /(?:^|[._-])v[1-9][0-9]*(?:[._-]|$)/.test(part),
              )
            );
          };
          if (
            config.client_call_id !== callId ||
            config.connection_id !== admitted.connection_id ||
            config.generation !== admitted.generation ||
            config.engine !== "porcupine" ||
            config.label !== "Hey NavoX" ||
            !Number.isFinite(expiry) ||
            expiry <= now() ||
            expiry > liveExpiresAt ||
            expiry > latest.expiresAt ||
            expiry > now() + 120_000 ||
            typeof config.access_key !== "string" ||
            !config.access_key.trim() ||
            config.access_key.length > 1024 ||
            /[\r\n]/.test(config.access_key) ||
            !asset(config.keyword_public_path, ".ppn") ||
            !asset(config.model_public_path, ".pv")
          )
            throw unavailable();
          return {
            accessKey: config.access_key,
            keywordPublicPath: config.keyword_public_path as string,
            modelPublicPath: config.model_public_path as string,
            label: "Hey NavoX",
          };
        },
        async submitWakeText(text, signal) {
          if (
            closing ||
            signal.aborted ||
            liveExpiresAt <= now() ||
            !request.mayTransmitAudio() ||
            !text.trim() ||
            text.length > 4000
          )
            throw unavailable();
          const current = options.reservations.mediaAuthority(owner, callId);
          if (current.generation !== admitted.generation) throw unavailable();
          const requestId = (
            options.requestId ?? (() => crypto.randomUUID())
          )();
          const result = record(
            await options.reservations.mediaRequest(
              owner,
              callId,
              admitted.generation,
              {
                action: "wake",
                connectionId: admitted.connection_id,
                requestId,
                text,
              },
              signal,
            ),
            ["request_id", "status", "delivery"],
          );
          if (
            closing ||
            signal.aborted ||
            result.request_id !== requestId ||
            result.status !== "accepted" ||
            result.delivery !== "queued"
          )
            throw unavailable();
        },
        close() {
          // Cleanup has an independent bounded lifetime, including after the
          // browser aborts or ends its reservation. Never retry an unknown join.
          closing ??= options.reservations
            .mediaRequest(owner, callId, admitted.generation, {
              action: "close",
              connectionId: admitted.connection_id,
            })
            .then(() => {});
          return closing;
        },
      };
      return lease;
    },
  };
}
