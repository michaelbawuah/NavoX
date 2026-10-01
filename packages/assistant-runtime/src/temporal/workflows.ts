import {
  ApplicationFailure,
  proxyActivities,
  sleep,
  workflowInfo,
} from "@temporalio/workflow";
import { GOAL_LIMITS, parseGoalWorkflowInput } from "../goals";
import type { GoalActivities, GoalVerificationResult } from "./activities";

/**
 * The bounded durable verifier for one personal goal.
 *
 * The input is the opaque goal ID alone. The workflow makes no provider call,
 * reads no cookie, approves nothing and executes nothing; it asks the activity
 * to re-check the owning service's own recorded facts, and stops as soon as
 * the goal reaches a terminal outcome or its own lifetime bound runs out.
 */
const { verifyGoal, markGoalDeadline } = proxyActivities<GoalActivities>({
  startToCloseTimeout: "30 seconds",
  retry: {
    initialInterval: "2 seconds",
    backoffCoefficient: 2,
    maximumInterval: "30 seconds",
    maximumAttempts: 3,
  },
});

const NON_TERMINAL = new Set<GoalVerificationResult["outcome"]>([
  "WAITING_FOR_USER",
  "WAITING_FOR_EXTERNAL",
  "RUNNING",
]);

export async function assistantGoalWorkflow(
  raw: unknown,
): Promise<GoalVerificationResult> {
  let goalId: string;
  try {
    goalId = parseGoalWorkflowInput(raw).goal_id;
  } catch {
    // A payload that is not exactly one goal ID can never be made valid by a
    // retry, so the run fails instead of looping.
    throw ApplicationFailure.nonRetryable(
      "An assistant goal workflow payload must name exactly one goal ID.",
      "AssistantGoalPayloadInvalid",
    );
  }

  const startedAt = workflowInfo().unsafe.now();
  for (let attempt = 0; attempt < GOAL_LIMITS.maxVerifyAttempts; attempt += 1) {
    const result = await verifyGoal({ goal_id: goalId });
    if (!NON_TERMINAL.has(result.outcome)) return result;
    // The workflow's own lifetime bound, not only the poll count, ends the run.
    if (
      workflowInfo().unsafe.now() - startedAt >=
      GOAL_LIMITS.workflowLifetimeMs
    )
      break;
    await sleep(GOAL_LIMITS.verifyPollMs);
  }
  return await markGoalDeadline({ goal_id: goalId });
}
