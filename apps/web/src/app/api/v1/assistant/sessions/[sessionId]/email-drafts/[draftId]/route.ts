import {
  assistantJsonBody,
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ sessionId: string; draftId: string }> };

export async function GET(
  request: Request,
  context: Context,
): Promise<Response> {
  return assistantResponse(async () => {
    const { sessionId, draftId } = await context.params;
    const sourceTurnId =
      new URL(request.url).searchParams.get("source_turn_id") ?? "";
    const draft = await getAssistantRuntime().readEmailDraft({
      cookie: sessionCookie(request),
      session_id: sessionId,
      source_turn_id: sourceTurnId,
      draft_id: draftId,
    });
    return Response.json({ draft });
  });
}

export async function PATCH(
  request: Request,
  context: Context,
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const { sessionId, draftId } = await context.params;
    const draft = await getAssistantRuntime().reviseEmailDraft({
      cookie: sessionCookie(request),
      session_id: sessionId,
      draft_id: draftId,
      body: await assistantJsonBody(request),
    });
    return Response.json({ draft });
  });
}
