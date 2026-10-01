import {
  AssistantError,
  assertSameOriginMutation,
} from "@navox/assistant-runtime";
import { spokenTextForTurn } from "@navox/assistant-runtime/voice";
import { MAX_SPEECH_AUDIO_BYTES } from "../../../../../../../../../lib/assistant-client";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../../../../../lib/assistant-route";
import {
  assistantApiBaseUrl,
  assistantOrigin,
  getAssistantRuntime,
} from "../../../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** The fixed SPEC-005 destination. Path shape is never client input. */
const SYNTHESIZE_PATH = "/ai/assistant/speech/synthesize";
/** Bounded wait for the bounded provider call; the abort is the answer. */
const PROXY_TIMEOUT_MS = 30_000;

interface SpeechRouteContext {
  params: Promise<{ sessionId: string; turnId: string }>;
}

function unplayable(): AssistantError {
  return new AssistantError(
    "unavailable",
    "The spoken answer could not be played. Read the answer instead.",
    { retryable: true },
  );
}

/** One qualified status mapping; an upstream body never reaches the client. */
function upstreamFailure(status: number): AssistantError {
  if (status === 401) {
    return new AssistantError("unauthorized", "Sign in to use the assistant.");
  }
  if (status === 403) {
    return new AssistantError(
      "forbidden",
      "This account cannot play spoken answers. Read the answer instead.",
    );
  }
  if (status === 404) {
    return new AssistantError(
      "unsupported",
      "Spoken answers are not available in this deployment. Read the answer instead.",
    );
  }
  if (status === 413 || status === 415 || status === 422) {
    return new AssistantError(
      "invalid_request",
      "That answer could not be spoken. Read the answer instead.",
    );
  }
  if (status === 429) {
    return new AssistantError(
      "unavailable",
      "Spoken answers are busy right now. Read the answer instead.",
      { retryable: true },
    );
  }
  if (status === 503) {
    return new AssistantError(
      "unsupported",
      "Spoken answers are not available right now. Read the answer instead.",
    );
  }
  return unplayable();
}

/** Reads at most one bounded MP3 answer; larger is malformed. */
async function readBoundedAudio(
  response: Response,
  limit: number,
): Promise<Uint8Array<ArrayBuffer>> {
  const declared = Number(response.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > limit) {
    throw upstreamFailure(502);
  }
  if (!response.body) throw upstreamFailure(502);
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
  if (total === 0) throw upstreamFailure(502);
  const audio = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    audio.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return audio;
}

/**
 * Forwards one server-derived answer to the server-configured SPEC-005 route
 * and returns only bounded MP3. No text, provider, model, voice or credential
 * travels from the browser, and no upstream body is echoed.
 */
async function synthesizeUpstream(
  cookie: string,
  text: string,
  requestSignal: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  let url: URL;
  try {
    const base = assistantApiBaseUrl().replace(/\/+$/, "");
    url = new URL(`${base}${SYNTHESIZE_PATH}`);
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
        "content-type": "application/json",
        accept: "audio/mpeg",
      },
      body: JSON.stringify({ text }),
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok) throw upstreamFailure(response.status);
    const mediaType = (response.headers.get("content-type") ?? "")
      .split(";")[0]
      ?.trim()
      .toLowerCase();
    if (mediaType !== "audio/mpeg") throw upstreamFailure(502);
    return await readBoundedAudio(response, MAX_SPEECH_AUDIO_BYTES);
  } catch (error) {
    if (error instanceof AssistantError) throw error;
    if (requestSignal.aborted) {
      throw new AssistantError(
        "unavailable",
        "Playback was canceled. Read the answer instead.",
      );
    }
    if (controller.signal.aborted) {
      throw new AssistantError(
        "unavailable",
        "The spoken answer took too long. Read the answer instead.",
        { retryable: true },
      );
    }
    throw new AssistantError(
      "unavailable",
      "Spoken answers are not reachable right now. Read the answer instead.",
      { retryable: true },
    );
  } finally {
    clearTimeout(timer);
    requestSignal.removeEventListener("abort", abortOnDisconnect);
  }
}

/**
 * The browser supplies session and turn selectors only. Ownership is proved
 * first, the exact saved turn is located, and the bounded spoken text is
 * derived on the server from its saved presentation.
 */
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
    });
    const { sessionId, turnId } = await context.params;
    const cookie = sessionCookie(request);
    const session = await getAssistantRuntime().readSession({
      cookie,
      session_id: sessionId,
    });
    const turn = session.turns.find((candidate) => candidate.id === turnId);
    if (!turn) {
      throw new AssistantError(
        "not_found",
        "That answer is no longer available. Ask again.",
      );
    }
    const text = spokenTextForTurn(turn);
    if (!text) {
      throw new AssistantError(
        "forbidden",
        "That answer cannot be read aloud. Nothing was changed.",
      );
    }
    const audio = await synthesizeUpstream(cookie, text, request.signal);
    return new Response(audio, {
      status: 200,
      headers: {
        "content-type": "audio/mpeg",
        "content-length": String(audio.byteLength),
      },
    });
  });
}
