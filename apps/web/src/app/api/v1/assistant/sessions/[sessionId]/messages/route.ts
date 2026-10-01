import type { AssistantMessageResponse } from "@navox/contracts";
import {
  assistantJsonBody,
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface MessageRouteContext {
  params: Promise<{ sessionId: string }>;
}

export async function POST(
  request: Request,
  context: MessageRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const { sessionId } = await context.params;
    const cookie = sessionCookie(request);
    const body = await assistantJsonBody(request);
    const response = await getAssistantRuntime().submitTurn({
      cookie,
      session_id: sessionId,
      body,
    });
    const payload: AssistantMessageResponse = response;
    return Response.json(payload);
  });
}
