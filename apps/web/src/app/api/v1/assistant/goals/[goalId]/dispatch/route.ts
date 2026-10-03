import type { AssistantGoalDispatchResponse } from "@navox/contracts";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface GoalDispatchRouteContext {
  params: Promise<{ goalId: string }>;
}

/**
 * The recoverable dispatch path for one goal after a workflow outage. It is
 * same-origin and bounded: the workflow ID is derived from the goal, so a
 * retry cannot start a second concurrent run. A closed run can restart within
 * the persisted attempt budget; nothing here can approve or execute an action.
 */
export async function POST(
  request: Request,
  context: GoalDispatchRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const { goalId } = await context.params;
    const cookie = sessionCookie(request);
    const result = await getAssistantRuntime().redispatchGoal({
      cookie,
      goal_id: goalId,
    });
    const body: AssistantGoalDispatchResponse = result;
    return Response.json(body);
  });
}
