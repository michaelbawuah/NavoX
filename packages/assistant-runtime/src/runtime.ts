import { randomUUID } from "node:crypto";
import type {
  AssistantBlock,
  AssistantEmailAction,
  AssistantEmailDraft,
  AssistantEvidenceResponse,
  AssistantGoalView,
  AssistantMessageRequest,
  AssistantMessageResponse,
  AssistantResponseState,
  AssistantSessionView,
  CapabilityDecision,
  IntentPlan,
  PlannedIntent,
} from "@navox/contracts";
import {
  ACTION_HISTORY_LIMIT,
  actionHistorySelector,
  answerActionHistory,
  parseActionHistory,
} from "./activity";
import {
  assertRegistryIntegrity,
  capabilityForIntentKind,
} from "./capabilities";
import { answerNextClass, parseClassSourceSnapshot } from "./class-meetings";
import { type DeliveryResolution, resolveDeliveryIntent } from "./delivery";
import {
  decideEmailSearch,
  decideEmailSelection,
  type EmailContent,
  type EmailContentIssue,
  type EmailSourceType,
  emailContentTarget,
  parseEmailResourceDetail,
  parseEmailSearchOutcome,
  selectedEmailTarget,
} from "./email";
import { createAssistantEmailActions } from "./email-actions";
import { AssistantError, isAssistantError, toAssistantError } from "./errors";
import {
  type AccountScope,
  type NavoxUpstream,
  PLANNER_NO_PROVIDER,
} from "./gateway";
import {
  type AssistantGoalDispatcher,
  type AssistantGoalService,
  createAssistantGoalService,
  goalView,
} from "./goal-service";
import {
  assertTurnBudget,
  fingerprintTurn,
  resolveReplay,
  retentionWindow,
} from "./ledger";
import { LIMITS } from "./limits";
import { meetingBlocks } from "./meeting";
import {
  type NavigationSourceType,
  navigationHref,
  navigationTargets,
  parseClassNavigationTarget,
  parseResourceNavigation,
} from "./navigation";
import {
  answerNews,
  clarifyNews,
  namedNewsMatches,
  newsSelector,
  newsTrends,
  normalizeGenericNewsPlan,
  parseNewsFeed,
  parseNewsStory,
  parseNewsSummary,
} from "./news";
import { parseIntentPlanEnvelope, planTurn } from "./planner";
import { buildPresentationPlan } from "./presentation";
import {
  type AssistantRequestClaim,
  type AssistantSessionRecord,
  type AssistantStore,
  type AssistantTurnRecord,
  toTurnView,
} from "./store";
import {
  answerSubscription,
  clarifySubscriptions,
  parseSubscriptionCancellation,
  parseSubscriptionSearch,
  subscriptionSelector,
} from "./subscriptions";
import { answerCurrentTime } from "./time";
import {
  blocksFromToday,
  decideToday,
  noticeBlocks,
  type TodayQueryResult,
} from "./today";
import {
  assertUuid,
  clampText,
  parseAssistantMessageRequest,
  parseCapabilityDecision,
  parseIntentPlan,
} from "./validate";
import { type DraftSpeech, spokenTextForTurn } from "./voice";
import { parseWeather, weatherAnswer, weatherSelector } from "./weather";

/** Qualified planning failures. A plan is a proposal, never a fallback route. */
const PLAN_FAILURE_TEXT: Record<string, string> = {
  invalid_request:
    "That request could not be planned safely. Nothing was changed.",
  misconfigured:
    "The assistant cannot plan that request in this deployment. Nothing was changed.",
  unsupported:
    "That assistant lookup is not enabled in this deployment. Nothing was changed.",
  forbidden: "This account cannot read those sources. Nothing was changed.",
  conflict: "That conversation changed. Please ask again. Nothing was changed.",
  not_found:
    "The assistant could not find that information. Nothing was changed.",
  unavailable:
    "The assistant could not look that up right now. Nothing was changed.",
};

export interface AssistantRuntimeDeps {
  store: AssistantStore;
  upstream: NavoxUpstream;
  /**
   * The durable goal dispatcher. Absent, or null, when this deployment has no
   * configured Temporal target; a goal then stays truthfully pending with an
   * explicit failed dispatch instead of a claimed completion.
   */
  goalDispatcher?: AssistantGoalDispatcher | null;
  /** Test seam. Production builds the goal service from the store. */
  goals?: AssistantGoalService;
  now?: () => Date;
  newId?: () => string;
  sleep?: (ms: number) => Promise<void>;
  claimWaitMs?: number;
  claimPollMs?: number;
  claimStaleMs?: number;
}

export interface AssistantRuntime {
  createSession(input: { cookie: string }): Promise<AssistantSessionView>;
  readSession(input: {
    cookie: string;
    session_id: string;
  }): Promise<AssistantSessionView>;
  submitTurn(input: {
    cookie: string;
    session_id: string;
    body: unknown;
  }): Promise<AssistantMessageResponse>;
  deleteSession(input: { cookie: string; session_id: string }): Promise<void>;
  purgeExpired(): Promise<number>;
  createEmailDraft(input: {
    cookie: string;
    session_id: string;
    body: unknown;
  }): Promise<AssistantEmailDraft>;
  readEmailDraft(input: {
    cookie: string;
    session_id: string;
    source_turn_id: string;
    draft_id: string;
  }): Promise<AssistantEmailDraft>;
  reviseEmailDraft(input: {
    cookie: string;
    session_id: string;
    draft_id: string;
    body: unknown;
  }): Promise<AssistantEmailDraft>;
  prepareEmailDraft(input: {
    cookie: string;
    session_id: string;
    draft_id: string;
    body: unknown;
  }): Promise<AssistantEmailAction>;
  approveEmailDraft(input: {
    cookie: string;
    session_id: string;
    draft_id: string;
    body: unknown;
  }): Promise<AssistantEmailAction>;
  readEmailAction(input: {
    cookie: string;
    session_id: string;
    source_turn_id: string;
    draft_id: string;
  }): Promise<AssistantEmailAction>;
  /**
   * The bounded spoken text of the current, source-turn bound draft version.
   * The browser supplies selectors and the version it reviewed; a stale version
   * or an unreadable draft is refused rather than spoken.
   */
  readEmailDraftSpeech(input: {
    cookie: string;
    session_id: string;
    source_turn_id: string;
    draft_id: string;
    version: number;
  }): Promise<DraftSpeech>;
  /**
   * Resolves one guarded navigation target from a saved in-session turn.
   * Ownership is re-derived from the cookie and every authority is re-checked
   * live; a missing, stale or mismatched target fails closed.
   */
  resolveNavigationTarget(input: {
    cookie: string;
    session_id: string;
    turn_id: string;
    item_id: string;
  }): Promise<{ url: string }>;
  /**
   * Re-reads the current content of one saved evidence selector.
   *
   * Ownership is re-derived from the cookie and the exact selector is re-found
   * in the saved turn, so a foreign session, a retired turn, a moved revision
   * or a revoked source all fail closed instead of replaying stored text.
   */
  readEmailEvidence(input: {
    cookie: string;
    session_id: string;
    turn_id: string;
    item_id: string;
  }): Promise<AssistantEvidenceResponse>;
  /** Reads one durable goal, fenced by the owning session's scope. */
  readGoal(input: {
    cookie: string;
    goal_id: string;
  }): Promise<AssistantGoalView>;
  /** Lists a bounded number of durable goals for one owned session. */
  listSessionGoals(input: {
    cookie: string;
    session_id: string;
  }): Promise<AssistantGoalView[]>;
  /**
   * The recoverable dispatch path for one goal. Re-starting is idempotent, so
   * a duplicate never becomes a second durable job.
   */
  redispatchGoal(input: {
    cookie: string;
    goal_id: string;
  }): Promise<{ goal: AssistantGoalView; dispatched: boolean }>;
}

function sessionView(
  record: AssistantSessionRecord,
  turns: AssistantSessionView["turns"],
  now: Date,
): AssistantSessionView {
  const expired = Date.parse(record.expires_at) <= now.getTime();
  return {
    id: record.id,
    created_at: record.created_at,
    updated_at: record.updated_at,
    expires_at: record.expires_at,
    status: expired ? "expired" : "active",
    turns,
  };
}

function requireSession(
  record: AssistantSessionRecord | null,
  now: Date,
): AssistantSessionRecord {
  if (!record) {
    throw new AssistantError(
      "not_found",
      "That assistant session is no longer available.",
    );
  }
  if (Date.parse(record.expires_at) <= now.getTime()) {
    throw new AssistantError("expired", "That assistant session has expired.");
  }
  return record;
}

export function createAssistantRuntime(
  deps: AssistantRuntimeDeps,
): AssistantRuntime {
  assertRegistryIntegrity();
  const now = deps.now ?? (() => new Date());
  const newId = deps.newId ?? (() => randomUUID());
  const sleep =
    deps.sleep ??
    ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const claimWaitMs = deps.claimWaitMs ?? LIMITS.claimWaitMs;
  const claimPollMs = deps.claimPollMs ?? LIMITS.claimPollMs;
  const claimStaleMs = deps.claimStaleMs ?? LIMITS.claimStaleMs;
  /**
   * Bounded personal goals. The service is derived from the same store and the
   * same scope checks as a turn; it grants no new authority and no provider
   * call, and it never approves or executes a consequential action.
   */
  const goals =
    deps.goals ??
    createAssistantGoalService({
      store: deps.store,
      dispatcher: deps.goalDispatcher ?? null,
      now,
      newId,
    });

  function replayOf(
    existing: AssistantTurnRecord,
    fingerprint: string,
    sessionId: string,
  ): AssistantMessageResponse {
    if (
      resolveReplay(existing.request_fingerprint, fingerprint) === "conflict"
    ) {
      throw new AssistantError(
        "conflict",
        "That request ID was already used with a different question.",
      );
    }
    return {
      session_id: sessionId,
      turn: toTurnView(existing),
      replay: true,
    };
  }

  async function releaseClaim(
    sessionId: string,
    scope: AccountScope,
    requestId: string,
  ): Promise<void> {
    try {
      await deps.store.releaseClaimRequest({
        session_id: sessionId,
        scope,
        request_id: requestId,
      });
    } catch {
      // A stale claim is taken over after the stale window; never mask the
      // real failure with a cleanup error.
    }
  }

  async function resolveClaim(
    sessionId: string,
    scope: AccountScope,
    requestId: string,
    turnId: string,
  ): Promise<void> {
    try {
      await deps.store.resolveClaimRequest({
        session_id: sessionId,
        scope,
        request_id: requestId,
        turn_id: turnId,
        now: now().toISOString(),
      });
    } catch {
      // The turn is durable, so a retry still replays from the fast path.
    }
  }

  /**
   * Takes the durable per-request claim before any upstream call. A duplicate
   * either owns the work, waits for the owner, or conflicts on a changed
   * payload — never a second Today call.
   */
  async function acquireClaim(input: {
    sessionId: string;
    scope: AccountScope;
    request: AssistantMessageRequest;
    fingerprint: string;
    at: Date;
  }): Promise<
    | { owned: true; claim: AssistantRequestClaim }
    | { owned: false; claim: AssistantRequestClaim | null }
  > {
    const staleBefore = new Date(
      input.at.getTime() - claimStaleMs,
    ).toISOString();
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const result = await deps.store.claimRequest({
        session_id: input.sessionId,
        scope: input.scope,
        request_id: input.request.request_id,
        request_fingerprint: input.fingerprint,
        now: input.at.toISOString(),
        stale_before: staleBefore,
      });
      if (result.created && result.claim)
        return { owned: true, claim: result.claim };
      if (result.claim) return { owned: false, claim: result.claim };
      // The claim vanished between the failed insert and the read; try again.
      await sleep(claimPollMs);
    }
    throw new AssistantError(
      "unavailable",
      "The assistant could not reserve that question. Please try again.",
      { retryable: true },
    );
  }

  /** Waits for the owning request to resolve so a duplicate replays its result. */
  async function awaitClaimedTurn(input: {
    sessionId: string;
    scope: AccountScope;
    requestId: string;
  }): Promise<AssistantTurnRecord | null> {
    const deadline = Date.now() + claimWaitMs;
    for (;;) {
      const turn = await deps.store.findTurnByRequest({
        session_id: input.sessionId,
        scope: input.scope,
        request_id: input.requestId,
      });
      if (turn) return turn;
      const claim = await deps.store.readClaimRequest({
        session_id: input.sessionId,
        scope: input.scope,
        request_id: input.requestId,
      });
      if (!claim) return null;
      if (claim.status === "RESOLVED") {
        return deps.store.findTurnByRequest({
          session_id: input.sessionId,
          scope: input.scope,
          request_id: input.requestId,
        });
      }
      if (Date.now() >= deadline) return null;
      await sleep(claimPollMs);
    }
  }

  async function scopeFor(cookie: string): Promise<AccountScope> {
    try {
      return await deps.upstream.fetchAccount(cookie);
    } catch (error) {
      throw toAssistantError(error);
    }
  }

  async function loadSession(
    sessionId: string,
    scope: AccountScope,
  ): Promise<AssistantSessionRecord> {
    const record = await deps.store.readSession({
      session_id: sessionId,
      scope,
    });
    return requireSession(record, now());
  }

  function clarifyDecision(reason: string): CapabilityDecision {
    return parseCapabilityDecision({
      kind: "CLARIFY",
      capability_id: null,
      target: null,
      reason,
      requires_approval: false,
      action_state: "NONE",
      action_id: null,
      response_state: "CLARIFY",
    });
  }

  /** A presentation-only decision. It never delegates and never needs approval. */
  function presentDecision(reason: string): CapabilityDecision {
    return parseCapabilityDecision({
      kind: "PRESENT",
      capability_id: null,
      target: null,
      reason,
      requires_approval: false,
      action_state: "NONE",
      action_id: null,
      response_state: "READY",
    });
  }

  /**
   * The plan of a delivery-only turn. `assistant.delivery` resolves to no
   * capability, so this plan can never delegate or reach a source.
   */
  function deliveryPlan(text: string): IntentPlan {
    return parseIntentPlan({
      version: 1,
      intents: [
        {
          kind: "assistant.delivery",
          capability_id: null,
          question: text,
          confidence: 1,
        },
      ],
    });
  }

  /**
   * Answers a delivery-only utterance from the saved conversation. It never
   * reaches the SPEC-005 planner, never calls a source and never speaks an
   * ineligible turn: an unknown, out-of-range or unspeakable referent asks for
   * a clarification instead.
   */
  function deliveryTurnOutcome(input: {
    text: string;
    delivery: DeliveryResolution;
    turns: readonly AssistantTurnRecord[];
  }): {
    plan: IntentPlan;
    state: AssistantResponseState;
    decision: CapabilityDecision;
    blocks: AssistantBlock[];
  } {
    const plan = deliveryPlan(input.text);
    const clarify = (reason: string, text: string) => ({
      plan,
      state: "CLARIFY" as AssistantResponseState,
      decision: clarifyDecision(reason),
      blocks: noticeBlocks("CLARIFY", text),
    });
    // A successful read-aloud re-presents the saved answer: its blocks, its
    // bounded summary and a PRESENT decision that delegates nothing.
    const present = (source: AssistantTurnRecord) => ({
      plan,
      state: "READY" as AssistantResponseState,
      decision: presentDecision("delivery.read_aloud"),
      blocks: source.presentation.blocks,
    });
    if (input.delivery.wake_only) {
      return {
        plan,
        state: "READY" as AssistantResponseState,
        decision: presentDecision("delivery.wake_greeting"),
        blocks: [{ kind: "ANSWER" as const, text: "Hi, I'm listening." }],
      };
    }
    if (input.delivery.conversation_only) {
      const acknowledgment =
        input.delivery.conversation_only === "ACKNOWLEDGMENT";
      return {
        plan,
        state: "READY" as AssistantResponseState,
        decision: presentDecision(
          acknowledgment ? "delivery.acknowledgment" : "delivery.check_in",
        ),
        blocks: [
          {
            kind: "ANSWER" as const,
            text: acknowledgment
              ? "You're welcome."
              : "I'm ready to help. What's on your mind?",
          },
        ],
      };
    }
    if (input.delivery.intent === "SUPPRESS") {
      return clarify(
        "delivery.suppressed",
        "Okay — I won't read answers aloud unless you ask me to.",
      );
    }
    const eligible = input.turns.filter(
      (turn) => spokenTextForTurn(toTurnView(turn)) !== null,
    );
    const ordinal = input.delivery.ordinal;
    if (ordinal !== null) {
      const referenced =
        ordinal > 0 ? input.turns[ordinal - 1] : eligible.at(-1);
      if (!referenced) {
        return clarify(
          "delivery.unknown_referent",
          "I don't have that many answers in this conversation yet. Tell me which answer to read aloud.",
        );
      }
      if (spokenTextForTurn(toTurnView(referenced)) === null) {
        return clarify(
          "delivery.ineligible_referent",
          "That turn has no finished answer I can read aloud.",
        );
      }
      return present(referenced);
    }
    const last = eligible.at(-1);
    if (!last) {
      return clarify(
        "delivery.no_eligible_answer",
        "I don't have an answer in this conversation to read aloud yet. Ask me something first.",
      );
    }
    return present(last);
  }

  /** Converts a planning or delegation failure into a qualified turn outcome. */
  function planFailure(
    error: unknown,
    phase:
      | "plan"
      | "today"
      | "search"
      | "meeting"
      | "subscription"
      | "news"
      | "class"
      | "weather"
      | "activity" = "plan",
  ): {
    state: AssistantResponseState;
    decision: CapabilityDecision;
    blocks: AssistantBlock[];
  } {
    const failure = isAssistantError(error) ? error : toAssistantError(error);
    const state: AssistantResponseState =
      failure.code === "forbidden" ? "WITHHELD" : "UNAVAILABLE";
    const refused =
      failure.code === "invalid_request" || failure.code === "misconfigured";
    return {
      state,
      decision: parseCapabilityDecision({
        kind: refused
          ? "REFUSED"
          : state === "WITHHELD"
            ? "WITHHELD"
            : "UNAVAILABLE",
        capability_id: null,
        target: null,
        reason: `${phase}.${failure.code}`,
        requires_approval: false,
        action_state: "NONE",
        action_id: null,
        response_state: state,
      }),
      blocks: noticeBlocks(
        state,
        phase === "news" && failure.reason === "news_disabled"
          ? "News is not connected yet. I need a news source before I can give you current headlines."
          : (PLAN_FAILURE_TEXT[failure.code] ??
              "The assistant could not plan that request. Nothing was changed."),
      ),
    };
  }

  async function resolvedToday(input: {
    cookie: string;
    scope: AccountScope;
    request: AssistantMessageRequest;
    today?: TodayQueryResult;
  }): Promise<{
    state: AssistantResponseState;
    decision: CapabilityDecision;
    blocks: AssistantBlock[];
  }> {
    try {
      const today =
        input.today ??
        (await deps.upstream.queryToday(input.cookie, {
          query: input.request.text,
          timezone: input.request.timezone,
        }));
      if (today.intent === "meeting_prep") {
        const current = await scopeFor(input.cookie);
        if (
          current.user_id !== input.scope.user_id ||
          current.workspace_id !== input.scope.workspace_id
        )
          throw new AssistantError("forbidden", "This account changed.");
        try {
          const prep = await deps.upstream.getMeetingPrep(input.cookie);
          const decision = decideToday(today);
          return {
            state: decision.response_state,
            decision,
            blocks: meetingBlocks(today, prep),
          };
        } catch (error) {
          if (isAssistantError(error) && error.code === "unauthorized")
            throw error;
          return planFailure(error, "meeting");
        }
      }
      const decision = decideToday(today);
      return {
        state: decision.response_state,
        decision,
        blocks: blocksFromToday(today),
      };
    } catch (error) {
      if (isAssistantError(error) && error.code === "unauthorized") throw error;
      return planFailure(error, "today");
    }
  }

  function exactTodayFallback(
    question: string,
    today: TodayQueryResult,
  ): boolean {
    const normalized = question.trim().toLocaleLowerCase("en-US");
    return today.supported_queries.some(
      (example) => example.trim().toLocaleLowerCase("en-US") === normalized,
    );
  }

  /**
   * Reads the verified current content of the one candidate a complete search
   * named. Anything else (incomplete coverage, more than one hit, a thread, or
   * a refused detail read) is qualified, so a title or a listing is never
   * presented as the message.
   */
  async function emailContentFor(input: {
    cookie: string;
    target: {
      resource_id: string;
      source_type: EmailSourceType;
      source_version: string | null;
      fresh_until: string | null;
    } | null;
  }): Promise<EmailContent> {
    const target = input.target;
    if (!target) return { state: "UNAVAILABLE", reason: "unverified" };
    if (target.source_type === "EMAIL_THREAD") return { state: "THREAD_ONLY" };
    try {
      return parseEmailResourceDetail(
        await deps.upstream.getResourceDetail(input.cookie, target.resource_id),
        {
          resource_id: target.resource_id,
          source_type: target.source_type,
          source_version: target.source_version,
          fresh_until: target.fresh_until,
        },
        { now: now() },
      );
    } catch (error) {
      if (isAssistantError(error) && error.code === "unauthorized") throw error;
      return { state: "UNAVAILABLE", reason: "unverified" };
    }
  }

  /**
   * Resolves a planner-recognised navigation follow-up against this session's
   * own saved turn. The runtime names the guarded path; nothing here navigates
   * by itself and nothing delegates to a capability.
   */
  function navigationTurn(input: {
    sessionId: string;
    intent: PlannedIntent;
    turns: readonly AssistantTurnRecord[];
  }): {
    state: AssistantResponseState;
    decision: CapabilityDecision;
    blocks: AssistantBlock[];
  } {
    const clarify = (reason: string, text: string) => ({
      state: "CLARIFY" as AssistantResponseState,
      decision: clarifyDecision(reason),
      blocks: noticeBlocks("CLARIFY", text),
    });
    const reference = input.intent.reference;
    if (reference.kind !== "RECENT_TURN" || reference.turn_id === null) {
      return clarify(
        "navigation.missing_reference",
        "Tell me which earlier answer or item you mean, and I can point you to it.",
      );
    }
    const source = input.turns.find((turn) => turn.id === reference.turn_id);
    if (!source) {
      return clarify(
        "navigation.unknown_reference",
        "That earlier answer is not in this conversation. Ask again and I can point you to it.",
      );
    }
    const targets = navigationTargets(source.presentation.blocks);
    if (targets.length === 0) {
      return clarify(
        "navigation.no_target",
        "That answer did not include an item I can open in NavoX. Nothing was changed.",
      );
    }
    if (targets.length > 1) {
      return clarify(
        "navigation.ambiguous",
        "That answer cited more than one item. Tell me which one to open.",
      );
    }
    const target = targets[0] as ReturnType<typeof navigationTargets>[number];
    return {
      state: "READY",
      decision: presentDecision("navigation.resolved"),
      blocks: [
        { kind: "ANSWER", text: "Here is the item from your conversation." },
        {
          kind: "NAVIGATION",
          label: target.label,
          href: navigationHref(input.sessionId, source.id, target.item_id),
          source_type: target.source_type,
          evidence_id: target.item_id,
        },
        ...(target.citations.length > 0
          ? [
              {
                kind: "CITATIONS" as const,
                citations: target.citations.slice(0, LIMITS.maxCitations),
              },
            ]
          : []),
      ],
    };
  }

  /** The one qualified failure for a content state that cannot be shown. */
  function evidenceUnavailable(reason: EmailContentIssue): AssistantError {
    switch (reason) {
      case "changed":
        return new AssistantError(
          "conflict",
          "This message changed since the answer. Search again to read its current content.",
        );
      case "stale":
        return new AssistantError(
          "unavailable",
          "This indexed message is no longer fresh. Search again to read its current content.",
        );
      case "no_content":
        return new AssistantError(
          "unavailable",
          "This indexed message has no readable content. Nothing was changed.",
        );
      default:
        return new AssistantError(
          "unavailable",
          "The current content of this message could not be verified. Nothing was changed.",
        );
    }
  }

  /**
   * Free-form questions reach the registered SPEC-005 planner first. SPEC-002's
   * keyword classifier cannot establish that a whole question belongs to Today
   * (for example, weather "today" or a named subscription "renewal"). The
   * runtime validates the plan and delegates at most one read-only capability.
   */
  async function planFreeformTurn(input: {
    cookie: string;
    scope: AccountScope;
    record: AssistantSessionRecord;
    request: AssistantMessageRequest;
    turns: readonly AssistantTurnRecord[];
  }): Promise<{
    plan: IntentPlan;
    state: AssistantResponseState;
    decision: CapabilityDecision;
    blocks: AssistantBlock[];
  }> {
    const pinned = planTurn({ text: input.request.text });
    const references = input.turns.slice(-LIMITS.maxPlanReferences);

    // A free-form route is a SPEC-005 decision. When no qualified planner is
    // available, only an exact SPEC-002 supported template can fall back.
    let plan: IntentPlan;
    try {
      // Only the operator's own prior questions leave this runtime; answers from
      // other services are never forwarded as planner input.
      const raw = await deps.upstream.planIntents(input.cookie, {
        utterance: input.request.text,
        recentReferences: references.map((turn) => turn.question),
        sessionId: input.record.navox_session_id,
      });
      plan = parseIntentPlanEnvelope(raw, {
        utterance: input.request.text,
        sessionId: input.record.navox_session_id,
        recentTurns: references.map((turn) => ({
          turn_id: turn.id,
          question: turn.question,
        })),
      });
      plan = normalizeGenericNewsPlan(plan);
    } catch (error) {
      if (isAssistantError(error) && error.code === "unauthorized") throw error;
      const failure = isAssistantError(error) ? error : toAssistantError(error);
      if (failure.reason === PLANNER_NO_PROVIDER) {
        try {
          const today = await deps.upstream.queryToday(input.cookie, {
            query: input.request.text,
            timezone: input.request.timezone,
          });
          if (exactTodayFallback(input.request.text, today)) {
            return {
              plan: pinned,
              ...(await resolvedToday({ ...input, today })),
            };
          }
        } catch (fallbackError) {
          if (
            isAssistantError(fallbackError) &&
            fallbackError.code === "unauthorized"
          )
            throw fallbackError;
          return { plan: pinned, ...planFailure(fallbackError, "today") };
        }
      }
      return { plan: pinned, ...planFailure(error) };
    }

    // Phase 2: route resolution and delegation. Each route uses its exact
    // validated question span and the same session-owned scope.
    async function resolveOne(intent: PlannedIntent): Promise<{
      plan: IntentPlan;
      state: AssistantResponseState;
      decision: CapabilityDecision;
      blocks: AssistantBlock[];
    }> {
      try {
        const definition = capabilityForIntentKind(intent.kind);
        if (intent.requires_clarification) {
          return {
            plan,
            state: "CLARIFY",
            decision: clarifyDecision("plan.clarify"),
            blocks: noticeBlocks(
              "CLARIFY",
              intent.clarification ??
                "I need a little more detail before I can look that up.",
            ),
          };
        }
        // A navigation follow-up only points at an item an earlier saved turn
        // already cited. It delegates to no capability and resolves solely from
        // this session's own saved turns.
        if (intent.kind === "assistant.navigate") {
          return {
            plan,
            ...navigationTurn({
              sessionId: input.record.id,
              intent,
              turns: input.turns,
            }),
          };
        }
        if (definition === null) {
          return {
            plan,
            state: "CLARIFY",
            decision: clarifyDecision("plan.clarify"),
            blocks: noticeBlocks(
              "CLARIFY",
              "I need a little more detail before I can look that up.",
            ),
          };
        }
        if (definition.mode !== "read_only") {
          throw new AssistantError(
            "misconfigured",
            "That capability is not available in this assistant phase.",
          );
        }
        if (definition.id === "today.read") {
          return {
            plan,
            ...(await resolvedToday({
              ...input,
              request: { ...input.request, text: intent.question },
            })),
          };
        }
        // The current-time route is answered from this runtime's own injected
        // clock, so it never reaches an upstream service.
        if (definition.id === "time.now") {
          return { plan, ...answerCurrentTime(now(), input.request.timezone) };
        }
        // Recheck the current account before handing the request to another
        // owning service. Local rows stay fenced by the session scope.
        const current = await scopeFor(input.cookie);
        if (
          current.user_id !== input.scope.user_id ||
          current.workspace_id !== input.scope.workspace_id
        ) {
          throw new AssistantError(
            "forbidden",
            "This account cannot use that assistant capability.",
          );
        }
        if (definition.id === "subscription.search") {
          const selector = subscriptionSelector(intent);
          if (selector === null) {
            return {
              plan,
              state: "CLARIFY",
              decision: clarifyDecision("subscription.missing_entity"),
              blocks: noticeBlocks(
                "CLARIFY",
                "Which named subscription should I look up?",
              ),
            };
          }
          try {
            const rows = parseSubscriptionSearch(
              await deps.upstream.querySubscriptions(input.cookie, selector),
              selector,
            );
            if (rows.length !== 1)
              return { plan, ...clarifySubscriptions(rows) };
            // The second read must not cross an account switch while this turn
            // waits on SPEC-004's search response.
            const afterSearch = await scopeFor(input.cookie);
            if (
              afterSearch.user_id !== input.scope.user_id ||
              afterSearch.workspace_id !== input.scope.workspace_id
            ) {
              throw new AssistantError(
                "forbidden",
                "This account cannot use that assistant capability.",
              );
            }
            const row = rows[0];
            if (!row)
              throw new AssistantError("unavailable", "Subscription changed.");
            const cancellation = parseSubscriptionCancellation(
              await deps.upstream.getSubscriptionCancellation(
                input.cookie,
                row.id,
              ),
              row,
              input.scope,
            );
            return { plan, ...answerSubscription(row, cancellation) };
          } catch (error) {
            if (isAssistantError(error) && error.code === "unauthorized")
              throw error;
            return { plan, ...planFailure(error, "subscription") };
          }
        }
        if (definition.id === "news.read") {
          const selector = newsSelector(intent);
          if (selector === undefined) {
            return {
              plan,
              state: "CLARIFY",
              decision: clarifyDecision("news.ungrounded_entity"),
              blocks: noticeBlocks(
                "CLARIFY",
                "Which named news story should I look up?",
              ),
            };
          }
          try {
            const stories = parseNewsFeed(
              await deps.upstream.getTrendingNews(input.cookie),
              now(),
            );
            if (selector === null) return { plan, ...newsTrends(stories) };
            const matches = namedNewsMatches(stories, selector);
            if (matches.length !== 1) return { plan, ...clarifyNews(matches) };
            const selected = matches[0];
            if (!selected)
              throw new AssistantError("unavailable", "News changed.");
            const beforeDetail = await scopeFor(input.cookie);
            if (
              beforeDetail.user_id !== input.scope.user_id ||
              beforeDetail.workspace_id !== input.scope.workspace_id
            )
              throw new AssistantError(
                "forbidden",
                "This account cannot read that story.",
              );
            const detail = parseNewsStory(
              await deps.upstream.getNewsStory(input.cookie, selected.id),
              now(),
            );
            if (
              detail.id !== selected.id ||
              detail.version !== selected.version ||
              detail.verification_status !== selected.verification_status ||
              detail.headline !== selected.headline
            )
              throw new AssistantError(
                "unavailable",
                "That news story changed.",
              );
            const beforeSummary = await scopeFor(input.cookie);
            if (
              beforeSummary.user_id !== input.scope.user_id ||
              beforeSummary.workspace_id !== input.scope.workspace_id
            )
              throw new AssistantError(
                "forbidden",
                "This account cannot read that story.",
              );
            const summary = parseNewsSummary(
              await deps.upstream.getNewsSummary(input.cookie, detail.id),
              detail,
              now(),
            );
            return { plan, ...answerNews(detail, summary) };
          } catch (error) {
            if (isAssistantError(error) && error.code === "unauthorized")
              throw error;
            return { plan, ...planFailure(error, "news") };
          }
        }
        if (definition.id === "weather.read") {
          const selector = weatherSelector(intent);
          if (selector === undefined) {
            return {
              plan,
              state: "CLARIFY",
              decision: clarifyDecision("weather.unsupported_scope"),
              blocks: noticeBlocks(
                "CLARIFY",
                "I can check current weather for your configured city. Which current city reading did you mean?",
              ),
            };
          }
          try {
            const reading = parseWeather(
              await deps.upstream.getWeather(input.cookie),
              now(),
            );
            return { plan, ...weatherAnswer(reading, selector) };
          } catch (error) {
            if (isAssistantError(error) && error.code === "unauthorized")
              throw error;
            return { plan, ...planFailure(error, "weather") };
          }
        }
        if (definition.id === "class.next") {
          try {
            const snapshot = parseClassSourceSnapshot(
              await deps.upstream.getClassSources(input.cookie),
              now(),
            );
            return {
              plan,
              ...answerNextClass(snapshot, now(), input.request.timezone),
            };
          } catch (error) {
            if (isAssistantError(error) && error.code === "unauthorized")
              throw error;
            return { plan, ...planFailure(error, "class") };
          }
        }
        // The Activity route reuses the existing authenticated SPEC-001/003
        // action ledger. It binds minimal facts only and never reads a payload.
        if (definition.id === "action.history") {
          const scope = actionHistorySelector(intent);
          if (!scope) {
            return {
              plan,
              state: "CLARIFY",
              decision: clarifyDecision("action.history.unsupported_selector"),
              blocks: noticeBlocks(
                "CLARIFY",
                "I can report what NavoX did today or list your most recent actions. I cannot filter activity by that name or date yet.",
              ),
            };
          }
          try {
            const records = parseActionHistory(
              await deps.upstream.listActions(input.cookie, {
                limit: ACTION_HISTORY_LIMIT,
              }),
              { scope: input.scope, now: now() },
            );
            return {
              plan,
              ...answerActionHistory({
                records,
                scope,
                timezone: input.request.timezone,
                now: now(),
              }),
            };
          } catch (error) {
            if (isAssistantError(error) && error.code === "unauthorized")
              throw error;
            return { plan, ...planFailure(error, "activity") };
          }
        }
        let resolved: ReturnType<typeof decideEmailSearch>;
        try {
          const search = await deps.upstream.searchEmail(input.cookie, {
            query: intent.question,
            limit: LIMITS.maxEmailResults,
          });
          const outcome = parseEmailSearchOutcome(search);
          // A unique complete hit reads its current content from the owning
          // service before anything is shown as the message itself.
          const content = await emailContentFor({
            cookie: input.cookie,
            target: emailContentTarget(outcome),
          });
          resolved = decideEmailSearch(outcome, { now: now(), content });
        } catch (error) {
          if (isAssistantError(error) && error.code === "unauthorized")
            throw error;
          // A source outage or an unverifiable payload is a search failure with
          // its own reason. It is never replaced by SPEC-002's answer.
          return { plan, ...planFailure(error, "search") };
        }
        return {
          plan,
          state: resolved.state,
          decision: resolved.decision,
          blocks: resolved.blocks,
        };
      } catch (error) {
        if (isAssistantError(error) && error.code === "unauthorized")
          throw error;
        return { plan, ...planFailure(error) };
      }
    }

    const one = plan.intents[0];
    if (!one)
      return {
        plan,
        ...planFailure(new AssistantError("invalid_request", "Empty plan")),
      };
    if (plan.intents.length === 1) return resolveOne(one);

    // A mixed consequential/unclear plan cannot trigger even a partial read.
    if (
      plan.intents.some(
        (intent) =>
          intent.requires_clarification || intent.kind === "assistant.clarify",
      )
    ) {
      return {
        plan,
        state: "CLARIFY",
        decision: clarifyDecision("plan.multi_intent"),
        blocks: noticeBlocks(
          "CLARIFY",
          "One part of that request needs clarification. Which read-only part should I handle first?",
        ),
      };
    }
    const labels: Record<PlannedIntent["kind"], string> = {
      "today.read": "Today",
      "email.search": "Email",
      "subscription.search": "Subscription",
      "news.read": "News",
      "weather.read": "Weather",
      "class.next": "Next class",
      "time.now": "Time",
      "action.history": "Activity",
      "assistant.delivery": "Request",
      "assistant.navigate": "Open",
      "assistant.clarify": "Request",
    };
    const parts: {
      intent: PlannedIntent;
      result: Awaited<ReturnType<typeof resolveOne>>;
    }[] = [];
    for (const intent of plan.intents) {
      const current = await scopeFor(input.cookie);
      if (
        current.user_id !== input.scope.user_id ||
        current.workspace_id !== input.scope.workspace_id
      )
        return {
          plan,
          ...planFailure(
            new AssistantError("forbidden", "This account changed."),
          ),
        };
      const result = await resolveOne(intent);
      if (result.state === "WITHHELD")
        return {
          plan,
          ...planFailure(
            new AssistantError(
              "forbidden",
              "This account cannot read those sources.",
            ),
          ),
        };
      parts.push({ intent, result });
    }
    const current = await scopeFor(input.cookie);
    if (
      current.user_id !== input.scope.user_id ||
      current.workspace_id !== input.scope.workspace_id
    )
      return {
        plan,
        ...planFailure(
          new AssistantError("forbidden", "This account changed."),
        ),
      };
    const ready = parts.some(({ result }) => result.state === "READY");
    const state: AssistantResponseState = ready
      ? "READY"
      : parts.some(({ result }) => result.state === "CLARIFY")
        ? "CLARIFY"
        : "UNAVAILABLE";
    const lines = parts.map(({ intent, result }) => {
      const text = result.blocks.find(
        (block) => block.kind === "ANSWER" || block.kind === "NOTICE",
      );
      return `${labels[intent.kind]}: ${clampText(text?.text ?? "No verified answer is available.", 700)}`;
    });
    const combined = clampText(lines.join("\n"), LIMITS.maxAnswerLength);
    const details = parts.flatMap(({ result }) =>
      result.blocks.filter(
        (block) => block.kind !== "ANSWER" && block.kind !== "NOTICE",
      ),
    );
    const caveats: AssistantBlock[] = parts.flatMap(({ intent, result }) => {
      if (result.state === "READY") return [];
      const notice = result.blocks.find((block) => block.kind === "NOTICE");
      const text =
        notice?.text ?? "This part needs clarification or a fresh source.";
      return [
        {
          kind: "NOTICE" as const,
          state: result.state,
          text: `${labels[intent.kind]}: ${clampText(text, 700)}`,
        },
      ];
    });
    if (details.length + caveats.length > 63)
      return {
        plan,
        ...planFailure(
          new AssistantError(
            "unavailable",
            "The combined result exceeded its bounds.",
          ),
        ),
      };
    return {
      plan,
      state,
      decision: parseCapabilityDecision({
        kind: ready
          ? "DELEGATE"
          : state === "CLARIFY"
            ? "CLARIFY"
            : "UNAVAILABLE",
        capability_id: null,
        target: null,
        reason: ready
          ? parts.every(({ result }) => result.state === "READY")
            ? "plan.combined"
            : "plan.partial"
          : "plan.no_complete_answer",
        requires_approval: false,
        action_state: "NONE",
        action_id: null,
        response_state: state,
      }),
      blocks: [
        state === "UNAVAILABLE"
          ? { kind: "NOTICE", state, text: combined }
          : { kind: "ANSWER", text: combined },
        ...details,
        ...caveats,
      ],
    };
  }

  /** A clicked candidate is a pointer, not authority. Recheck both the saved
   * ambiguous turn and the current SPEC-007 result before making it actionable.
   */
  async function selectEmail(input: {
    cookie: string;
    scope: AccountScope;
    sessionId: string;
    request: AssistantMessageRequest;
  }): Promise<{
    plan: IntentPlan;
    state: AssistantResponseState;
    decision: CapabilityDecision;
    blocks: AssistantBlock[];
  }> {
    const { request } = input;
    if (
      request.modality !== "TEXT" ||
      request.text !== "Select an email" ||
      request.referents.length !== 2
    ) {
      throw new AssistantError("invalid_request", "Select one listed email.");
    }
    const [sourceTurnId, resourceId] = request.referents;
    const sourceId = assertUuid(sourceTurnId, "source turn ID");
    const selectedId = assertUuid(resourceId, "email resource ID");
    const turns = await deps.store.listTurns({
      session_id: input.sessionId,
      scope: input.scope,
    });
    const source = turns.find((turn) => turn.id === sourceId);
    const items = source?.presentation.blocks.filter(
      (block) => block.kind === "ITEM",
    );
    if (
      source?.state !== "CLARIFY" ||
      source.decision.capability_id !== "email.search" ||
      source.decision.reason !== "email.search.ambiguous" ||
      source.plan.intents.length !== 1 ||
      source.plan.intents[0]?.kind !== "email.search" ||
      !items ||
      items.length < 2 ||
      items.filter(
        (block) =>
          block.item.id === selectedId &&
          block.item.type === "EMAIL" &&
          block.item.sources.some(
            (citation) =>
              citation.evidence_id === selectedId &&
              citation.source_type === "EMAIL",
          ),
      ).length !== 1
    ) {
      throw new AssistantError(
        "forbidden",
        "That email was not an exact option in this conversation.",
      );
    }
    const current = await scopeFor(input.cookie);
    if (
      current.user_id !== input.scope.user_id ||
      current.workspace_id !== input.scope.workspace_id
    ) {
      throw new AssistantError("forbidden", "This account changed.");
    }
    const search = await deps.upstream.searchEmail(input.cookie, {
      query: source.question,
      limit: LIMITS.maxEmailResults,
    });
    const outcome = parseEmailSearchOutcome(search);
    const content = await emailContentFor({
      cookie: input.cookie,
      target: selectedEmailTarget(outcome, selectedId),
    });
    const resolved = decideEmailSelection(outcome, selectedId, {
      now: now(),
      content,
    });
    return {
      plan: source.plan,
      state: resolved.state,
      decision: resolved.decision,
      blocks: resolved.blocks,
    };
  }

  const emailActions = createAssistantEmailActions({ ...deps, goals });

  return {
    ...emailActions,
    async readEmailDraftSpeech(input) {
      return emailActions.readEmailDraftSpeech(input);
    },
    async readEmailEvidence(input) {
      const scope = await scopeFor(input.cookie);
      const sessionId = assertUuid(input.session_id, "session ID");
      // Ownership is re-derived from the cookie before any saved selector is
      // trusted, so another account cannot read this conversation's evidence.
      await loadSession(sessionId, scope);
      const turnId = assertUuid(input.turn_id, "turn ID");
      const itemId = assertUuid(input.item_id, "item ID");
      const turns = await deps.store.listTurns({
        session_id: sessionId,
        scope,
      });
      const turn = turns.find((candidate) => candidate.id === turnId);
      if (!turn) {
        throw new AssistantError(
          "not_found",
          "That answer is no longer in this conversation.",
        );
      }
      const evidence = turn.presentation.blocks.filter(
        (block): block is Extract<AssistantBlock, { kind: "EVIDENCE" }> =>
          block.kind === "EVIDENCE" && block.evidence_id === itemId,
      );
      const block = evidence[0];
      if (
        evidence.length !== 1 ||
        !block ||
        block.source_type !== "EMAIL" ||
        block.source_version === null
      ) {
        throw new AssistantError(
          "not_found",
          "That source is not part of this answer. Nothing was changed.",
        );
      }
      const content = await emailContentFor({
        cookie: input.cookie,
        target: {
          resource_id: itemId,
          source_type: "EMAIL",
          source_version: block.source_version,
          fresh_until: block.fresh_until,
        },
      });
      if (content.state === "READY") {
        return {
          evidence_id: itemId,
          source_type: "EMAIL",
          excerpts: content.excerpts,
        };
      }
      throw evidenceUnavailable(
        content.state === "UNAVAILABLE" ? content.reason : "unverified",
      );
    },
    async resolveNavigationTarget(input) {
      const scope = await scopeFor(input.cookie);
      const sessionId = assertUuid(input.session_id, "session ID");
      // Ownership is re-derived from the cookie and the session row before any
      // saved item is trusted; a foreign or expired session fails closed.
      await loadSession(sessionId, scope);
      const turnId = assertUuid(input.turn_id, "turn ID");
      const itemId = assertUuid(input.item_id, "item ID");
      const turns = await deps.store.listTurns({
        session_id: sessionId,
        scope,
      });
      const turn = turns.find((candidate) => candidate.id === turnId);
      if (!turn) {
        throw new AssistantError(
          "not_found",
          "That item is no longer in this conversation.",
        );
      }
      const matches = navigationTargets(turn.presentation.blocks).filter(
        (target) => target.item_id === itemId,
      );
      if (matches.length !== 1) {
        throw new AssistantError(
          "not_found",
          "That item is no longer available to open.",
        );
      }
      const target = matches[0] as ReturnType<typeof navigationTargets>[number];
      if (target.source_type === "CLASS_MEETING") {
        if (!target.connection_id) {
          throw new AssistantError(
            "not_found",
            "That class link is no longer available.",
          );
        }
        return parseClassNavigationTarget(
          await deps.upstream.getClassNavigationTarget(input.cookie, {
            connectionId: target.connection_id,
            resourceId: target.item_id,
          }),
        );
      }
      return parseResourceNavigation(
        await deps.upstream.getResourceDetail(input.cookie, target.item_id),
        {
          resource_id: target.item_id,
          source_type: target.source_type as NavigationSourceType,
        },
      );
    },
    async createSession(input) {
      const scope = await scopeFor(input.cookie);
      const navoxSessionId = await deps.upstream.createAssistantSession(
        input.cookie,
      );
      const window = retentionWindow(now());
      const record = await deps.store.createSession({
        id: newId(),
        navox_session_id: navoxSessionId,
        scope,
        created_at: window.createdAt,
        expires_at: window.expiresAt,
      });
      // Retention is enforced opportunistically; a failure here never blocks a session.
      try {
        await deps.store.purgeExpired(window.createdAt, LIMITS.purgeBatchSize);
      } catch {
        // Ignore: the next successful call retries the same bounded delete.
      }
      return sessionView(record, [], now());
    },

    async readSession(input) {
      const scope = await scopeFor(input.cookie);
      const sessionId = assertUuid(input.session_id, "session ID");
      const record = await loadSession(sessionId, scope);
      const turns = await deps.store.listTurns({
        session_id: sessionId,
        scope,
      });
      return sessionView(record, turns.map(toTurnView), now());
    },

    async submitTurn(input) {
      assertRegistryIntegrity();
      const request = parseAssistantMessageRequest(input.body);
      const sessionId = assertUuid(input.session_id, "session ID");
      const scope = await scopeFor(input.cookie);
      const at = now();
      const record = await loadSession(sessionId, scope);

      const fingerprint = fingerprintTurn({
        text: request.text,
        modality: request.modality,
        timezone: request.timezone,
        referents: request.referents,
      });

      // An exact retry returns the saved response without touching an upstream
      // service again; a changed payload behind the same ID is refused.
      const existing = await deps.store.findTurnByRequest({
        session_id: sessionId,
        scope,
        request_id: request.request_id,
      });
      if (existing) {
        return replayOf(existing, fingerprint, sessionId);
      }
      assertTurnBudget(record.next_sequence);

      // Durable claim before the upstream call: a concurrent duplicate replays
      // one result, and a changed payload is refused without a second Today call.
      const held = await acquireClaim({
        sessionId,
        scope,
        request,
        fingerprint,
        at,
      });
      if (!held.owned) {
        if (!held.claim) {
          throw new AssistantError(
            "unavailable",
            "The assistant could not reserve that question. Please try again.",
            { retryable: true },
          );
        }
        if (held.claim.request_fingerprint !== fingerprint) {
          throw new AssistantError(
            "conflict",
            "That request ID was already used with a different question.",
          );
        }
        const saved = await awaitClaimedTurn({
          sessionId,
          scope,
          requestId: request.request_id,
        });
        if (saved) return replayOf(saved, fingerprint, sessionId);
        throw new AssistantError(
          "unavailable",
          "That question is still being answered. Please try again shortly.",
          { retryable: true },
        );
      }

      /**
       * A delivery cue is classified before any planning. A cue-only utterance
       * is answered from this same session's saved turns and never reaches the
       * planner or a source; an embedded cue leaves general routing untouched.
       */
      const delivery = resolveDeliveryIntent(request.text);
      /**
       * The plan of record if the turn never reaches its owning service. It is
       * built before any store call, so a failure path can never throw a second
       * time while it is converting an error into a qualified turn.
       */
      const fallbackPlan = delivery.cue_only
        ? deliveryPlan(request.text)
        : planTurn({ text: request.text });
      let plan: IntentPlan;
      let state: AssistantResponseState;
      let decision: CapabilityDecision;
      let blocks: AssistantBlock[];
      try {
        if (delivery.cue_only) {
          // Reading the saved conversation is a store call like any other, so
          // it shares the routing branch's failure handling: the catch below
          // turns a failure into a qualified turn, which resolves the durable
          // claim through the normal insert instead of leaving it to expire.
          const turns = await deps.store.listTurns({
            session_id: sessionId,
            scope,
          });
          ({ plan, state, decision, blocks } = deliveryTurnOutcome({
            text: request.text,
            delivery,
            turns,
          }));
        } else {
          plan = fallbackPlan;
          if (request.referents.length > 0) {
            const selected = await selectEmail({
              cookie: input.cookie,
              scope,
              sessionId,
              request,
            });
            plan = selected.plan;
            state = selected.state;
            decision = selected.decision;
            blocks = selected.blocks;
          } else {
            const turns = await deps.store.listTurns({
              session_id: sessionId,
              scope,
            });
            const planned = await planFreeformTurn({
              cookie: input.cookie,
              scope,
              record,
              request,
              turns,
            });
            plan = planned.plan;
            state = planned.state;
            decision = planned.decision;
            blocks = planned.blocks;
          }
        }
      } catch (error) {
        const failure = isAssistantError(error)
          ? error
          : toAssistantError(error);
        if (failure.code === "unauthorized") {
          await releaseClaim(sessionId, scope, request.request_id);
          throw failure;
        }
        const refused =
          request.referents.length > 0 && !delivery.cue_only
            ? planFailure(error, "search")
            : planFailure(error);
        plan = fallbackPlan;
        state = refused.state;
        decision = refused.decision;
        blocks = refused.blocks;
      }

      const presentation = buildPresentationPlan({
        decision,
        blocks,
        modality: request.modality,
        delivery: delivery.intent,
      });

      const answer = blocks.find((block) => block.kind === "ANSWER");
      let sequence: number;
      try {
        sequence = await deps.store.reserveSequence({
          session_id: sessionId,
          scope,
          now: at.toISOString(),
        });
      } catch (error) {
        await releaseClaim(sessionId, scope, request.request_id);
        throw error;
      }

      let turn: AssistantTurnRecord;
      try {
        turn = await deps.store.insertTurn({
          id: newId(),
          session_id: sessionId,
          scope,
          sequence,
          modality: request.modality,
          state,
          request_id: request.request_id,
          request_fingerprint: fingerprint,
          question: request.text,
          response_text:
            answer && answer.kind === "ANSWER" ? answer.text : null,
          plan,
          decision,
          presentation,
          action_refs: [],
          created_at: at.toISOString(),
        });
      } catch (error) {
        // A concurrent duplicate lost the insert race; return the saved winner.
        if (isAssistantError(error) && error.code === "conflict") {
          const saved = await deps.store.findTurnByRequest({
            session_id: sessionId,
            scope,
            request_id: request.request_id,
          });
          if (saved) {
            await resolveClaim(sessionId, scope, request.request_id, saved.id);
            return replayOf(saved, fingerprint, sessionId);
          }
        }
        await releaseClaim(sessionId, scope, request.request_id);
        throw error;
      }

      await resolveClaim(sessionId, scope, request.request_id, turn.id);
      // A bounded goal is derived from the saved turn. The turn is already the
      // operator's answer, so goal bookkeeping never fails it: the goals route
      // reports only goals that were actually recorded.
      try {
        await goals.recordTurnGoal({ scope, session_id: sessionId, turn });
      } catch {
        // A later turn naming the same saved turn retries this bounded write.
      }
      return { session_id: sessionId, turn: toTurnView(turn), replay: false };
    },

    async deleteSession(input) {
      const scope = await scopeFor(input.cookie);
      const sessionId = assertUuid(input.session_id, "session ID");
      const deleted = await deps.store.deleteSession({
        session_id: sessionId,
        scope,
      });
      if (!deleted) {
        throw new AssistantError(
          "not_found",
          "That assistant session is no longer available.",
        );
      }
    },

    async purgeExpired() {
      return deps.store.purgeExpired(
        now().toISOString(),
        LIMITS.purgeBatchSize,
      );
    },

    async readGoal(input) {
      const scope = await scopeFor(input.cookie);
      const goalId = assertUuid(input.goal_id, "goal ID");
      return goalView(await goals.readGoal({ goal_id: goalId, scope }));
    },

    async listSessionGoals(input) {
      const scope = await scopeFor(input.cookie);
      const sessionId = assertUuid(input.session_id, "session ID");
      // Ownership is re-derived here, so a foreign session is a not-found and
      // never an empty list that implies the session exists.
      await loadSession(sessionId, scope);
      const records = await goals.listGoals({
        session_id: sessionId,
        scope,
      });
      return records.map(goalView);
    },

    async redispatchGoal(input) {
      const scope = await scopeFor(input.cookie);
      const goalId = assertUuid(input.goal_id, "goal ID");
      const result = await goals.redispatch({ goal_id: goalId, scope });
      return { goal: goalView(result.goal), dispatched: result.dispatched };
    },
  };
}
