import type { AssistantGoalKind } from "@navox/contracts";
import { AssistantError } from "./errors";
import {
  GOAL_DETAIL,
  type GoalVerificationOutcome,
  groundedTodayTurn,
} from "./goals";
import {
  isRecord,
  parseCapabilityDecision,
  parseIntentPlan,
  parsePresentationPlan,
} from "./validate";

/** An owned source attestation, with no conversation or retrieved content. */
export interface TurnGoalProjection {
  contract_version: 1;
  classification: "BRIEFING" | "MEETING_PREP" | "NOT_GROUNDED" | "UNREADABLE";
}

/** Missing evidence is unavailable, rather than evidence of a failed source. */
export type ProjectedTurnGoalVerification =
  | { available: false; outcome: null }
  | { available: true; outcome: GoalVerificationOutcome };

/** Capture the same parser/grounding decision as the existing goal verifier. */
export function deriveTurnGoalProjection(input: {
  state: string;
  plan: unknown;
  decision: unknown;
  presentation: unknown;
}): TurnGoalProjection {
  try {
    const kind = groundedTodayTurn({
      state: input.state,
      plan: parseIntentPlan(input.plan, { allowBoundTurn: true }),
      decision: parseCapabilityDecision(input.decision),
      presentation: parsePresentationPlan(input.presentation),
    });
    return {
      contract_version: 1,
      classification:
        kind === "BRIEFING" || kind === "MEETING_PREP" ? kind : "NOT_GROUNDED",
    };
  } catch {
    return { contract_version: 1, classification: "UNREADABLE" };
  }
}

/** Stored projections accept only the exact two-field, versioned contract. */
export function parseTurnGoalProjection(value: unknown): TurnGoalProjection {
  try {
    if (!isRecord(value)) throw new Error();
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) throw new Error();
    const keys = Reflect.ownKeys(value);
    if (
      keys.length !== 2 ||
      !keys.includes("contract_version") ||
      !keys.includes("classification")
    )
      throw new Error();
    const fields = Object.getOwnPropertyDescriptors(value);
    const version = fields.contract_version;
    const classification = fields.classification;
    // JSON data cannot contain getters; do not execute a supplied accessor.
    if (
      !version ||
      !("value" in version) ||
      version.value !== 1 ||
      !classification ||
      !("value" in classification)
    )
      throw new Error();
    const kind: unknown = classification.value;
    if (
      kind !== "BRIEFING" &&
      kind !== "MEETING_PREP" &&
      kind !== "NOT_GROUNDED" &&
      kind !== "UNREADABLE"
    )
      throw new Error();
    return { contract_version: 1, classification: kind };
  } catch {
    throw new AssistantError(
      "unavailable",
      "Stored goal verification is unavailable.",
    );
  }
}

/** Ownership must be checked by the caller before using this classification. */
export function verifyProjectedTurnGoal(
  kind: AssistantGoalKind,
  projection: unknown | null,
): ProjectedTurnGoalVerification {
  let parsed: TurnGoalProjection;
  try {
    parsed = parseTurnGoalProjection(projection);
  } catch {
    return { available: false, outcome: null };
  }
  if (parsed.classification === "UNREADABLE")
    return {
      available: true,
      outcome: {
        status: "FAILED",
        detail: GOAL_DETAIL.turnUnreadable,
        completed: false,
      },
    };
  if (
    (kind === "BRIEFING" || kind === "MEETING_PREP") &&
    parsed.classification === kind
  )
    return {
      available: true,
      outcome: {
        status: "COMPLETED",
        detail: GOAL_DETAIL.verified,
        completed: true,
      },
    };
  return {
    available: true,
    outcome: {
      status: "FAILED",
      detail: GOAL_DETAIL.turnNotReady,
      completed: false,
    },
  };
}
