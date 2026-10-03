import type { AssistantGoalListResponse } from "@navox/contracts";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface GoalsRouteContext {
  params: Promise<{ sessionId: string }>;
}

/**
 * The truthful state of this session's bounded goals. It reads only rows the
 * owning session already wrote; a foreign session is a not-found.
 */
export async function GET(
  request: Request,
  context: GoalsRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    const { sessionId } = await context.params;
    const cookie = sessionCookie(request);
    const goals = await getAssistantRuntime().listSessionGoals({
      cookie,
      session_id: sessionId,
    });
    const body: AssistantGoalListResponse = { goals };
    return Response.json(body);
  });
}
