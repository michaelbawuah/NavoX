/**
 * SPEC-008 NavoXbot shared contracts.
 *
 * This module stays type-only to match the repository convention for
 * `@navox/contracts`: the shapes below are the authority for what the
 * TypeScript runtime may persist or return. Executable validators live in
 * `@navox/assistant-runtime` and are called at every API and adapter boundary.
 *
 * A presentation choice (`TEXT`, `VOICE`, `BOTH`) never carries authority. It
 * only decides what the client renders and whether a spoken answer is offered.
 */

/** How the client submitted a turn. Text is a typed turn, voice is clicked-to-talk. */
export type AssistantModality = "TEXT" | "VOICE";

/** How the client wants the answer surfaced. Not an authority signal. */
export type AssistantPresentation = "TEXT" | "VOICE" | "BOTH";

/**
 * The delivery preference the server recorded for one answer.
 *
 * `SPEAK` means the operator asked for that answer in words ("read it to me"),
 * so the client treats it as an explicit request; `SUPPRESS` means the answer
 * stays silent; `AUTOMATIC` follows the session's Voice Mode preference. The
 * client never supplies this value and it carries no authority.
 */
export type AssistantDeliveryIntent = "AUTOMATIC" | "SPEAK" | "SUPPRESS";

/**
 * Terminal state of an assistant turn.
 *
 * - `READY`      an owning service answered from its current facts.
 * - `CLARIFY`    the request was understood but is out of the bounded M1 route.
 * - `UNAVAILABLE` an owning service failed, changed, or is not reachable.
 * - `WITHHELD`   the account may not see the requested facts.
 */
export type AssistantResponseState =
  | "READY"
  | "CLARIFY"
  | "UNAVAILABLE"
  | "WITHHELD";

export type AssistantActionState =
  | "NONE"
  | "PROPOSED"
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "EXECUTING"
  | "EXECUTED"
  | "FAILED"
  | "DECLINED"
  | "EXPIRED";

/**
 * Capabilities the runtime may delegate to. Consequential routes are added
 * here only with their SPEC-001 approval and SPEC-003 execution contracts.
 */
export type AssistantCapabilityId =
  | "today.read"
  | "email.search"
  | "subscription.search"
  | "news.read"
  | "weather.read"
  | "class.next"
  | "time.now"
  | "action.history";

/**
 * Bounded planning vocabulary. Unknown values are rejected by the validators,
 * and a route is only usable when the capability registry also owns it.
 */
export type AssistantIntentKind =
  | "today.read"
  | "email.search"
  | "subscription.search"
  | "news.read"
  | "weather.read"
  | "class.next"
  | "time.now"
  | "action.history"
  | "assistant.delivery"
  | "assistant.clarify";

/**
 * Browser adapter state. `UNSUPPORTED` is an explicit typed fallback, and
 * `MUTED` is the resting state while automatic answer speech is suppressed.
 * A voice state never carries approval, action or capability authority.
 */
export type AssistantVoiceState =
  | "IDLE"
  | "UNSUPPORTED"
  | "LISTENING"
  | "TRANSCRIBING"
  | "THINKING"
  | "MUTED"
  | "SPEAKING"
  | "STOPPED";

export type AssistantErrorCode =
  | "invalid_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "expired"
  | "unavailable"
  | "unsupported"
  | "misconfigured";

/** A citation keeps selectors and IDs, never copied third-party evidence text. */
export interface AssistantCitation {
  provider: string;
  source_type: string;
  external_resource_id: string | null;
  evidence_id: string | null;
  connection_id: string | null;
  observed_at: string | null;
}

export interface AssistantItemBlock {
  id: string;
  type: string;
  title: string;
  description: string | null;
  status: string;
  due_at: string | null;
  band: string | null;
  sources: AssistantCitation[];
}

export interface AssistantMeetingBriefing {
  commitment_id: string;
  title: string;
  starts_at: string;
  minutes_until: number;
  description: string | null;
  related_commitments: { id: string; title: string; status: string }[];
  prep_points: string[];
}

/** A scheduled academic meeting synthesized from authorized Canvas/Calendar records. */
export interface ClassMeeting {
  courseId: string | null;
  courseName: string | null;
  courseCode: string | null;
  section: string | null;
  startAt: string | null;
  endAt: string | null;
  location: string | null;
  meetingUrl: string | null;
  status: "SCHEDULED" | "CANCELLED" | "UNCERTAIN";
  sourceRefs: {
    provider: "canvas" | "google";
    resourceId: string;
    connectionId: string;
    externalResourceId: string;
    updatedAt: string | null;
    navigationAvailable: boolean;
  }[];
  sourceSchedules: {
    provider: "canvas" | "google";
    startAt: string;
    endAt: string | null;
    location: string | null;
    status: "SCHEDULED" | "CANCELLED";
    updatedAt: string | null;
  }[];
  confidence: "CORROBORATED" | "SINGLE_SOURCE" | "CONFLICTED";
  scheduleAuthority: "canvas" | "google" | null;
  conflictState:
    | "NONE"
    | "TIME_CONFLICT"
    | "LOCATION_CONFLICT"
    | "STATUS_CONFLICT"
    | "UNRESOLVED";
}

/** Structured response blocks rendered by the `/navox` client. */
export type AssistantBlock =
  | { kind: "ANSWER"; text: string }
  | { kind: "ITEM"; item: AssistantItemBlock }
  | { kind: "DETAILS"; lines: string[] }
  | { kind: "CITATIONS"; citations: AssistantCitation[] }
  | { kind: "NOTICE"; state: AssistantResponseState; text: string }
  | { kind: "SUGGESTIONS"; queries: string[] }
  | { kind: "MEETING_BRIEFING"; meeting: AssistantMeetingBriefing }
  | {
      kind: "CLASS_NAVIGATION";
      label: "Open Calendar" | "Open Class";
      connection_id: string;
      resource_id: string;
    };

/**
 * Grounded slots. Each one restates something from the operator's own words;
 * none of them is permission, an identifier or a delegated capability.
 */
export type AssistantIntentEntityKind =
  | "NONE"
  | "PERSON"
  | "ORGANIZATION"
  | "PROJECT"
  | "TOPIC";

export interface AssistantIntentEntitySlot {
  kind: AssistantIntentEntityKind;
  value: string | null;
  confidence: number;
}

export type AssistantIntentTimeKind =
  | "NONE"
  | "RELATIVE"
  | "ABSOLUTE"
  | "RANGE";

export interface AssistantIntentTimeSlot {
  kind: AssistantIntentTimeKind;
  expression: string | null;
  confidence: number;
}

export type AssistantIntentReferenceKind = "NONE" | "RECENT_TURN";

/**
 * A follow-up pointer. `ordinal` is a position in the recent user turns this
 * runtime supplied; `turn_id` is the session-owned selector this runtime bound
 * it to. Upstream plans may not supply `turn_id`.
 */
export interface AssistantIntentReferenceSlot {
  kind: AssistantIntentReferenceKind;
  ordinal: number | null;
  turn_id: string | null;
}

/**
 * A single bounded intent. The runtime plans read-only routes and carries the
 * operator's original question to the owning service unchanged.
 */
export interface PlannedIntent {
  kind: AssistantIntentKind;
  capability_id: AssistantCapabilityId | null;
  question: string;
  confidence: number;
  entity: AssistantIntentEntitySlot;
  time: AssistantIntentTimeSlot;
  reference: AssistantIntentReferenceSlot;
  requires_clarification: boolean;
  clarification: string | null;
}

export interface IntentPlan {
  version: 1;
  intents: PlannedIntent[];
}

/** The runtime's validated decision about one capability invocation. */
export interface CapabilityDecision {
  /**
   * `PRESENT` is a read-only re-presentation of an already-saved answer, such
   * as an explicit Read aloud request. It delegates to no capability.
   */
  kind:
    | "DELEGATE"
    | "PRESENT"
    | "CLARIFY"
    | "UNAVAILABLE"
    | "WITHHELD"
    | "REFUSED";
  capability_id: AssistantCapabilityId | null;
  target: string | null;
  reason: string;
  requires_approval: boolean;
  action_state: AssistantActionState;
  action_id: string | null;
  response_state: AssistantResponseState;
}

/** What the client should render and whether it may speak the answer. */
export interface AssistantPresentationPlan {
  presentation: AssistantPresentation;
  speak: boolean;
  speech_text: string | null;
  /**
   * Server-recorded delivery preference. The client reads it to decide whether
   * this answer was explicitly requested; it never sends or overrides it.
   */
  delivery: AssistantDeliveryIntent;
  blocks: AssistantBlock[];
}

export interface AssistantGoalRef {
  goal_id: string;
  created_at: string;
}

export interface AssistantActionRef {
  action_id: string;
  state: AssistantActionState;
}

export interface AssistantTurnView {
  id: string;
  sequence: number;
  modality: AssistantModality;
  state: AssistantResponseState;
  question: string | null;
  plan: IntentPlan | null;
  decision: CapabilityDecision | null;
  presentation: AssistantPresentationPlan | null;
  action_refs: AssistantActionRef[];
  created_at: string;
}

export interface AssistantSessionView {
  id: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  status: "active" | "deleted" | "expired";
  turns: AssistantTurnView[];
}

/** The only fields a client may supply. Everything else is server-derived. */
export interface AssistantMessageRequest {
  request_id: string;
  text: string;
  modality: AssistantModality;
  timezone: string | null;
  referents: string[];
}

export interface AssistantMessageResponse {
  session_id: string;
  turn: AssistantTurnView;
  replay: boolean;
}

export interface CreateAssistantSessionResponse {
  session: AssistantSessionView;
}

export interface AssistantSessionResponse {
  session: AssistantSessionView;
}

export interface DeleteAssistantSessionResponse {
  session_id: string;
  deleted: true;
}

export interface AssistantErrorResponse {
  error: {
    code: AssistantErrorCode;
    message: string;
    retryable: boolean;
  };
}

/** A SPEC-001/003-backed reply draft selected from one session-owned email turn. */
export interface AssistantEmailDraftVersion {
  version: number;
  to: string[];
  subject: string;
  body: string;
  created_by: "AI" | "USER";
  payload_hash: string;
}

export interface AssistantEmailDraft {
  id: string;
  binding_kind: "KNOWLEDGE_EMAIL";
  commitment_id: null;
  source_id: string;
  current_version: number;
  status: string;
  action_id: string | null;
  versions: AssistantEmailDraftVersion[];
}

/** Exact action facts returned by the existing SPEC-001/003 approval service. */
export interface AssistantEmailAction {
  id: string;
  status: string;
  payload_hash: string;
  payload: {
    sender: string;
    to: string;
    subject: string;
    body_text: string;
    draft_id: string;
    draft_version: number;
    connection_id: string;
    post_send_state: "unchanged";
    reply: { source_message_id: string; thread_id: string };
  };
  approval: {
    status: string;
    expires_at: string;
    action_payload_hash: string;
  } | null;
  result: { message_id?: string; thread_id?: string };
}
