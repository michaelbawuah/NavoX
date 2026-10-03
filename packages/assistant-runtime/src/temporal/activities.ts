import type { AssistantGoalKind } from "@navox/contracts";
import {
  GOAL_DETAIL,
  type GoalVerificationOutcome,
  verifyActionGoal,
  verifyTurnGoal,
  windowEndedOutcome,
} from "../goals";
import type { AssistantStore } from "../store";
import {
  parseCapabilityDecision,
  parseIntentPlan,
  parsePresentationPlan,
} from "../validate";

/**
 * The only place a goal's status may change.
 *
 * Every activity re-reads the goal row and re-checks that the referenced saved
 * turn or SPEC-001/003 action is owned by the goal's stored user and workspace
 * before it writes. A goal whose source is not owned is left untouched, so a
 * worker can never mutate a row it cannot justify.
 *
 * The worker never approves, executes, retries or cancels a consequential
 * action: it only classifies the state the owning service already recorded.
 */

/** The bounded workflow payload: one opaque goal ID and nothing else. */
export interface GoalVerificationInput {
  goal_id: string;
}

export type GoalOutcomeName =
  | "COMPLETED"
  | "FAILED"
  | "WAITING_FOR_USER"
  | "WAITING_FOR_EXTERNAL"
  | "RUNNING"
  | "MISSING"
  | "SOURCE_NOT_OWNED";

export interface GoalVerificationResult {
  outcome: GoalOutcomeName;
  detail: string | null;
}

function verifyTurn(
  kind: AssistantGoalKind,
  turn: {
    state: string;
    plan: unknown;
    decision: unknown;
    presentation: unknown;
  },
): GoalVerificationOutcome {
  try {
    return verifyTurnGoal({
      kind,
      state: turn.state,
      plan: parseIntentPlan(turn.plan, { allowBoundTurn: true }),
      decision: parseCapabilityDecision(turn.decision),
      presentation: parsePresentationPlan(turn.presentation),
    });
  } catch {
    // A turn row this runtime cannot re-validate is never a verified success.
    return {
      status: "FAILED",
      detail: GOAL_DETAIL.turnUnreadable,
      completed: false,
    };
  }
}

export function createGoalActivities(store: AssistantStore) {
  return {
    /**
     * Reads one goal by opaque ID, re-checks the ownership of its referenced
     * turn or action, classifies the outcome from the owning service's own
     * recorded facts, and persists that outcome.
     */
    async verifyGoal(
      input: GoalVerificationInput,
    ): Promise<GoalVerificationResult> {
      const check = await store.readGoalSourceCheck({ goal_id: input.goal_id });
      if (!check) return { outcome: "MISSING", detail: null };
      if (!check.source_owned)
        return { outcome: "SOURCE_NOT_OWNED", detail: null };
      const goal = check.goal;
      // A completed goal is frozen; a late or replayed run cannot rewrite it.
      if (goal.status === "COMPLETED")
        return { outcome: "COMPLETED", detail: goal.detail };

      const now = new Date();
      let outcome: GoalVerificationOutcome;
      if (goal.kind === "COMMUNICATION_ACTION") {
        if (!check.action) return { outcome: "MISSING", detail: null };
        outcome = verifyActionGoal({
          status: check.action.status,
          executed_at: check.action.executed_at,
          verified_at: check.action.verified_at,
          now,
        });
      } else {
        if (!check.turn) return { outcome: "MISSING", detail: null };
        outcome = verifyTurn(goal.kind, check.turn);
      }

      await store.applyGoalVerification({
        goal_id: goal.id,
        status: outcome.status,
        detail: outcome.detail,
        completed_at: outcome.completed ? now.toISOString() : null,
        now: now.toISOString(),
      });
      return { outcome: outcome.status, detail: outcome.detail };
    },

    /**
     * The workflow's own lifetime bound ran out. The goal keeps its last
     * source-backed state, with a detail that only says the observing window
     * ended; a goal that never reached an observation is recorded as an
     * unsuccessful verification.
     */
    async markGoalDeadline(
      input: GoalVerificationInput,
    ): Promise<GoalVerificationResult> {
      const check = await store.readGoalSourceCheck({ goal_id: input.goal_id });
      if (!check) return { outcome: "MISSING", detail: null };
      if (!check.source_owned)
        return { outcome: "SOURCE_NOT_OWNED", detail: null };
      if (check.goal.status === "COMPLETED")
        return { outcome: "COMPLETED", detail: check.goal.detail };
      const outcome = windowEndedOutcome(check.goal.status);
      await store.applyGoalVerification({
        goal_id: check.goal.id,
        status: outcome.status,
        detail: outcome.detail,
        completed_at: null,
        now: new Date().toISOString(),
      });
      return { outcome: outcome.status, detail: outcome.detail };
    },
  };
}

export type GoalActivities = ReturnType<typeof createGoalActivities>;
