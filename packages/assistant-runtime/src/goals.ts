import type {
  AssistantGoalDispatchState,
  AssistantGoalKind,
  AssistantGoalStatus,
  AssistantGoalView,
  AssistantPresentationPlan,
  CapabilityDecision,
  IntentPlan,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { isRecord, isUuid } from "./validate";

/**
 * SPEC-008 M14B bounded personal goals.
 *
 * A goal is a durable, opaque identifier for one bounded verification job. The
 * runtime only ever persists identifiers, a status from a closed vocabulary and
 * a detail string from the frozen table below, so no question text, answer
 * text, email body, source content, cookie, provider token or approval secret
 * can reach the goal row or a Temporal workflow payload. The Temporal payload
 * is the goal ID alone.
 *
 * Status authority stays with the owning service:
 *
 *   * a briefing or meeting-prep goal verifies that an already saved turn is a
 *     grounded successful result of the owning SPEC-002 service. It never
 *     re-runs retrieval or generation.
 *   * a consequential-action goal observes the existing SPEC-001/003 ledger.
 *     Only an action whose status is `completed` *and* whose `verified_at`
 *     exists may become `COMPLETED`; pending approval stays
 *     `WAITING_FOR_USER`, and an executed but unverified action stays
 *     `WAITING_FOR_EXTERNAL`.
 *
 * Nothing in this module executes, approves or retries a consequential action.
 */

export const GOAL_KINDS: readonly AssistantGoalKind[] = [
  "BRIEFING",
  "MEETING_PREP",
  "COMMUNICATION_ACTION",
];

export const GOAL_STATUSES: readonly AssistantGoalStatus[] = [
  "PENDING",
  "RUNNING",
  "WAITING_FOR_USER",
  "WAITING_FOR_EXTERNAL",
  "COMPLETED",
  "FAILED",
];

export const GOAL_DISPATCH_STATES: readonly AssistantGoalDispatchState[] = [
  "NOT_DISPATCHED",
  "DISPATCHED",
  "DISPATCH_FAILED",
];

/** Goal bounds shared by the runtime, the SQL migration and the workflow. */
export const GOAL_LIMITS = {
  /** Dispatch attempts the runtime may make for one goal before it gives up. */
  maxDispatchAttempts: 3,
  /** Verification polls one workflow run may perform before it stops. */
  maxVerifyAttempts: 12,
  /** Delay between two verification polls. */
  verifyPollMs: 5 * 60_000,
  /** Whole-workflow lifetime bound; the run never outlives this window. */
  workflowLifetimeMs: 60 * 60_000,
  /** Goals returned by one bounded session listing. */
  maxGoalsPerSession: 50,
  /** The longest stored operator-facing detail string. */
  maxDetailLength: 500,
} as const;

/**
 * A frozen detail vocabulary. Every value is a fixed internal label about the
 * goal's own state; no service text, payload or identifier is interpolated.
 */
const DETAILS = {
  pendingDispatch: "goal.created",
  dispatchUnconfigured: "goal.dispatch_unconfigured",
  dispatchFailed: "goal.dispatch_failed",
  dispatched: "goal.dispatched",
  verified: "goal.verified",
  turnNotReady: "goal.turn_not_grounded",
  turnUnreadable: "goal.turn_unreadable",
  waitingForApproval: "goal.action_waiting_for_approval",
  running: "goal.action_in_flight",
  executedUnverified: "goal.action_executed_unverified",
  actionFailed: "goal.action_failed",
  actionUncertain: "goal.action_uncertain",
  ledgerInconsistent: "goal.action_ledger_inconsistent",
  unknownStatus: "goal.action_status_unrecognized",
  verificationDeadline: "goal.verification_deadline_reached",
  /**
   * The workflow's own window ended while the owning service still recorded a
   * live state. These carry no new authority: they never change the ledger
   * status, they only say the window that observed it has ended.
   */
  windowEndedWaitingForUser: "goal.window_ended_waiting_for_user",
  windowEndedWaitingForExternal: "goal.window_ended_waiting_for_external",
  windowEndedInFlight: "goal.window_ended_in_flight",
} as const;

export const GOAL_DETAIL = DETAILS;

export type GoalDetail = (typeof DETAILS)[keyof typeof DETAILS];

const ALL_DETAILS: readonly string[] = Object.values(DETAILS);

/** The deterministic Temporal workflow ID for one goal. */
export function goalWorkflowId(goalId: string): string {
  return `navox-assistant-goals:${goalId}`;
}

/**
 * The registered Temporal workflow type. It must stay equal to the exported
 * workflow function's name in `temporal/workflows.ts`; a regression asserts it.
 */
export const ASSISTANT_GOAL_WORKFLOW_TYPE = "assistantGoalWorkflow";

export function parseGoalKind(value: unknown): AssistantGoalKind {
  if (
    typeof value !== "string" ||
    !GOAL_KINDS.includes(value as AssistantGoalKind)
  )
    throw new AssistantError(
      "unavailable",
      "A saved goal could not be verified.",
    );
  return value as AssistantGoalKind;
}

export function parseGoalStatus(value: unknown): AssistantGoalStatus {
  if (
    typeof value !== "string" ||
    !GOAL_STATUSES.includes(value as AssistantGoalStatus)
  )
    throw new AssistantError(
      "unavailable",
      "A saved goal could not be verified.",
    );
  return value as AssistantGoalStatus;
}

export function parseGoalDispatchState(
  value: unknown,
): AssistantGoalDispatchState {
  if (
    typeof value !== "string" ||
    !GOAL_DISPATCH_STATES.includes(value as AssistantGoalDispatchState)
  )
    throw new AssistantError(
      "unavailable",
      "A saved goal could not be verified.",
    );
  return value as AssistantGoalDispatchState;
}

/** A stored detail must come from the frozen table; anything else is refused. */
export function parseGoalDetail(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || !ALL_DETAILS.includes(value))
    throw new AssistantError(
      "unavailable",
      "A saved goal could not be verified.",
    );
  return value;
}

export interface GoalProjectionInput {
  id: string;
  kind: AssistantGoalKind;
  status: AssistantGoalStatus;
  dispatch_state: AssistantGoalDispatchState;
  detail: string | null;
  action_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

/** Projects one stored goal into the client-facing view. */
export function goalViewOf(goal: GoalProjectionInput): AssistantGoalView {
  if (!isUuid(goal.id)) {
    throw new AssistantError(
      "unavailable",
      "A saved goal could not be verified.",
    );
  }
  return {
    id: goal.id,
    kind: parseGoalKind(goal.kind),
    status: parseGoalStatus(goal.status),
    dispatch_state: parseGoalDispatchState(goal.dispatch_state),
    detail: parseGoalDetail(goal.detail),
    action_id: goal.action_id,
    created_at: goal.created_at,
    updated_at: goal.updated_at,
    completed_at: goal.completed_at,
  };
}

/**
 * The provenance a saved turn must still carry for a briefing or meeting goal
 * to be verifiable. A goal never re-runs retrieval; it re-checks the owning
 * service's own recorded decision, plan and blocks.
 */
export interface GoalTurnProvenance {
  state: string;
  plan: IntentPlan | null;
  decision: CapabilityDecision | null;
  presentation: AssistantPresentationPlan | null;
}

/** The reason the owning SPEC-002 Today service records for its own answer. */
const TODAY_REASON = /^today\.[a-z_]+$/;

/**
 * The established grounded `today.read` contract: the turn is `READY`, its
 * recorded decision is a delegated `today.query` read with exactly the
 * runtime's own authority fields, its plan is one bound `today.read` intent,
 * and its blocks still carry the owning service's own result. A meeting goal
 * additionally requires the synthesised briefing to belong to an item this
 * same turn carried.
 *
 * This is the same rule at creation and at verification, so a turn that cannot
 * prove its provenance never becomes a verified goal.
 */
export function groundedTodayTurn(
  turn: GoalTurnProvenance,
): AssistantGoalKind | null {
  if (turn.state !== "READY") return null;
  const decision = turn.decision;
  if (decision?.kind !== "DELEGATE") return null;
  if (decision.capability_id !== "today.read") return null;
  if (decision.target !== "today.query") return null;
  if (decision.response_state !== "READY") return null;
  if (decision.requires_approval !== false) return null;
  if (decision.action_state !== "NONE") return null;
  if (decision.action_id !== null) return null;
  if (
    typeof decision.reason !== "string" ||
    !TODAY_REASON.test(decision.reason)
  )
    return null;

  const plan = turn.plan;
  if (plan?.version !== 1 || plan.intents.length !== 1) return null;
  const intent = plan.intents[0];
  if (!intent) return null;
  if (intent.kind !== "today.read" || intent.capability_id !== "today.read")
    return null;
  if (intent.requires_clarification) return null;

  const blocks = turn.presentation?.blocks ?? [];
  const hasResult = blocks.some(
    (block) => block.kind === "ANSWER" || block.kind === "ITEM",
  );
  if (!hasResult) return null;

  const briefing = blocks.find((block) => block.kind === "MEETING_BRIEFING");
  if (!briefing) return "BRIEFING";
  // The briefing must describe an item this same turn carried; the owning
  // service only ever synthesises it from the matching Today item.
  const itemIds = new Set(
    blocks.flatMap((block) => (block.kind === "ITEM" ? [block.item.id] : [])),
  );
  if (!itemIds.has(briefing.meeting.commitment_id)) return null;
  return "MEETING_PREP";
}

/** The goal a successfully saved turn owns, or `null` when it is not grounded. */
export function goalKindForTurn(
  view: GoalTurnProvenance,
): AssistantGoalKind | null {
  return groundedTodayTurn(view);
}

export interface GoalVerificationOutcome {
  /** Never `PENDING`: a workflow only writes an outcome it can justify. */
  status: TerminalGoalStatus;
  detail: GoalDetail;
  /** The exact instant to persist when, and only when, the goal completed. */
  completed: boolean;
}

/** Every goal status except the resting `PENDING` state. */
export type TerminalGoalStatus = Exclude<AssistantGoalStatus, "PENDING">;

/**
 * Verifies a saved briefing or meeting-prep turn against the owning service's
 * own recorded outcome. The turn must still meet the grounded `today.read`
 * contract *and* still classify as the goal's own kind; anything else fails
 * closed instead of being described as a verified result.
 */
export function verifyTurnGoal(
  input: GoalTurnProvenance & { kind: AssistantGoalKind },
): GoalVerificationOutcome {
  if (groundedTodayTurn(input) === input.kind)
    return { status: "COMPLETED", detail: DETAILS.verified, completed: true };
  return { status: "FAILED", detail: DETAILS.turnNotReady, completed: false };
}

/** Statuses that have not reached a terminal outcome in the action ledger. */
const AWAITING_APPROVAL_STATUSES = new Set([
  "pending",
  "candidate",
  "queued",
  "awaiting_approval",
]);
const IN_FLIGHT_STATUSES = new Set(["approved", "executing", "running"]);
const FAILED_STATUSES = new Set([
  "failed",
  "rejected",
  "blocked",
  "cancelled",
  "canceled",
  "expired",
]);
const UNCERTAIN_STATUSES = new Set(["uncertain"]);
/** Forward-dated timestamps beyond this skew are treated as unreadable. */
const GOAL_CLOCK_SKEW_MS = 60_000;

function isoInstant(
  value: string | null | undefined,
  now: Date,
): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) || !Number.isFinite(parsed))
    return null;
  if (parsed > now.getTime() + GOAL_CLOCK_SKEW_MS) return null;
  return value;
}

/**
 * Verifies one consequential-action goal against the existing SPEC-001/003
 * ledger row. The worker never approves, executes or retries the action; it
 * only classifies the state the owning service recorded.
 */
export function verifyActionGoal(input: {
  status: string;
  executed_at: string | null;
  verified_at: string | null;
  now: Date;
}): GoalVerificationOutcome {
  const status =
    typeof input.status === "string" ? input.status.trim().toLowerCase() : "";
  if (!status)
    return {
      status: "FAILED",
      detail: DETAILS.ledgerInconsistent,
      completed: false,
    };
  const executedAt = isoInstant(input.executed_at, input.now);
  const verifiedAt = isoInstant(input.verified_at, input.now);
  // A malformed or forward-dated instant is not proof of anything.
  if (
    (input.executed_at !== null && executedAt === null) ||
    (input.verified_at !== null && verifiedAt === null)
  )
    return {
      status: "FAILED",
      detail: DETAILS.ledgerInconsistent,
      completed: false,
    };
  // The existing action contracts write the execution before the independent
  // verification. A row that contradicts that ordering proves nothing.
  if (verifiedAt !== null) {
    if (status !== "completed" || executedAt === null)
      return {
        status: "FAILED",
        detail: DETAILS.ledgerInconsistent,
        completed: false,
      };
    if (Date.parse(executedAt) > Date.parse(verifiedAt))
      return {
        status: "FAILED",
        detail: DETAILS.ledgerInconsistent,
        completed: false,
      };
  }
  if (status === "completed") {
    if (executedAt === null)
      return {
        status: "FAILED",
        detail: DETAILS.ledgerInconsistent,
        completed: false,
      };
    if (verifiedAt !== null)
      return {
        status: "COMPLETED",
        detail: DETAILS.verified,
        completed: true,
      };
    return {
      status: "WAITING_FOR_EXTERNAL",
      detail: DETAILS.executedUnverified,
      completed: false,
    };
  }
  if (AWAITING_APPROVAL_STATUSES.has(status))
    return {
      status: "WAITING_FOR_USER",
      detail: DETAILS.waitingForApproval,
      completed: false,
    };
  if (IN_FLIGHT_STATUSES.has(status))
    return { status: "RUNNING", detail: DETAILS.running, completed: false };
  if (UNCERTAIN_STATUSES.has(status))
    return {
      // The owning service recorded an uncertain outcome, not a failure. Only
      // a later verified completion may move this goal to COMPLETED.
      status: "WAITING_FOR_EXTERNAL",
      detail: DETAILS.actionUncertain,
      completed: false,
    };
  if (FAILED_STATUSES.has(status))
    return {
      status: "FAILED",
      detail: DETAILS.actionFailed,
      completed: false,
    };
  return {
    status: "FAILED",
    detail: DETAILS.unknownStatus,
    completed: false,
  };
}

/**
 * The outcome a workflow writes when its own window ends. It preserves the
 * last source-backed state instead of turning elapsed time into a failure: a
 * pending approval stays `WAITING_FOR_USER`, executed-but-unverified work stays
 * `WAITING_FOR_EXTERNAL`, and an in-flight action stays `RUNNING`. The detail
 * only says the observing window ended; it is not a ledger status.
 *
 * A goal that never reached any source-backed observation has nothing to
 * preserve, so it is recorded as a failed verification.
 */
export function windowEndedOutcome(
  // A completed goal is never passed here: the caller returns its frozen
  // `COMPLETED` state unchanged, and the type makes that explicit.
  lastStatus: Exclude<AssistantGoalStatus, "COMPLETED">,
): GoalVerificationOutcome {
  if (lastStatus === "WAITING_FOR_USER")
    return {
      status: "WAITING_FOR_USER",
      detail: DETAILS.windowEndedWaitingForUser,
      completed: false,
    };
  if (lastStatus === "WAITING_FOR_EXTERNAL")
    return {
      status: "WAITING_FOR_EXTERNAL",
      detail: DETAILS.windowEndedWaitingForExternal,
      completed: false,
    };
  if (lastStatus === "RUNNING")
    return {
      status: "RUNNING",
      detail: DETAILS.windowEndedInFlight,
      completed: false,
    };
  return {
    status: "FAILED",
    detail: DETAILS.verificationDeadline,
    completed: false,
  };
}

/** A stored workflow payload is the opaque goal ID and nothing else. */
export function parseGoalWorkflowInput(value: unknown): { goal_id: string } {
  if (
    !isRecord(value) ||
    !isUuid(value.goal_id) ||
    Object.keys(value).length !== 1
  )
    throw new AssistantError(
      "invalid_request",
      "A goal workflow payload must name exactly one goal ID.",
    );
  return { goal_id: value.goal_id };
}
