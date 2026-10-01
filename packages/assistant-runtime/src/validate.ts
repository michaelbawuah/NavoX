import type {
  AssistantActionState,
  AssistantBlock,
  AssistantCapabilityId,
  AssistantCitation,
  AssistantIntentEntityKind,
  AssistantIntentEntitySlot,
  AssistantIntentKind,
  AssistantIntentReferenceKind,
  AssistantIntentReferenceSlot,
  AssistantIntentTimeKind,
  AssistantIntentTimeSlot,
  AssistantItemBlock,
  AssistantMeetingBriefing,
  AssistantMessageRequest,
  AssistantModality,
  AssistantPresentation,
  AssistantPresentationPlan,
  AssistantResponseState,
  AssistantVoiceState,
  CapabilityDecision,
  IntentPlan,
  PlannedIntent,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";

/**
 * Known values. `satisfies` keeps these arrays in lockstep with the shared
 * contracts, so adding a contract value without a validator fails typecheck.
 */
export const CAPABILITY_IDS = [
  "today.read",
  "email.search",
  "subscription.search",
  "news.read",
  "weather.read",
  "class.next",
  "time.now",
] as const satisfies readonly AssistantCapabilityId[];
export const INTENT_KINDS = [
  "today.read",
  "email.search",
  "subscription.search",
  "news.read",
  "weather.read",
  "class.next",
  "time.now",
  "assistant.clarify",
] as const satisfies readonly AssistantIntentKind[];
export const INTENT_ENTITY_KINDS = [
  "NONE",
  "PERSON",
  "ORGANIZATION",
  "PROJECT",
  "TOPIC",
] as const satisfies readonly AssistantIntentEntityKind[];
export const INTENT_TIME_KINDS = [
  "NONE",
  "RELATIVE",
  "ABSOLUTE",
  "RANGE",
] as const satisfies readonly AssistantIntentTimeKind[];
export const INTENT_REFERENCE_KINDS = [
  "NONE",
  "RECENT_TURN",
] as const satisfies readonly AssistantIntentReferenceKind[];
export const MODALITIES = [
  "TEXT",
  "VOICE",
] as const satisfies readonly AssistantModality[];
export const PRESENTATIONS = [
  "TEXT",
  "VOICE",
  "BOTH",
] as const satisfies readonly AssistantPresentation[];
export const RESPONSE_STATES = [
  "READY",
  "CLARIFY",
  "UNAVAILABLE",
  "WITHHELD",
] as const satisfies readonly AssistantResponseState[];
export const ACTION_STATES = [
  "NONE",
  "PROPOSED",
  "PENDING_APPROVAL",
  "APPROVED",
  "EXECUTING",
  "EXECUTED",
  "FAILED",
  "DECLINED",
  "EXPIRED",
] as const satisfies readonly AssistantActionState[];
export const VOICE_STATES = [
  "IDLE",
  "UNSUPPORTED",
  "LISTENING",
  "TRANSCRIBING",
  "THINKING",
  "MUTED",
  "SPEAKING",
  "STOPPED",
] as const satisfies readonly AssistantVoiceState[];

/**
 * The SPEC-002 `QueryIntent` union. Pinning it here means an unknown value from
 * the owning service is a qualified failure instead of an assumed success.
 */
export const TODAY_INTENTS = [
  "today",
  "attention",
  "this_week",
  "waiting",
  "renewals",
  "promises",
  "forgetting",
  "meeting_prep",
  "handleable",
  "unsupported",
] as const;

export type TodayIntent = (typeof TODAY_INTENTS)[number];

export function isTodayIntent(value: unknown): value is TodayIntent {
  return isOneOf(value, TODAY_INTENTS);
}

export const DECISION_KINDS = [
  "DELEGATE",
  "CLARIFY",
  "UNAVAILABLE",
  "WITHHELD",
  "REFUSED",
] as const;

export const BLOCK_KINDS = [
  "ANSWER",
  "ITEM",
  "DETAILS",
  "CITATIONS",
  "NOTICE",
  "SUGGESTIONS",
  "MEETING_BRIEFING",
  "CLASS_NAVIGATION",
] as const;

/** Field names a client may ever send. Anything else is refused, not ignored. */
export const MESSAGE_REQUEST_FIELDS = [
  "request_id",
  "text",
  "modality",
  "timezone",
  "referents",
] as const;

export function invalid(message: string): never {
  throw new AssistantError("invalid_request", message);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isOneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
): value is T {
  return (
    typeof value === "string" && (allowed as readonly string[]).includes(value)
  );
}

export function isAssistantCapabilityId(
  value: unknown,
): value is AssistantCapabilityId {
  return isOneOf(value, CAPABILITY_IDS);
}

export function assertAssistantCapabilityId(
  value: unknown,
): AssistantCapabilityId {
  if (!isAssistantCapabilityId(value)) {
    invalid("That capability is not available to this runtime.");
  }
  return value;
}

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}

export function assertUuid(value: unknown, label: string): string {
  if (!isUuid(value)) invalid(`A valid ${label} is required.`);
  return value;
}

export function isIanaTimezone(value: unknown): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > 64)
    return false;
  if (value !== "UTC" && /\s/.test(value)) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

function boundedString(
  value: unknown,
  label: string,
  max: number,
  min = 1,
): string {
  if (typeof value !== "string") invalid(`${label} must be text.`);
  const text = value.trim();
  if (text.length < min || text.length > max)
    invalid(`${label} must be ${min}-${max} characters.`);
  return text;
}

/** Clamps retained text to the storage bound instead of trusting the caller. */
export function clampText(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max);
}

export function parseAssistantMessageRequest(
  payload: unknown,
): AssistantMessageRequest {
  if (!isRecord(payload)) invalid("A JSON object body is required.");
  for (const key of Object.keys(payload)) {
    if (!(MESSAGE_REQUEST_FIELDS as readonly string[]).includes(key)) {
      invalid(`The assistant request does not accept "${key}".`);
    }
  }
  const requestId = assertUuid(payload.request_id, "request ID");
  const text = boundedString(
    payload.text,
    "Question",
    LIMITS.maxQuestionLength,
  );
  if (!isOneOf(payload.modality, MODALITIES))
    invalid("Modality must be TEXT or VOICE.");
  const timezone = payload.timezone ?? null;
  if (timezone !== null && !isIanaTimezone(timezone)) {
    invalid("Timezone must be an IANA time zone name.");
  }
  const rawReferents = payload.referents ?? [];
  if (
    !Array.isArray(rawReferents) ||
    rawReferents.length > LIMITS.maxReferents
  ) {
    invalid(`At most ${LIMITS.maxReferents} referents are accepted.`);
  }
  const referents = rawReferents.map((referent, index) =>
    boundedString(referent, `Referent ${index + 1}`, 128),
  );
  return {
    request_id: requestId,
    text,
    modality: payload.modality,
    timezone: timezone as string | null,
    referents,
  };
}

export function parseCitation(payload: unknown): AssistantCitation {
  if (!isRecord(payload)) invalid("A citation must be an object.");
  const nullableText = (value: unknown, max: number): string | null => {
    if (value === null || value === undefined) return null;
    if (typeof value !== "string") invalid("A citation selector must be text.");
    return clampText(value, max);
  };
  return {
    provider: boundedString(payload.provider, "Citation provider", 64),
    source_type: boundedString(payload.source_type, "Citation source type", 64),
    external_resource_id: nullableText(payload.external_resource_id, 512),
    evidence_id: nullableText(payload.evidence_id, 128),
    connection_id: nullableText(payload.connection_id, 128),
    observed_at: nullableText(payload.observed_at, 64),
  };
}

export function parseItemBlock(payload: unknown): AssistantItemBlock {
  if (!isRecord(payload)) invalid("An item block must be an object.");
  const description = payload.description;
  if (description !== null && typeof description !== "string") {
    invalid("Item description must be text or null.");
  }
  const dueAt = payload.due_at;
  if (dueAt !== null && typeof dueAt !== "string")
    invalid("Item due date must be text or null.");
  const band = payload.band;
  if (band !== null && band !== undefined && typeof band !== "string") {
    invalid("Item band must be text or null.");
  }
  const sources = payload.sources ?? [];
  if (!Array.isArray(sources)) invalid("Item sources must be a list.");
  return {
    id: boundedString(payload.id, "Item ID", 128),
    type: boundedString(payload.type, "Item type", 64),
    title: boundedString(payload.title, "Item title", 300),
    description: description === null ? null : clampText(description, 1000),
    status: boundedString(payload.status, "Item status", 64),
    due_at: dueAt as string | null,
    band: typeof band === "string" ? band : null,
    sources: sources.slice(0, LIMITS.maxCitations).map(parseCitation),
  };
}

export function parseMeetingBriefing(
  payload: unknown,
): AssistantMeetingBriefing {
  if (!isRecord(payload) || !isUuid(payload.commitment_id))
    invalid("A meeting briefing needs an exact commitment ID.");
  const startsAt = boundedString(payload.starts_at, "Meeting start", 64);
  if (!Number.isFinite(Date.parse(startsAt)))
    invalid("Meeting start must be a date-time.");
  if (
    !Number.isInteger(payload.minutes_until) ||
    (payload.minutes_until as number) < 0 ||
    (payload.minutes_until as number) > 525_600
  )
    invalid("Meeting timing is invalid.");
  const description = payload.description;
  if (description !== null && typeof description !== "string")
    invalid("Meeting description must be text or null.");
  if (
    !Array.isArray(payload.related_commitments) ||
    payload.related_commitments.length > 20 ||
    !Array.isArray(payload.prep_points) ||
    payload.prep_points.length > 12
  )
    invalid("Meeting briefing exceeds its bounds.");
  return {
    commitment_id: payload.commitment_id,
    title: boundedString(payload.title, "Meeting title", 300),
    starts_at: startsAt,
    minutes_until: payload.minutes_until as number,
    description: description === null ? null : clampText(description, 1000),
    related_commitments: payload.related_commitments.map((entry) => {
      if (!isRecord(entry) || !isUuid(entry.id))
        invalid("A related commitment needs an exact ID.");
      return {
        id: entry.id,
        title: boundedString(entry.title, "Related commitment title", 300),
        status: boundedString(entry.status, "Related commitment status", 64),
      };
    }),
    prep_points: payload.prep_points.map((point) =>
      boundedString(point, "Meeting prep point", 400),
    ),
  };
}

export function parseAssistantBlock(payload: unknown): AssistantBlock {
  if (!isRecord(payload)) invalid("A response block must be an object.");
  const kind = payload.kind;
  if (!isOneOf(kind, BLOCK_KINDS)) invalid("Unknown response block kind.");
  switch (kind) {
    case "ANSWER":
      return {
        kind,
        text: boundedString(
          payload.text,
          "Answer text",
          LIMITS.maxAnswerLength,
        ),
      };
    case "ITEM":
      return { kind, item: parseItemBlock(payload.item) };
    case "MEETING_BRIEFING":
      return { kind, meeting: parseMeetingBriefing(payload.meeting) };
    case "CLASS_NAVIGATION":
      if (payload.label !== "Open Calendar" && payload.label !== "Open Class")
        invalid("Unknown class navigation label.");
      return {
        kind,
        label: payload.label,
        connection_id: assertUuid(payload.connection_id, "connection ID"),
        resource_id: assertUuid(payload.resource_id, "resource ID"),
      };
    case "DETAILS": {
      if (
        !Array.isArray(payload.lines) ||
        payload.lines.length > LIMITS.maxDetails
      ) {
        invalid(`At most ${LIMITS.maxDetails} detail lines are accepted.`);
      }
      return {
        kind,
        lines: payload.lines.map((line, index) =>
          boundedString(line, `Detail ${index + 1}`, 400),
        ),
      };
    }
    case "CITATIONS": {
      if (
        !Array.isArray(payload.citations) ||
        payload.citations.length > LIMITS.maxCitations
      ) {
        invalid(`At most ${LIMITS.maxCitations} citations are accepted.`);
      }
      return { kind, citations: payload.citations.map(parseCitation) };
    }
    case "NOTICE": {
      if (!isOneOf(payload.state, RESPONSE_STATES))
        invalid("Unknown notice state.");
      return {
        kind,
        state: payload.state,
        text: boundedString(
          payload.text,
          "Notice text",
          LIMITS.maxAnswerLength,
        ),
      };
    }
    case "SUGGESTIONS": {
      if (
        !Array.isArray(payload.queries) ||
        payload.queries.length > LIMITS.maxSuggestions
      ) {
        invalid(`At most ${LIMITS.maxSuggestions} suggestions are accepted.`);
      }
      return {
        kind,
        queries: payload.queries.map((query, index) =>
          boundedString(query, `Suggestion ${index + 1}`, 200),
        ),
      };
    }
  }
}

export function parsePresentationPlan(
  payload: unknown,
): AssistantPresentationPlan {
  if (!isRecord(payload)) invalid("A presentation plan must be an object.");
  if (!isOneOf(payload.presentation, PRESENTATIONS))
    invalid("Unknown presentation choice.");
  if (typeof payload.speak !== "boolean")
    invalid("Presentation speak must be a boolean.");
  const speechText = payload.speech_text;
  if (speechText !== null && typeof speechText !== "string") {
    invalid("Speech text must be text or null.");
  }
  if (payload.speak && (speechText === null || speechText.length === 0)) {
    invalid("A speaking plan requires speech text.");
  }
  if (!Array.isArray(payload.blocks) || payload.blocks.length > 64) {
    invalid("A presentation plan accepts at most 64 blocks.");
  }
  return {
    presentation: payload.presentation,
    speak: payload.speak,
    speech_text:
      speechText === null
        ? null
        : clampText(speechText, LIMITS.maxSpeechLength),
    blocks: payload.blocks.map(parseAssistantBlock),
  };
}

function boundedConfidence(value: unknown, label: string): number {
  if (typeof value !== "number" || !(value >= 0 && value <= 1)) {
    invalid(`${label} confidence must be between 0 and 1.`);
  }
  return value;
}

function nullableSlotText(
  value: unknown,
  label: string,
  max: number,
): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") invalid(`${label} must be text or null.`);
  const text = value.trim();
  if (text.length === 0 || text.length > max) {
    invalid(`${label} must be 1-${max} characters.`);
  }
  return text;
}

export function parseIntentEntitySlot(
  payload: unknown,
): AssistantIntentEntitySlot {
  if (!isRecord(payload)) invalid("An intent entity slot must be an object.");
  if (!isOneOf(payload.kind, INTENT_ENTITY_KINDS))
    invalid("Unknown intent entity kind.");
  const value = nullableSlotText(
    payload.value,
    "Intent entity",
    LIMITS.maxIntentSlotLength,
  );
  if (payload.kind === "NONE" && value !== null)
    invalid("An absent entity cannot carry a value.");
  if (payload.kind !== "NONE" && value === null)
    invalid("A named entity requires its source text.");
  return {
    kind: payload.kind,
    value,
    confidence: boundedConfidence(payload.confidence, "Intent entity"),
  };
}

export function parseIntentTimeSlot(payload: unknown): AssistantIntentTimeSlot {
  if (!isRecord(payload)) invalid("An intent time slot must be an object.");
  if (!isOneOf(payload.kind, INTENT_TIME_KINDS))
    invalid("Unknown intent time kind.");
  const expression = nullableSlotText(
    payload.expression,
    "Intent time",
    LIMITS.maxIntentSlotLength,
  );
  if (payload.kind === "NONE" && expression !== null)
    invalid("An absent time slot cannot carry an expression.");
  if (payload.kind !== "NONE" && expression === null)
    invalid("A time slot requires its source text.");
  return {
    kind: payload.kind,
    expression,
    confidence: boundedConfidence(payload.confidence, "Intent time"),
  };
}

/**
 * A follow-up pointer. `allowBoundTurn` is false for upstream plans: only this
 * runtime may bind an ordinal to a session-owned turn selector.
 */
export function parseIntentReferenceSlot(
  payload: unknown,
  options?: { allowBoundTurn?: boolean },
): AssistantIntentReferenceSlot {
  if (!isRecord(payload))
    invalid("An intent reference slot must be an object.");
  if (!isOneOf(payload.kind, INTENT_REFERENCE_KINDS))
    invalid("Unknown intent reference kind.");
  const ordinal = payload.ordinal ?? null;
  if (ordinal !== null) {
    if (
      !Number.isInteger(ordinal) ||
      (ordinal as number) < 1 ||
      (ordinal as number) > LIMITS.maxPlanReferences
    ) {
      invalid("A recent-turn ordinal must be within the recent-turn bound.");
    }
  }
  if (payload.kind === "NONE" && ordinal !== null)
    invalid("An absent reference cannot carry an ordinal.");
  if (payload.kind === "RECENT_TURN" && ordinal === null)
    invalid("A recent-turn reference requires an ordinal.");
  const turnId = payload.turn_id ?? null;
  if (turnId !== null) {
    if (options?.allowBoundTurn !== true) {
      invalid("A plan may not supply its own session turn selector.");
    }
    assertUuid(turnId, "turn ID");
  }
  return {
    kind: payload.kind,
    ordinal: ordinal as number | null,
    turn_id: turnId as string | null,
  };
}

const EMPTY_ENTITY = { kind: "NONE", value: null, confidence: 1 } as const;
const EMPTY_TIME = { kind: "NONE", expression: null, confidence: 1 } as const;
const EMPTY_REFERENCE = { kind: "NONE", ordinal: null, turn_id: null } as const;

export function parsePlannedIntent(
  payload: unknown,
  options?: { allowBoundTurn?: boolean },
): PlannedIntent {
  if (!isRecord(payload)) invalid("A planned intent must be an object.");
  if (!isOneOf(payload.kind, INTENT_KINDS)) invalid("Unknown intent kind.");
  const capabilityId = payload.capability_id ?? null;
  if (capabilityId !== null && !isAssistantCapabilityId(capabilityId)) {
    invalid("Unknown capability in the plan.");
  }
  const confidence = boundedConfidence(payload.confidence, "Intent");
  const requiresClarification = payload.requires_clarification ?? false;
  if (typeof requiresClarification !== "boolean") {
    invalid("A plan must state whether it needs clarification.");
  }
  const clarification = payload.clarification ?? null;
  if (clarification !== null && typeof clarification !== "string") {
    invalid("A clarification must be text or null.");
  }
  if (requiresClarification && (clarification ?? "").trim().length === 0) {
    invalid("A clarification requirement needs its question.");
  }
  if (
    clarification !== null &&
    clarification.trim().length > LIMITS.maxClarificationLength
  ) {
    invalid(
      `A clarification must be at most ${LIMITS.maxClarificationLength} characters.`,
    );
  }
  if (!requiresClarification && clarification !== null) {
    invalid("A clarification question requires the requirement flag.");
  }
  if (payload.kind === "assistant.clarify" && !requiresClarification) {
    invalid("The clarify route must require a clarification.");
  }
  return {
    kind: payload.kind,
    capability_id: capabilityId,
    question: boundedString(
      payload.question,
      "Intent question",
      LIMITS.maxQuestionLength,
    ),
    confidence,
    entity: parseIntentEntitySlot(payload.entity ?? EMPTY_ENTITY),
    time: parseIntentTimeSlot(payload.time ?? EMPTY_TIME),
    reference: parseIntentReferenceSlot(
      payload.reference ?? EMPTY_REFERENCE,
      options,
    ),
    requires_clarification: requiresClarification,
    clarification:
      clarification === null
        ? null
        : clampText(clarification.trim(), LIMITS.maxClarificationLength),
  };
}

export function parseIntentPlan(
  payload: unknown,
  options?: { allowBoundTurn?: boolean },
): IntentPlan {
  if (!isRecord(payload)) invalid("A plan must be an object.");
  if (payload.version !== 1) invalid("Unknown plan version.");
  if (
    !Array.isArray(payload.intents) ||
    payload.intents.length < 1 ||
    payload.intents.length > LIMITS.maxIntents
  ) {
    invalid(`A plan needs 1-${LIMITS.maxIntents} intents.`);
  }
  return {
    version: 1,
    intents: payload.intents.map((intent) =>
      parsePlannedIntent(intent, options),
    ),
  };
}

export function parseCapabilityDecision(payload: unknown): CapabilityDecision {
  if (!isRecord(payload)) invalid("A capability decision must be an object.");
  if (!isOneOf(payload.kind, DECISION_KINDS))
    invalid("Unknown capability decision kind.");
  const capabilityId = payload.capability_id ?? null;
  if (capabilityId !== null && !isAssistantCapabilityId(capabilityId)) {
    invalid("Unknown capability in the decision.");
  }
  if (
    payload.requires_approval !== false &&
    payload.requires_approval !== true
  ) {
    invalid("A decision must state whether approval is required.");
  }
  if (!isOneOf(payload.action_state, ACTION_STATES))
    invalid("Unknown action state.");
  if (!isOneOf(payload.response_state, RESPONSE_STATES))
    invalid("Unknown response state.");
  const target = payload.target ?? null;
  if (target !== null && typeof target !== "string")
    invalid("Decision target must be text or null.");
  const actionId = payload.action_id ?? null;
  if (actionId !== null) assertUuid(actionId, "action ID");
  return {
    kind: payload.kind,
    capability_id: capabilityId,
    target: target === null ? null : clampText(target, 128),
    reason: boundedString(payload.reason, "Decision reason", 120),
    requires_approval: payload.requires_approval,
    action_state: payload.action_state,
    action_id: actionId as string | null,
    response_state: payload.response_state,
  };
}
