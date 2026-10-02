import { AssistantError, isRecord } from "@navox/assistant-runtime";
import {
  assistantJsonBody,
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../../../lib/assistant-server";
import {
  audioResponse,
  synthesizeUpstream,
} from "../../../../../../../../../lib/assistant-synthesis";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface SpeechRouteContext {
  params: Promise<{ sessionId: string; draftId: string }>;
}

/**
 * The browser supplies the source turn, draft and the exact version it
 * reviewed, never the spoken words. The runtime derives the bounded text from
 * the authenticated, source-turn bound draft and refuses a stale version or an
 * over-long draft instead of truncating the exact send content.
 */
export async function POST(
  request: Request,
  context: SpeechRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const { sessionId, draftId } = await context.params;
    const body = await assistantJsonBody(request);
    const sourceTurnId = isRecord(body) ? body.source_turn_id : null;
    const version = isRecord(body) ? body.version : null;
    if (typeof sourceTurnId !== "string" || !Number.isInteger(version)) {
      throw new AssistantError(
        "invalid_request",
        "A source turn and draft version are required.",
      );
    }
    const cookie = sessionCookie(request);
    const speech = await getAssistantRuntime().readEmailDraftSpeech({
      cookie,
      session_id: sessionId,
      source_turn_id: sourceTurnId,
      draft_id: draftId,
      version: version as number,
    });
    if (!speech.speakable) {
      throw new AssistantError(
        "unsupported",
        speech.reason === "too_long"
          ? "This draft is longer than the spoken limit. Read the full text instead; nothing was sent."
          : "That draft has nothing to read aloud.",
      );
    }
    const audio = await synthesizeUpstream(cookie, speech.text, request.signal);
    return audioResponse(audio);
  });
}
