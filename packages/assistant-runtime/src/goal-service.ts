import { randomUUID } from "node:crypto";
import type { AssistantGoalKind, AssistantGoalView } from "@navox/contracts";
import { AssistantError } from "./errors";
import {
  GOAL_DETAIL,
  GOAL_LIMITS,
  type GoalDetail,
  goalKindForTurn,
  goalViewOf,
} from "./goals";
import type {
  AssistantGoalRecord,
  AssistantScope,
  AssistantStore,
  AssistantTurnRecord,
} from "./store";
import { toTurnView } from "./store";

/**
 * The durable-workflow seam for one bounded goal.
 *
 * The implementation reports whether a run is live and starts a bounded
 * workflow run whose ID is derived from the opaque goal UUID. It receives the goal
 * ID and nothing else, so no cookie, question, answer, recipient or approval
 * secret can reach the workflow payload.
 */
export interface AssistantGoalDispatcher {
  /**
   * Whether a durable run for this goal is live right now. This is a read-only
   * probe: it starts nothing and consumes no dispatch attempt.
   */
  isActive(input: { goal_id: string }): Promise<boolean>;
  /**
   * Starts a run. A live run is never replaced, because the workflow ID is
   * derived from the goal, so a duplicate request cannot start a second
   * concurrent run.
   */
  dispatch(input: { goal_id: string }): Promise<void>;
}

export interface AssistantGoalServiceDeps {
  store: AssistantStore;
  /**
   * Absent in a deployment without a configured Temporal target. A missing
   * dispatcher is recorded as an explicit failed dispatch, never a completion.
   */
  dispatcher?: AssistantGoalDispatcher | null;
  now?: () => Date;
  newId?: () => string;
}

export interface AssistantGoalService {
  /**
   * Records the bounded goal a successfully saved turn owns, then attempts a
   * bounded dispatch. A replay returns the existing row instead of a duplicate.
   */
  recordTurnGoal(input: {
    scope: AssistantScope;
    session_id: string;
    turn: AssistantTurnRecord;
  }): Promise<AssistantGoalRecord | null>;
  /** Records the goal for one prepared SPEC-001/003 action. */
  recordActionGoal(input: {
    scope: AssistantScope;
    session_id: string;
    action_id: string;
  }): Promise<AssistantGoalRecord>;
  /** Reads one goal, fenced by the owning session's workspace and user. */
  readGoal(input: {
    goal_id: string;
    scope: AssistantScope;
  }): Promise<AssistantGoalRecord>;
  /** Lists a bounded number of goals for one owned session. */
  listGoals(input: {
    session_id: string;
    scope: AssistantScope;
  }): Promise<AssistantGoalRecord[]>;
  /**
   * The recoverable dispatch path. Re-attempting a start is idempotent: the
   * workflow ID is derived from the goal, so a duplicate never becomes a
   * second concurrent run, and the bounded attempt budget still applies.
   */
  redispatch(input: {
    goal_id: string;
    scope: AssistantScope;
  }): Promise<{ goal: AssistantGoalRecord; dispatched: boolean }>;
}

export function goalView(goal: AssistantGoalRecord): AssistantGoalView {
  return goalViewOf(goal);
}

export function createAssistantGoalService(
  deps: AssistantGoalServiceDeps,
): AssistantGoalService {
  const now = deps.now ?? (() => new Date());
  const newId = deps.newId ?? (() => randomUUID());
  const dispatcher = deps.dispatcher ?? null;

  /**
   * Attempts one bounded dispatch.
   *
   * The budget covers every start attempt, including explicit retries, and is
   * claimed atomically, so concurrent requests can never exceed it. A goal
   * whose run is already live is left untouched and consumes nothing: the
   * request is idempotent rather than a new attempt.
   */
  async function attemptDispatch(
    goal: AssistantGoalRecord,
    scope: AssistantScope,
    options: { force: boolean },
  ): Promise<{ goal: AssistantGoalRecord; runPresent: boolean }> {
    if (goal.status === "COMPLETED") return { goal, runPresent: false };
    if (goal.dispatch_state === "DISPATCHED" && !options.force)
      return { goal, runPresent: false };

    const writeFailure = async (detail: GoalDetail) => {
      const failed = await deps.store.markGoalDispatchFailed({
        goal_id: goal.id,
        scope,
        now: now().toISOString(),
        detail,
      });
      return { goal: failed ?? goal, runPresent: false };
    };

    if (!dispatcher) return writeFailure(GOAL_DETAIL.dispatchUnconfigured);

    let active: boolean;
    try {
      active = await dispatcher.isActive({ goal_id: goal.id });
    } catch {
      // A read-only probe failure invalidates nothing that was already
      // recorded; a goal that was never dispatched is a bounded failure.
      return goal.dispatch_state === "DISPATCHED"
        ? { goal, runPresent: false }
        : writeFailure(GOAL_DETAIL.dispatchFailed);
    }
    if (active) return { goal, runPresent: true };

    // Claim one attempt from the bounded total budget before spending any
    // start. The condition and the increment are one atomic statement.
    const claimed = await deps.store.claimGoalDispatchAttempt({
      goal_id: goal.id,
      scope,
      now: now().toISOString(),
      max_attempts: GOAL_LIMITS.maxDispatchAttempts,
    });
    if (!claimed) return { goal, runPresent: false };

    try {
      await dispatcher.dispatch({ goal_id: goal.id });
    } catch {
      // A workflow outage is a truthful pending/failed row, never a completion.
      return writeFailure(GOAL_DETAIL.dispatchFailed);
    }
    const dispatched = await deps.store.markGoalDispatched({
      goal_id: goal.id,
      scope,
      now: now().toISOString(),
    });
    return { goal: dispatched ?? claimed, runPresent: true };
  }

  async function recordGoal(input: {
    scope: AssistantScope;
    session_id: string;
    kind: AssistantGoalKind;
    source_turn_id: string | null;
    action_id: string | null;
  }): Promise<AssistantGoalRecord> {
    const at = now().toISOString();
    const { goal } = await deps.store.createGoal({
      id: newId(),
      session_id: input.session_id,
      scope: input.scope,
      kind: input.kind,
      source_turn_id: input.source_turn_id,
      action_id: input.action_id,
      created_at: at,
    });
    return (await attemptDispatch(goal, input.scope, { force: false })).goal;
  }

  async function readGoal(input: {
    goal_id: string;
    scope: AssistantScope;
  }): Promise<AssistantGoalRecord> {
    const goal = await deps.store.readGoal({
      goal_id: input.goal_id,
      scope: input.scope,
    });
    if (!goal)
      throw new AssistantError(
        "not_found",
        "That assistant goal is no longer available.",
      );
    return goal;
  }

  return {
    async recordTurnGoal(input) {
      const kind = goalKindForTurn(toTurnView(input.turn));
      if (!kind) return null;
      // The saved turn must belong to the session this goal names; the store
      // re-checks the same rule inside its insert statement.
      if (input.turn.session_id !== input.session_id)
        throw new AssistantError(
          "forbidden",
          "That goal source is not available for this account.",
        );
      return recordGoal({
        scope: input.scope,
        session_id: input.turn.session_id,
        kind,
        source_turn_id: input.turn.id,
        action_id: null,
      });
    },

    async recordActionGoal(input) {
      return recordGoal({
        scope: input.scope,
        session_id: input.session_id,
        kind: "COMMUNICATION_ACTION",
        source_turn_id: null,
        action_id: input.action_id,
      });
    },

    readGoal,

    async listGoals(input) {
      return deps.store.listGoals({
        session_id: input.session_id,
        scope: input.scope,
      });
    },

    async redispatch(input) {
      const goal = await readGoal(input);
      const updated = await attemptDispatch(goal, input.scope, {
        force: true,
      });
      return {
        goal: updated.goal,
        dispatched: updated.runPresent,
      };
    },
  };
}
