/** Owner-bound Outlook contracts. Provider text remains text, never HTML. */
export interface OutlookConnection {
  id: string;
  email: string;
  status: string;
  read_enabled: boolean;
  send_enabled: boolean;
}
export interface OutlookStatus {
  configured: boolean;
  connections: OutlookConnection[];
  notice: string | null;
}
export interface OutlookMessage {
  id: string;
  sender: string;
  subject: string;
  preview: string;
  sent_at: string | null;
  open_url: string | null;
  reason: string;
}
export interface OutlookMessageDetail extends OutlookMessage {
  to: string[];
  body: string;
  truncated: boolean;
}
export interface OutlookDraft {
  id: string;
  revision: number;
  payload_hash: string;
  connection_id: string;
  source_message_id: string;
  sender: string;
  to: string[];
  subject: string;
  body: string;
  status: "review" | "executing" | "uncertain" | "completed" | "discarded";
  expires_at: string | null;
  verified_at: string | null;
  notice: string | null;
  content_available: boolean;
}
const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const uuid =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function invalid(): never {
  throw new Error("Outlook returned an unreadable response. Try again.");
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 10000): string {
  if (typeof value !== "string" || value.length > max) return invalid();
  return value;
}
function identifier(value: unknown): string {
  const result = text(value, 36);
  return uuid.test(result) ? result : invalid();
}
function boolean(value: unknown): boolean {
  return typeof value === "boolean" ? value : invalid();
}
function optionalText(value: unknown, max = 10000): string | null {
  return value === null || value === undefined ? null : text(value, max);
}
function instant(value: unknown): string | null {
  const result = optionalText(value, 64);
  if (result !== null && !Number.isFinite(Date.parse(result))) return invalid();
  return result;
}
function list<T>(value: unknown, parse: (item: unknown) => T, max = 500): T[] {
  if (!Array.isArray(value) || value.length > max) return invalid();
  return value.map(parse);
}
function recipients(value: unknown): string[] {
  if (typeof value === "string") return [text(value, 320)];
  return list(value, (v) => text(v, 320), 20);
}
export function outlookOpenUrl(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  const url = new URL(text(value, 5000));
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    (url.port && url.port !== "443") ||
    ![
      "outlook.office.com",
      "outlook.office365.com",
      "outlook.live.com",
    ].includes(url.hostname)
  )
    return invalid();
  return url.toString();
}
export function outlookAuthorizationUrl(value: unknown): string {
  const url = new URL(text(value, 12000));
  if (
    url.protocol !== "https:" ||
    url.hostname !== "login.microsoftonline.com" ||
    (url.port && url.port !== "443") ||
    url.username ||
    url.password ||
    url.hash ||
    !/^\/(?:common|organizations|consumers|[0-9a-f-]{36})\/oauth2\/v2\.0\/authorize$/i.test(
      url.pathname,
    )
  )
    return invalid();
  return url.toString();
}
export function readOutlookStatus(value: unknown): OutlookStatus {
  const v = object(value);
  return {
    configured: boolean(v.configured),
    notice: optionalText(v.notice, 2000),
    connections: list(
      v.connections,
      (item) => {
        const c = object(item);
        return {
          id: identifier(c.id),
          email: text(c.email, 320),
          status: text(c.status, 40),
          read_enabled: boolean(c.read_enabled),
          send_enabled: boolean(c.send_enabled),
        };
      },
      50,
    ),
  };
}
export function canReadOutlook(connection: OutlookConnection): boolean {
  return (
    connection.read_enabled &&
    ["CONNECTED", "DEGRADED"].includes(connection.status)
  );
}
export function readOutlookMessage(value: unknown): OutlookMessage {
  const v = object(value);
  return {
    id: text(v.id, 2048),
    sender: text(v.sender, 500),
    subject: text(v.subject, 2000),
    preview: v.preview === undefined ? "" : text(v.preview, 5000),
    sent_at: instant(v.sent_at),
    open_url: outlookOpenUrl(v.open_url),
    reason: v.reason === undefined ? "" : text(v.reason, 1000),
  };
}
export function readOutlookMessages(value: unknown) {
  const v = object(value);
  return {
    messages: list(v.messages, readOutlookMessage),
    has_more: boolean(v.has_more),
    notice: optionalText(v.notice, 2000),
  };
}
export function readOutlookMessageDetail(value: unknown): OutlookMessageDetail {
  const v = object(value);
  return {
    ...readOutlookMessage(v),
    to: recipients(v.to),
    body: text(v.body, 200000),
    truncated: v.truncated === undefined ? false : boolean(v.truncated),
  };
}
export function readOutlookDraft(value: unknown): OutlookDraft {
  const v = object(value);
  if (
    !Number.isSafeInteger(v.revision) ||
    Number(v.revision) < 1 ||
    typeof v.payload_hash !== "string" ||
    !/^[a-f0-9]{64}$/.test(v.payload_hash)
  )
    return invalid();
  if (
    v.status !== "review" &&
    v.status !== "executing" &&
    v.status !== "uncertain" &&
    v.status !== "completed" &&
    v.status !== "discarded"
  )
    return invalid();
  return {
    id: identifier(v.id),
    revision: Number(v.revision),
    payload_hash: v.payload_hash,
    connection_id: identifier(v.connection_id),
    source_message_id: text(v.source_message_id, 2048),
    sender: text(v.sender, 500),
    to: recipients(v.to),
    subject: text(v.subject, 256),
    body: text(v.body, 12000),
    status: v.status,
    expires_at: instant(v.expires_at),
    verified_at: instant(v.verified_at),
    notice: optionalText(v.notice, 2000),
    content_available:
      v.content_available === undefined ? true : boolean(v.content_available),
  };
}
export const readOutlookDrafts = (value: unknown) =>
  list(value, readOutlookDraft);
export function readOutlookGenerated(value: unknown) {
  const v = object(value);
  return {
    to: recipients(v.to),
    subject: text(v.subject, 256),
    body: text(v.body, 12000),
  };
}
export async function outlookRequest<T>(
  accountKey: string,
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${api}/outlook${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      headers: {
        "Content-Type": "application/json",
        "X-NavoX-Account": accountKey,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(35000)])
        : AbortSignal.timeout(35000),
    });
  } catch {
    throw new Error(
      "Outlook could not be reached. Check your connection and try again.",
    );
  }
  if (response.status === 204) return parse(null);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401)
      throw new Error("Sign in again to check your Outlook mailbox.");
    if (response.status === 409)
      throw new Error(
        "Your account, message or draft changed. Reopen it before continuing.",
      );
    if (response.status === 404)
      throw new Error(
        "This Outlook mailbox, email or draft is unavailable. Reopen it before continuing.",
      );
    if (response.status === 503)
      throw new Error(
        "Outlook is temporarily unavailable. Try again in a moment.",
      );
    if (response.status === 403)
      throw new Error(
        "This Outlook action needs permission. Check Connections.",
      );
    if (response.status === 429)
      throw new Error("Outlook needs a short pause. Try again in a moment.");
    if (response.status === 422)
      throw new Error(
        "Check your reply details and review them again before continuing.",
      );
    throw new Error("Outlook could not complete this request. Try again.");
  }
  try {
    return parse(payload);
  } catch {
    return invalid();
  }
}
