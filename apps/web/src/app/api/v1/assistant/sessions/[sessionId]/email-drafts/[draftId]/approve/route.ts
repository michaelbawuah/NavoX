import {
  assistantJsonBody,
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ sessionId: string; draftId: string }> },
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const { sessionId, draftId } = await context.params;
    const action = await getAssistantRuntime().approveEmailDraft({
      cookie: sessionCookie(request),
      session_id: sessionId,
      draft_id: draftId,
      body: await assistantJsonBody(request),
    });
    return Response.json({ action });
  });
}
