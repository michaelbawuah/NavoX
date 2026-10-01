import type {
  AssistantSessionResponse,
  DeleteAssistantSessionResponse,
} from "@navox/contracts";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface SessionRouteContext {
  params: Promise<{ sessionId: string }>;
}

export async function GET(
  request: Request,
  context: SessionRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    const { sessionId } = await context.params;
    const cookie = sessionCookie(request);
    const session = await getAssistantRuntime().readSession({
      cookie,
      session_id: sessionId,
    });
    const body: AssistantSessionResponse = { session };
    return Response.json(body);
  });
}

/** Explicit history deletion. The SPEC-005 session row is not ours to remove. */
export async function DELETE(
  request: Request,
  context: SessionRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const { sessionId } = await context.params;
    const cookie = sessionCookie(request);
    await getAssistantRuntime().deleteSession({
      cookie,
      session_id: sessionId,
    });
    const body: DeleteAssistantSessionResponse = {
      session_id: sessionId,
      deleted: true,
    };
    return Response.json(body);
  });
}
