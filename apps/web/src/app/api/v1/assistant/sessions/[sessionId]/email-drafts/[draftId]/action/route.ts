import {
  assistantResponse,
  sessionCookie,
} from "../../../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ sessionId: string; draftId: string }> },
): Promise<Response> {
  return assistantResponse(async () => {
    const { sessionId, draftId } = await context.params;
    const sourceTurnId =
      new URL(request.url).searchParams.get("source_turn_id") ?? "";
    const action = await getAssistantRuntime().readEmailAction({
      cookie: sessionCookie(request),
      session_id: sessionId,
      source_turn_id: sourceTurnId,
      draft_id: draftId,
    });
    return Response.json({ action });
  });
}
