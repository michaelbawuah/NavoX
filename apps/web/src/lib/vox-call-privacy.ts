"use client";

import { useEffect, useState } from "react";
import type { VoxCallState } from "./vox-call";

export type VoxPrivacyMode = "off-record" | "saved-history" | "history-off";
export type VoxCallPrivacy = {
  user_id: string;
  workspace_id: string;
  client_call_id: string;
  generation: number;
  privacy_generation: number;
  mode: VoxPrivacyMode;
  retention_days: 30 | 90 | null;
  expires_at: string;
};
const keys = [
  "user_id",
  "workspace_id",
  "client_call_id",
  "generation",
  "privacy_generation",
  "mode",
  "retention_days",
  "expires_at",
];
const MAX_BYTES = 8192;
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function readVoxCallPrivacy(
  value: unknown,
  expected: { userId: string; workspaceId: string; callId: string },
  now = Date.now(),
): VoxCallPrivacy {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Call privacy could not be checked.");
  const row = value as Record<string, unknown>;
  const expiry =
    typeof row.expires_at === "string" ? Date.parse(row.expires_at) : NaN;
  if (
    Object.keys(row).length !== keys.length ||
    Object.keys(row).some((key) => !keys.includes(key)) ||
    row.user_id !== expected.userId ||
    row.workspace_id !== expected.workspaceId ||
    row.client_call_id !== expected.callId ||
    ![expected.userId, expected.workspaceId, expected.callId].every((id) =>
      UUID.test(id),
    ) ||
    ![row.generation, row.privacy_generation].every(
      (number) =>
        Number.isInteger(number) &&
        Number(number) >= 0 &&
        Number(number) <= 2147483647,
    ) ||
    !["off-record", "saved-history", "history-off"].includes(
      String(row.mode),
    ) ||
    ![30, 90, null].includes(row.retention_days as number | null) ||
    (row.mode !== "saved-history" && row.retention_days !== null) ||
    !Number.isFinite(now) ||
    !Number.isFinite(expiry) ||
    expiry <= now ||
    expiry > now + 120000
  )
    throw new Error("Call privacy could not be checked.");
  return row as VoxCallPrivacy;
}

export function voxPrivacyLabel(value: VoxCallPrivacy): string {
  if (value.mode === "off-record") return "Off the record";
  if (value.mode === "history-off") return "History off";
  return value.retention_days === null
    ? "History on · no auto-delete"
    : `History on · ${value.retention_days} days`;
}

async function boundedJson(response: Response): Promise<unknown> {
  if (
    !response.ok ||
    !response.headers.get("content-type")?.startsWith("application/json") ||
    Number(response.headers.get("content-length") ?? 0) > MAX_BYTES ||
    !response.body
  )
    throw new Error("Call privacy could not be checked.");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES)
        throw new Error("Call privacy could not be checked.");
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

/** Only read metadata after an explicit call. A reservation's legacy mode is not consent. */
export function useVoxCallPrivacy(state: VoxCallState | undefined) {
  const scope =
    state?.ownerVerified &&
    state.owner &&
    state.callId &&
    ["connecting", "active", "reconnecting", "standby"].includes(state.phase)
      ? {
          userId: state.owner.userId,
          workspaceId: state.owner.workspaceId,
          callId: state.callId,
        }
      : null;
  const key = scope
    ? `${scope.workspaceId}:${scope.userId}:${scope.callId}:${state?.phase}`
    : "";
  const [result, setResult] = useState<{
    key: string;
    value: VoxCallPrivacy | null;
    unavailable: boolean;
  } | null>(null);
  useEffect(() => {
    if (!key) {
      setResult(null);
      return;
    }
    const [workspaceId, userId, callId] = key.split(":");
    let live = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let active: AbortController | undefined;
    async function load() {
      if (!live) return;
      const controller = new AbortController();
      active = controller;
      // Keep a still-valid label quiet during a background refresh. Scope or
      // phase changes, expiry and any failed refresh clear it immediately.
      setResult((previous) =>
        previous?.key === key &&
        previous.value &&
        Date.parse(previous.value.expires_at) > Date.now()
          ? previous
          : { key, value: null, unavailable: false },
      );
      const timeout = setTimeout(() => controller.abort(), 10000);
      let delay = 30000;
      try {
        const api =
          process.env.NEXT_PUBLIC_API_BASE_URL ??
          "http://localhost:8000/api/v1";
        const response = await fetch(
          `${api.replace(/\/$/, "")}/vox/calls/${callId}/privacy`,
          {
            method: "GET",
            credentials: "include",
            cache: "no-store",
            redirect: "error",
            signal: controller.signal,
            headers: { "X-NavoX-Account": `${workspaceId}:${userId}` },
          },
        );
        const value = readVoxCallPrivacy(await boundedJson(response), {
          workspaceId,
          userId,
          callId,
        });
        if (!live || controller.signal.aborted) return;
        setResult({ key, value, unavailable: false });
        delay = Math.max(
          0,
          Math.min(30000, Date.parse(value.expires_at) - Date.now()),
        );
      } catch {
        if (live) setResult({ key, value: null, unavailable: true });
      } finally {
        clearTimeout(timeout);
        if (live) retry = setTimeout(() => void load(), delay);
      }
    }
    void load();
    return () => {
      live = false;
      active?.abort();
      clearTimeout(retry);
    };
  }, [key]);
  return {
    value:
      key &&
      result?.key === key &&
      result.value &&
      Date.parse(result.value.expires_at) > Date.now()
        ? result.value
        : null,
    label:
      key && result?.key === key && result.unavailable
        ? "Privacy status unavailable"
        : "Checking privacy settings…",
  };
}
