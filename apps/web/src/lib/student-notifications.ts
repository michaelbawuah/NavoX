export type ClassAlertSettings = {
  enabled: boolean;
  next_class: boolean;
  leave_now: boolean;
  lead_minutes: number;
  quiet_start: string;
  quiet_end: string;
  daily_cap: number;
};
export type ClassAlertStatus = {
  configured: boolean;
  public_key: string | null;
  settings: ClassAlertSettings;
  devices: { id: string; endpoint_hash: string }[];
  timezone: string;
  notice: string;
};
const apiBase =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class NotificationRequestError extends Error {}

export function notificationError(cause: unknown, fallback: string): string {
  return cause instanceof NotificationRequestError ? cause.message : fallback;
}

export async function classAlertRequest(
  path = "",
  method = "GET",
  body?: unknown,
  accountKey?: string,
): Promise<unknown> {
  const response = await fetch(`${apiBase}/student/notifications${path}`, {
    method,
    credentials: "include",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
    headers: {
      ...(accountKey ? { "X-NavoX-Account": accountKey } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined
      ? {}
      : {
          body: JSON.stringify(body),
        }),
  });
  const payload: unknown =
    response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    throw new NotificationRequestError(
      response.status === 401
        ? "Sign in again to manage your reminders."
        : response.status === 409
          ? "Your account or reminder settings changed. Open Settings again before continuing."
          : response.status === 422
            ? "Check your reminder times and limits, then try again."
            : response.status === 503
              ? "Class reminders are temporarily unavailable. Please try again later."
              : "Your reminders could not be updated. Please try again.",
    );
  }
  return payload;
}

export function readAlertStatus(value: unknown): ClassAlertStatus {
  if (!value || typeof value !== "object")
    throw new Error("Notification settings could not be loaded.");
  const status = value as Partial<ClassAlertStatus>;
  const settings = status.settings;
  if (
    typeof status.configured !== "boolean" ||
    !settings ||
    typeof settings.enabled !== "boolean" ||
    typeof settings.next_class !== "boolean" ||
    typeof settings.leave_now !== "boolean" ||
    typeof settings.lead_minutes !== "number" ||
    typeof settings.daily_cap !== "number" ||
    typeof settings.quiet_start !== "string" ||
    typeof settings.quiet_end !== "string" ||
    typeof status.timezone !== "string" ||
    typeof status.notice !== "string" ||
    !Array.isArray(status.devices) ||
    status.devices.some(
      (device) =>
        typeof device?.id !== "string" ||
        typeof device?.endpoint_hash !== "string",
    ) ||
    (status.public_key !== null && typeof status.public_key !== "string")
  )
    throw new Error("Notification settings could not be loaded.");
  return status as ClassAlertStatus;
}

export function vapidKey(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(
    value.replace(/-/g, "+").replace(/_/g, "/") +
      "=".repeat((4 - (value.length % 4)) % 4),
  );
  const result = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; ++index)
    result[index] = binary.charCodeAt(index);
  if (result.length !== 65 || result[0] !== 4)
    throw new Error("Notification setup is unavailable.");
  return result;
}
export async function endpointDigest(endpoint: string): Promise<string> {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(endpoint),
  );
  return Array.from(new Uint8Array(bytes), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}
