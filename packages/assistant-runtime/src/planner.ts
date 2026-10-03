import type { IntentPlan, PlannedIntent } from "@navox/contracts";
import { capabilityForIntentKind } from "./capabilities";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";
import {
  INTENT_KINDS,
  isOneOf,
  isRecord,
  isUuid,
  parseIntentPlan,
} from "./validate";

/**
 * Bounded planner for the M1 vertical slice.
 *
 * It does not classify the operator's question and it is not a keyword router:
 * the original text is carried to the owning SPEC-002 route, which owns intent
 * classification and the current projection. `unsupported` comes back from that
 * service and is surfaced honestly instead of being re-routed by string
 * matching here.
 */
export function planTurn(input: { text: string }): IntentPlan {
  return parseIntentPlan({
    version: 1,
    intents: [
      {
        kind: "today.read",
        capability_id: "today.read",
        question: input.text,
        confidence: 1,
      },
    ],
  });
}

/** A session-owned turn this runtime may point a follow-up reference at. */
export interface RecentTurnReference {
  turn_id: string;
  question: string;
}

const UPSTREAM_INTENT_FIELDS = [
  "route",
  "question",
  "entity",
  "time",
  "reference",
  "confidence",
  "requires_clarification",
  "clarification",
] as const;

/**
 * The SPEC-005 response envelope. The plan is nested inside it, and the
 * envelope carries the audit identifiers plus the session binding this runtime
 * must re-check. Anything else is a field this runtime does not own.
 */
const UPSTREAM_ENVELOPE_FIELDS = [
  "task_id",
  "trace_id",
  "plan",
  "session_id",
  "turn_sequence",
  "actions_executed",
] as const;

function upstreamInvalid(message: string): never {
  throw new AssistantError("invalid_request", message);
}

/**
 * Validates one SPEC-005 plan against this runtime's own registry.
 *
 * The upstream service supplies routes and grounded slots only. This function
 * pins the operator's original question, maps the route to a server-owned
 * capability, refuses every field this runtime does not own, and binds a
 * follow-up ordinal to a session-owned turn selector. A plan can name a
 * read-only route; it can never name a tool, provider, workspace or action.
 */
export function parseUpstreamIntentPlan(
  payload: unknown,
  input: {
    utterance: string;
    recentTurns: readonly RecentTurnReference[];
  },
): IntentPlan {
  if (!isRecord(payload))
    upstreamInvalid("The planner returned an unreadable plan.");
  if (payload.version !== 1)
    upstreamInvalid("The planner named an unknown plan version.");
  const rawIntents = payload.intents;
  if (
    !Array.isArray(rawIntents) ||
    rawIntents.length < 1 ||
    rawIntents.length > LIMITS.maxIntents
  ) {
    upstreamInvalid("The planner returned an unusable number of intents.");
  }
  let spanEnd = 0;
  const intents: PlannedIntent[] = rawIntents.map((raw) => {
    if (!isRecord(raw))
      upstreamInvalid("The planner returned an unreadable intent.");
    for (const key of Object.keys(raw)) {
      if (!(UPSTREAM_INTENT_FIELDS as readonly string[]).includes(key)) {
        upstreamInvalid(
          "The planner returned a field this runtime does not own.",
        );
      }
    }
    if (!isOneOf(raw.route, INTENT_KINDS)) {
      upstreamInvalid("The planner named a route this runtime does not own.");
    }
    let question = input.utterance;
    if (raw.question !== undefined && raw.question !== null) {
      if (
        typeof raw.question !== "string" ||
        raw.question.length < 1 ||
        raw.question.length > LIMITS.maxQuestionLength ||
        raw.question !== raw.question.trim()
      )
        upstreamInvalid("The planner returned an invalid question span.");
      question = raw.question;
    } else if (rawIntents.length > 1) {
      upstreamInvalid("A compound plan needs exact question spans.");
    }
    const start = input.utterance.indexOf(question, spanEnd);
    if (start < 0)
      upstreamInvalid("The planner invented or reordered a question span.");
    spanEnd = start + question.length;
    const definition = capabilityForIntentKind(raw.route);
    const [planned] = parseIntentPlan(
      {
        version: 1,
        intents: [
          {
            kind: raw.route,
            capability_id: definition?.id ?? null,
            question,
            confidence: raw.confidence,
            entity: raw.entity,
            time: raw.time,
            reference: raw.reference,
            requires_clarification: raw.requires_clarification,
            clarification: raw.clarification,
          },
        ],
      },
      { allowBoundTurn: false },
    ).intents;
    if (
      rawIntents.length > 1 &&
      planned.entity.value !== null &&
      !question
        .toLocaleLowerCase("en-US")
        .includes(planned.entity.value.toLocaleLowerCase("en-US"))
    )
      upstreamInvalid(
        "The planner invented an entity outside its question span.",
      );
    if (
      rawIntents.length > 1 &&
      planned.time.expression !== null &&
      !question
        .toLocaleLowerCase("en-US")
        .includes(planned.time.expression.toLocaleLowerCase("en-US"))
    )
      upstreamInvalid("The planner invented a time outside its question span.");
    return bindRecentTurnReference(planned, input.recentTurns);
  });
  return parseIntentPlan({ version: 1, intents }, { allowBoundTurn: true });
}

function requireEnvelopeUuid(value: unknown, label: string): string {
  if (typeof value !== "string" || !isUuid(value)) {
    upstreamInvalid(`The planner returned an invalid ${label}.`);
  }
  return value;
}

/**
 * Validates the SPEC-005 envelope and returns the bound plan inside it.
 *
 * The envelope is where the owning service states that nothing ran and which
 * session it answered for. This runtime verifies both before trusting the plan:
 * a claimed action result, a mismatched session, a missing plan and any extra
 * authority field are all refusals, never a route.
 */
export function parseIntentPlanEnvelope(
  payload: unknown,
  input: {
    utterance: string;
    recentTurns: readonly RecentTurnReference[];
    sessionId: string | null;
  },
): IntentPlan {
  if (!isRecord(payload))
    upstreamInvalid("The planner returned an unreadable plan.");
  for (const key of Object.keys(payload)) {
    if (!(UPSTREAM_ENVELOPE_FIELDS as readonly string[]).includes(key)) {
      upstreamInvalid(
        "The planner returned a field this runtime does not own.",
      );
    }
  }
  if (payload.actions_executed !== false) {
    upstreamInvalid("The planner did not state that it ran nothing.");
  }
  requireEnvelopeUuid(payload.task_id, "task ID");
  requireEnvelopeUuid(payload.trace_id, "trace ID");
  const sessionId = payload.session_id ?? null;
  if (sessionId !== null) requireEnvelopeUuid(sessionId, "session ID");
  if (sessionId !== input.sessionId) {
    upstreamInvalid("The planner answered for another session.");
  }
  const turnSequence = payload.turn_sequence ?? null;
  if (
    turnSequence !== null &&
    (!Number.isInteger(turnSequence) || (turnSequence as number) < 0)
  ) {
    upstreamInvalid("The planner returned an invalid turn position.");
  }
  if (!("plan" in payload)) upstreamInvalid("The planner returned no plan.");
  return parseUpstreamIntentPlan(payload.plan, {
    utterance: input.utterance,
    recentTurns: input.recentTurns,
  });
}

/**
 * Binds a validated follow-up ordinal to the session turn it points at. The
 * ordinal indexes the recent turns this runtime supplied, oldest first.
 */
export function bindRecentTurnReference(
  intent: PlannedIntent,
  recentTurns: readonly RecentTurnReference[],
): PlannedIntent {
  if (intent.reference.kind !== "RECENT_TURN") return intent;
  const ordinal = intent.reference.ordinal;
  const turn = ordinal === null ? undefined : recentTurns[ordinal - 1];
  if (!turn) {
    throw new AssistantError(
      "invalid_request",
      "The planner referenced a turn this conversation does not own.",
    );
  }
  return {
    ...intent,
    reference: { ...intent.reference, turn_id: turn.turn_id },
  };
}
