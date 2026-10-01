import type { AssistantGoalResponse } from "@navox/contracts";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface GoalRouteContext {
  params: Promise<{ goalId: string }>;
}

export async function GET(
  request: Request,
  context: GoalRouteContext,
): Promise<Response> {
  return assistantResponse(async () => {
    const { goalId } = await context.params;
    const cookie = sessionCookie(request);
    const goal = await getAssistantRuntime().readGoal({
      cookie,
      goal_id: goalId,
    });
    const body: AssistantGoalResponse = { goal };
    return Response.json(body);
  });
}
