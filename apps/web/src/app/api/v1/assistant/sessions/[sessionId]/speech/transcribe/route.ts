import {
  AssistantError,
  assertSameOriginMutation,
} from "@navox/assistant-runtime";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../../../../lib/assistant-route";
import {
  assistantApiBaseUrl,
  assistantOrigin,
  getAssistantRuntime,
} from "../../../../../../../../lib/assistant-server";
import {
  MAX_WAV_BYTES,
  parseAssistantWav,
  SpeechClipError,
} from "../../../../../../../../lib/assistant-wav";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** The fixed SPEC-005 destination. Path shape is never client input. */
const TRANSCRIBE_PATH = "/ai/assistant/speech/transcribe";
/** Bounded wait for the bounded provider call; the abort is the answer. */
const PROXY_TIMEOUT_MS = 30_000;
/** 500 transcript characters plus JSON envelope, with room to be refused. */
const RESPONSE_MAX_BYTES = 4_096;
/** Mirrors the SPEC-005 transcript bound on question text. */
const MAX_TRANSCRIPT_CHARACTERS = 500;

interface SpeechRouteContext {
  params: Promise<{ sessionId: string }>;
}

function tooLarge(): AssistantError {
  return new AssistantError(
    "invalid_request",
    "That recording is too large. Record a shorter question.",
  );
}

function unusableClip(): AssistantError {
  return new AssistantError(
    "invalid_request",
    "That recording could not be used. Record a clip and try again.",
  );
}

/** Reads at most one bounded clip; an oversize stream is rejected unread. */
async function readBoundedWav(
  request: Request,
): Promise<Uint8Array<ArrayBuffer>> {
  const declared = Number(request.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > MAX_WAV_BYTES) throw tooLarge();
  const body = request.body;
  if (!body) throw unusableClip();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    total += value.byteLength;
    if (total > MAX_WAV_BYTES) {
      await reader.cancel().catch(() => undefined);
      throw tooLarge();
    }
    chunks.push(value);
  }
  const audio = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    audio.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return audio;
}

/** One qualified status mapping; an upstream body never reaches the client. */
function upstreamFailure(status: number): AssistantError {
  if (status === 401) {
    return new AssistantError("unauthorized", "Sign in to use the assistant.");
  }
  if (status === 403) {
    return new AssistantError(
      "forbidden",
      "This account cannot use speech transcription.",
    );
  }
  if (status === 404) {
    return new AssistantError(
      "unsupported",
      "Speech transcription is not available in this deployment. Type your question instead.",
    );
  }
  if (status === 413) return tooLarge();
  if (status === 415 || status === 422) {
    return new AssistantError(
      "invalid_request",
      "That recording could not be used. Record a clip and try again.",
    );
  }
  if (status === 429) {
    return new AssistantError(
      "unavailable",
      "Speech transcription is busy right now. Type your question instead.",
      { retryable: true },
    );
  }
  if (status === 503) {
    return new AssistantError(
      "unsupported",
      "Speech transcription is not available right now. Type your question instead.",
    );
  }
  return new AssistantError(
    "unavailable",
    "The recording could not be transcribed. Type your question instead.",
    { retryable: true },
  );
}

/** Reads at most `limit` bytes of a JSON envelope; larger is malformed. */
async function readBoundedText(
  response: Response,
  limit: number,
): Promise<string> {
  const declared = Number(response.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > limit) {
    throw upstreamFailure(502);
  }
  if (!response.body) return await response.text();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel().catch(() => undefined);
      throw upstreamFailure(502);
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(bytes);
}

/**
 * Forwards one validated clip to the server-configured SPEC-005 route and
 * returns only bounded question text. No provider, model, credential or URL
 * travels from the browser, and no upstream body or audio is echoed.
 */
async function transcribeUpstream(
  cookie: string,
  audio: Uint8Array<ArrayBuffer>,
  requestSignal: AbortSignal,
): Promise<string> {
  let url: URL;
  try {
    const base = assistantApiBaseUrl().replace(/\/+$/, "");
    url = new URL(`${base}${TRANSCRIBE_PATH}`);
  } catch {
    throw new AssistantError(
      "misconfigured",
      "The assistant speech route is not configured for this deployment.",
    );
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new AssistantError(
      "misconfigured",
      "The assistant speech route is not configured for this deployment.",
    );
  }
  const controller = new AbortController();
  const abortOnDisconnect = () => controller.abort();
  if (requestSignal.aborted) controller.abort();
  else
    requestSignal.addEventListener("abort", abortOnDisconnect, { once: true });
  const timer = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        cookie,
        "content-type": "audio/wav",
        accept: "application/json",
      },
      body: audio,
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok) throw upstreamFailure(response.status);
    const raw = await readBoundedText(response, RESPONSE_MAX_BYTES);
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch {
      throw upstreamFailure(502);
    }
    const text =
      payload && typeof payload === "object" && "text" in payload
        ? (payload as { text?: unknown }).text
        : null;
    if (typeof text !== "string") throw upstreamFailure(502);
    const question = text.trim();
    if (!question || question.length > MAX_TRANSCRIPT_CHARACTERS) {
      throw upstreamFailure(502);
    }
    return question;
  } catch (error) {
    if (error instanceof AssistantError) throw error;
    if (requestSignal.aborted) {
      throw new AssistantError(
        "unavailable",
        "The recording was canceled. Type your question instead.",
      );
    }
    if (controller.signal.aborted) {
      throw new AssistantError(
        "unavailable",
        "The transcription took too long. Type your question instead.",
        { retryable: true },
      );
    }
    throw new AssistantError(
      "unavailable",
      "Speech transcription is not reachable right now. Type your question instead.",
      { retryable: true },
    );
  } finally {
    clearTimeout(timer);
    requestSignal.removeEventListener("abort", abortOnDisconnect);
  }
}

export async function POST(
  request: Request,
  context: SpeechRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    assertSameOriginMutation({
      method: request.method,
      origin: request.headers.get("origin"),
      requestUrl: request.url,
      secFetchSite: request.headers.get("sec-fetch-site"),
      contentType: request.headers.get("content-type"),
      configuredOrigin: assistantOrigin(),
      expectedContentType: "audio/wav",
      contentTypeMessage: "A raw audio/wav body is required.",
    });
    const { sessionId } = await context.params;
    const cookie = sessionCookie(request);
    const audio = await readBoundedWav(request);
    try {
      parseAssistantWav(audio);
    } catch (error) {
      if (error instanceof SpeechClipError) {
        throw new AssistantError("invalid_request", error.message);
      }
      throw error;
    }
    // Exact session ownership is proved before any audio leaves this process.
    await getAssistantRuntime().readSession({ cookie, session_id: sessionId });
    const text = await transcribeUpstream(cookie, audio, request.signal);
    return Response.json({ text });
  });
}
