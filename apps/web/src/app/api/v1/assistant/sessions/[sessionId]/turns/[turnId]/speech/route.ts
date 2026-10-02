import {
  AssistantError,
  assertSameOriginMutation,
} from "@navox/assistant-runtime";
import { spokenTextForTurn } from "@navox/assistant-runtime/voice";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../../../../../lib/assistant-route";
import {
  assistantOrigin,
  getAssistantRuntime,
} from "../../../../../../../../../lib/assistant-server";
import {
  audioResponse,
  synthesizeUpstream,
} from "../../../../../../../../../lib/assistant-synthesis";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface SpeechRouteContext {
  params: Promise<{ sessionId: string; turnId: string }>;
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
    return audioResponse(audio);
  });
}
