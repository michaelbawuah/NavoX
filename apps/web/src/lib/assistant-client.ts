import type {
  AssistantEmailAction,
  AssistantEmailDraft,
  AssistantErrorCode,
  AssistantMessageResponse,
  AssistantModality,
  AssistantSessionView,
  DeleteAssistantSessionResponse,
} from "@navox/contracts";

export class AssistantClientError extends Error {
  constructor(
    public code: AssistantErrorCode,
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "AssistantClientError";
  }
}

const CODE_MESSAGES: Partial<Record<AssistantErrorCode, string>> = {
  unauthorized: "Sign in to use the assistant.",
  forbidden: "This request must come from the NavoX app.",
  not_found: "That conversation is no longer available.",
  expired: "That conversation expired. Start a new one.",
  conflict: "That question was already sent with different wording.",
  invalid_request:
    "That request was not valid. Check the wording and try again.",
  unsupported: "This conversation reached its limit. Start a new one.",
  unavailable: "The assistant could not answer right now. Please try again.",
  misconfigured: "The assistant is not configured for this deployment.",
};

interface ErrorPayload {
  error?: { code?: string; message?: string };
}

async function readError(response: Response): Promise<AssistantClientError> {
  let payload: ErrorPayload | null = null;
  try {
    payload = (await response.json()) as ErrorPayload;
  } catch {
    payload = null;
  }
  const raw = payload?.error?.code;
  const code = (
    raw && raw in CODE_MESSAGES ? raw : "unavailable"
  ) as AssistantErrorCode;
  const message =
    payload?.error?.message ??
    CODE_MESSAGES[code] ??
    "The assistant could not answer right now. Please try again.";
  return new AssistantClientError(code, response.status, message);
}

async function assistantRequest<T>(
  path: string,
  options: { method: "GET" | "POST" | "PATCH" | "DELETE"; body?: unknown },
): Promise<T> {
  const response = await fetch(path, {
    method: options.method,
    credentials: "include",
    cache: "no-store",
    headers: { "content-type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function assistantSessionPath(sessionId?: string): string {
  const base = "/api/v1/assistant/sessions";
  return sessionId ? `${base}/${encodeURIComponent(sessionId)}` : base;
}

/** Raised when the browser cannot produce a UUID the server will accept. */
export class AssistantRequestIdError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AssistantRequestIdError";
  }
}

export interface RequestIdSource {
  randomUUID?: () => string;
  getRandomValues?: (array: Uint8Array) => Uint8Array;
}

function uuidFromRandomBytes(bytes: Uint8Array): string {
  const octets = Uint8Array.from(bytes);
  octets[6] = ((octets[6] ?? 0) & 0x0f) | 0x40;
  octets[8] = ((octets[8] ?? 0) & 0x3f) | 0x80;
  const hex = [...octets]
    .map((octet) => octet.toString(16).padStart(2, "0"))
    .join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

/**
 * The server requires a UUID request identifier, so this never falls back to a
 * time-and-random string that would fail validation with a silent 400.
 */
export function newAssistantRequestId(source?: RequestIdSource): string {
  const scope =
    source ??
    (typeof globalThis.crypto === "undefined"
      ? {}
      : (globalThis.crypto as RequestIdSource));
  if (typeof scope.randomUUID === "function") {
    try {
      return scope.randomUUID();
    } catch {
      // Fall through to getRandomValues.
    }
  }
  if (typeof scope.getRandomValues === "function") {
    try {
      return uuidFromRandomBytes(scope.getRandomValues(new Uint8Array(16)));
    } catch {
      // Fall through to the explicit failure below.
    }
  }
  throw new AssistantRequestIdError(
    "This browser cannot create a secure request identifier. Reload the page and try again.",
  );
}

export async function createAssistantSession(): Promise<AssistantSessionView> {
  const body = await assistantRequest<{ session: AssistantSessionView }>(
    assistantSessionPath(),
    { method: "POST", body: {} },
  );
  return body.session;
}

const SESSION_STORAGE_KEY = "navox.assistant.session.v1";

/** A browser-tab pointer only; the server still authorizes every session read. */
export async function resumeOrCreateAssistantSession(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null,
): Promise<AssistantSessionView> {
  let prior: string | null = null;
  try {
    prior = storage?.getItem(SESSION_STORAGE_KEY) ?? null;
  } catch {
    /* Storage may be disabled. */
  }
  if (prior) {
    try {
      return await loadAssistantSession(prior);
    } catch (error) {
      if (
        !(error instanceof AssistantClientError) ||
        (error.code !== "not_found" && error.code !== "expired")
      )
        throw error;
      try {
        storage?.removeItem(SESSION_STORAGE_KEY);
      } catch {
        /* Storage may be disabled. */
      }
    }
  }
  const session = await createAssistantSession();
  try {
    storage?.setItem(SESSION_STORAGE_KEY, session.id);
  } catch {
    /* Storage may be disabled. */
  }
  return session;
}

export function forgetAssistantSession(
  storage: Pick<Storage, "removeItem"> | null,
): void {
  try {
    storage?.removeItem(SESSION_STORAGE_KEY);
  } catch {
    /* Storage may be disabled. */
  }
}

export async function loadAssistantSession(
  sessionId: string,
): Promise<AssistantSessionView> {
  const body = await assistantRequest<{ session: AssistantSessionView }>(
    assistantSessionPath(sessionId),
    { method: "GET" },
  );
  return body.session;
}

export async function deleteAssistantSession(sessionId: string): Promise<void> {
  await assistantRequest<DeleteAssistantSessionResponse>(
    assistantSessionPath(sessionId),
    {
      method: "DELETE",
      body: {},
    },
  );
}

export interface AssistantTurnInput {
  requestId: string;
  text: string;
  modality: AssistantModality;
  timezone?: string | null;
  referents?: string[];
}

export async function submitAssistantTurn(
  sessionId: string,
  input: AssistantTurnInput,
): Promise<AssistantMessageResponse> {
  return assistantRequest<AssistantMessageResponse>(
    `${assistantSessionPath(sessionId)}/messages`,
    {
      method: "POST",
      body: {
        request_id: input.requestId,
        text: input.text,
        modality: input.modality,
        timezone: input.timezone ?? null,
        referents: input.referents ?? [],
      },
    },
  );
}

/** Mirrors the SPEC-005 transcript bound on the assistant question text. */
export const MAX_TRANSCRIPT_LENGTH = 500;

/**
 * Uploads one already-validated WAV clip to the session-scoped Next route.
 * The body is the raw container: no provider, model, credential or URL ever
 * travels from the browser, and an abort rejects the request unread.
 */
export async function transcribeAssistantSpeech(
  sessionId: string,
  wav: Uint8Array<ArrayBuffer>,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(
    `${assistantSessionPath(sessionId)}/speech/transcribe`,
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "content-type": "audio/wav" },
      body: wav,
      signal,
    },
  );
  if (!response.ok) throw await readError(response);
  let payload: { text?: unknown };
  try {
    payload = (await response.json()) as { text?: unknown };
  } catch {
    throw new AssistantClientError(
      "unavailable",
      502,
      "The recording could not be transcribed. Type your question instead.",
    );
  }
  const text = typeof payload?.text === "string" ? payload.text.trim() : "";
  if (!text) {
    throw new AssistantClientError(
      "unavailable",
      502,
      "No speech was heard. Type your question instead.",
    );
  }
  if (text.length > MAX_TRANSCRIPT_LENGTH) {
    throw new AssistantClientError(
      "unavailable",
      502,
      "That recording was too long. Record a shorter question.",
    );
  }
  return text;
}

/** One bounded MP3 answer; the SPEC-005 speech route never exceeds 2 MiB. */
export const MAX_SPEECH_AUDIO_BYTES = 2 * 1024 * 1024;

export function assistantTurnPath(sessionId: string, turnId: string): string {
  return `${assistantSessionPath(sessionId)}/turns/${encodeURIComponent(turnId)}`;
}

/**
 * Requests the saved-turn speech route with session and turn selectors only.
 * The server derives and bounds the spoken text; no answer text, provider,
 * model, voice or destination ever travels from the browser.
 */
export async function synthesizeAssistantSpeech(
  sessionId: string,
  turnId: string,
  signal?: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  const response = await fetch(
    `${assistantTurnPath(sessionId, turnId)}/speech`,
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: "{}",
      signal,
    },
  );
  if (!response.ok) throw await readError(response);
  const mediaType = (response.headers.get("content-type") ?? "")
    .split(";")[0]
    ?.trim()
    .toLowerCase();
  if (mediaType !== "audio/mpeg") {
    throw new AssistantClientError(
      "unavailable",
      502,
      "The spoken answer could not be played. Read the answer instead.",
    );
  }
  const declared = Number(response.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > MAX_SPEECH_AUDIO_BYTES) {
    throw new AssistantClientError(
      "unavailable",
      502,
      "The spoken answer could not be played. Read the answer instead.",
    );
  }
  const audio = new Uint8Array(await response.arrayBuffer());
  if (audio.byteLength === 0 || audio.byteLength > MAX_SPEECH_AUDIO_BYTES) {
    throw new AssistantClientError(
      "unavailable",
      502,
      "The spoken answer could not be played. Read the answer instead.",
    );
  }
  return audio;
}

function draftPath(sessionId: string, draftId?: string): string {
  const base = `${assistantSessionPath(sessionId)}/email-drafts`;
  return draftId ? `${base}/${encodeURIComponent(draftId)}` : base;
}

export async function loadAssistantEmailDraft(
  sessionId: string,
  sourceTurnId: string,
  draftId: string,
): Promise<AssistantEmailDraft> {
  const response = await assistantRequest<{ draft: AssistantEmailDraft }>(
    `${draftPath(sessionId, draftId)}?source_turn_id=${encodeURIComponent(sourceTurnId)}`,
    { method: "GET" },
  );
  return response.draft;
}

export async function createAssistantEmailDraft(
  sessionId: string,
  sourceTurnId: string,
  instructions: string,
): Promise<AssistantEmailDraft> {
  const response = await assistantRequest<{ draft: AssistantEmailDraft }>(
    draftPath(sessionId),
    {
      method: "POST",
      body: { source_turn_id: sourceTurnId, instructions },
    },
  );
  return response.draft;
}

export async function reviseAssistantEmailDraft(
  sessionId: string,
  sourceTurnId: string,
  draft: AssistantEmailDraft,
  subject: string,
  body: string,
): Promise<AssistantEmailDraft> {
  const response = await assistantRequest<{ draft: AssistantEmailDraft }>(
    draftPath(sessionId, draft.id),
    {
      method: "PATCH",
      body: {
        source_turn_id: sourceTurnId,
        expected_version: draft.current_version,
        subject,
        body,
      },
    },
  );
  return response.draft;
}

export async function prepareAssistantEmailDraft(
  sessionId: string,
  sourceTurnId: string,
  draft: AssistantEmailDraft,
): Promise<AssistantEmailAction> {
  const response = await assistantRequest<{ action: AssistantEmailAction }>(
    `${draftPath(sessionId, draft.id)}/prepare`,
    {
      method: "POST",
      body: {
        source_turn_id: sourceTurnId,
        expected_version: draft.current_version,
        request_id: newAssistantRequestId(),
      },
    },
  );
  return response.action;
}

export async function approveAssistantEmailDraft(
  sessionId: string,
  sourceTurnId: string,
  draftId: string,
  action: AssistantEmailAction,
): Promise<AssistantEmailAction> {
  const response = await assistantRequest<{ action: AssistantEmailAction }>(
    `${draftPath(sessionId, draftId)}/approve`,
    {
      method: "POST",
      body: {
        source_turn_id: sourceTurnId,
        request_id: newAssistantRequestId(),
        expected_payload_hash: action.payload_hash,
        draft_version: action.payload.draft_version,
        confirmed: true,
      },
    },
  );
  return response.action;
}

export async function loadAssistantEmailAction(
  sessionId: string,
  sourceTurnId: string,
  draftId: string,
): Promise<AssistantEmailAction> {
  const response = await assistantRequest<{ action: AssistantEmailAction }>(
    `${draftPath(sessionId, draftId)}/action?source_turn_id=${encodeURIComponent(sourceTurnId)}`,
    { method: "GET" },
  );
  return response.action;
}

export function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    return null;
  }
}

/**
 * Keeps one request identifier per pending submission so a timeout or a
 * transient failure replays the saved turn instead of creating a second one.
 */
export class AssistantTurnLedger {
  private pending: { key: string; requestId: string } | null = null;

  requestIdFor(key: string): string {
    if (this.pending?.key === key) return this.pending.requestId;
    const requestId = newAssistantRequestId();
    this.pending = { key, requestId };
    return requestId;
  }

  resolve(): void {
    this.pending = null;
  }

  forget(): void {
    this.pending = null;
  }
}
