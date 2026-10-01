import {
  assistantJsonBody,
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const { sessionId } = await context.params;
    const draft = await getAssistantRuntime().createEmailDraft({
      cookie: sessionCookie(request),
      session_id: sessionId,
      body: await assistantJsonBody(request),
    });
    return Response.json({ draft }, { status: 201 });
  });
}
