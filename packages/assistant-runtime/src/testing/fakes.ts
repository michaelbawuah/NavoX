import { AssistantError } from "../errors";
import type { AccountScope, NavoxUpstream } from "../gateway";
import { PLANNER_NO_PROVIDER } from "../gateway";
import { GOAL_DETAIL } from "../goals";
import { LIMITS } from "../limits";
import type {
  AssistantGoalRecord,
  AssistantRequestClaim,
  AssistantScope,
  AssistantSessionRecord,
  AssistantStore,
  AssistantTurnRecord,
} from "../store";
import type { TodayQueryResult } from "../today";

export const SCOPE: AssistantScope = {
  user_id: "11111111-1111-4111-8111-111111111111",
  workspace_id: "22222222-2222-4222-8222-222222222222",
};

export const OTHER_SCOPE: AssistantScope = {
  user_id: "33333333-3333-4333-8333-333333333333",
  workspace_id: "44444444-4444-4444-8444-444444444444",
};

export const NAVOX_SESSION_ID = "55555555-5555-4555-8555-555555555555";
export const SESSION_ID = "66666666-6666-4666-8666-666666666666";
export const REQUEST_ID = "77777777-7777-4777-8777-777777777777";

/** In-memory stand-in for the PostgreSQL store that mirrors its constraints. */
export interface MemoryActionRow {
  user_id: string;
  workspace_id: string;
  status: string;
  executed_at: string | null;
  verified_at: string | null;
}

export interface MemoryStore extends AssistantStore {
  readonly sessions: AssistantSessionRecord[];
  readonly turns: AssistantTurnRecord[];
  readonly claims: Map<string, AssistantRequestClaim>;
  readonly goals: AssistantGoalRecord[];
  /** Seedable SPEC-001/003 action rows the worker-facing read joins against. */
  readonly actions: Map<string, MemoryActionRow>;
}

export function createMemoryStore(): MemoryStore {
  const sessions: AssistantSessionRecord[] = [];
  const turns: AssistantTurnRecord[] = [];
  const claims = new Map<string, AssistantRequestClaim>();
  const goals: AssistantGoalRecord[] = [];
  const actions = new Map<string, MemoryActionRow>();
  const claimKey = (sessionId: string, requestId: string) =>
    `${sessionId}:${requestId}`;

  return {
    sessions,
    turns,
    claims,
    goals,
    actions,

    async createSession(input) {
      if (
        sessions.some(
          (session) => session.navox_session_id === input.navox_session_id,
        )
      ) {
        throw new AssistantError(
          "conflict",
          "That SPEC-005 session is already tracked.",
        );
      }
      const record: AssistantSessionRecord = {
        id: input.id,
        navox_session_id: input.navox_session_id,
        workspace_id: input.scope.workspace_id,
        user_id: input.scope.user_id,
        next_sequence: 1,
        created_at: input.created_at,
        updated_at: input.created_at,
        expires_at: input.expires_at,
      };
      sessions.push(record);
      return { ...record };
    },

    async readSession(input) {
      const found = sessions.find(
        (session) =>
          session.id === input.session_id &&
          session.workspace_id === input.scope.workspace_id &&
          session.user_id === input.scope.user_id,
      );
      return found ? { ...found } : null;
    },

    async listTurns(input) {
      return turns
        .filter(
          (turn) =>
            turn.session_id === input.session_id &&
            sessions.some(
              (session) =>
                session.id === turn.session_id &&
                session.workspace_id === input.scope.workspace_id &&
                session.user_id === input.scope.user_id,
            ),
        )
        .sort((a, b) => a.sequence - b.sequence)
        .map((turn) => ({ ...turn }));
    },

    async findTurnByRequest(input) {
      const session = await this.readSession({
        session_id: input.session_id,
        scope: input.scope,
      });
      if (!session) return null;
      const found = turns.find(
        (turn) =>
          turn.session_id === input.session_id &&
          turn.request_id === input.request_id,
      );
      return found ? { ...found } : null;
    },

    async claimRequest(input) {
      const key = claimKey(input.session_id, input.request_id);
      const existing = claims.get(key);
      if (
        existing &&
        existing.status === "PENDING" &&
        Date.parse(existing.updated_at) <= Date.parse(input.stale_before)
      ) {
        claims.delete(key);
      }
      const current = claims.get(key);
      if (current) return { created: false, claim: { ...current } };
      const claim: AssistantRequestClaim = {
        request_id: input.request_id,
        request_fingerprint: input.request_fingerprint,
        status: "PENDING",
        turn_id: null,
        updated_at: input.now,
      };
      claims.set(key, claim);
      return { created: true, claim: { ...claim } };
    },

    async readClaimRequest(input) {
      const found = claims.get(claimKey(input.session_id, input.request_id));
      return found ? { ...found } : null;
    },

    async resolveClaimRequest(input) {
      const key = claimKey(input.session_id, input.request_id);
      const found = claims.get(key);
      if (found?.status !== "PENDING") return;
      claims.set(key, {
        ...found,
        status: "RESOLVED",
        turn_id: input.turn_id,
        updated_at: input.now,
      });
    },

    async releaseClaimRequest(input) {
      const key = claimKey(input.session_id, input.request_id);
      const found = claims.get(key);
      if (found?.status === "PENDING") claims.delete(key);
    },

    async reserveSequence(input) {
      const session = sessions.find(
        (candidate) =>
          candidate.id === input.session_id &&
          candidate.workspace_id === input.scope.workspace_id &&
          candidate.user_id === input.scope.user_id,
      );
      if (!session || Date.parse(session.expires_at) <= Date.parse(input.now)) {
        throw new AssistantError(
          "not_found",
          "That assistant session is no longer available.",
        );
      }
      if (session.next_sequence > LIMITS.maxTurnsPerSession) {
        throw new AssistantError(
          "unsupported",
          "This conversation reached its turn limit. Start a new one.",
        );
      }
      const sequence = session.next_sequence;
      session.next_sequence += 1;
      session.updated_at = input.now;
      return sequence;
    },

    async insertTurn(input) {
      if (
        turns.some(
          (turn) =>
            turn.session_id === input.session_id &&
            turn.request_id === input.request_id,
        )
      ) {
        throw new AssistantError(
          "conflict",
          "That request ID is already saved.",
        );
      }
      if (
        turns.some(
          (turn) =>
            turn.session_id === input.session_id &&
            turn.sequence === input.sequence,
        )
      ) {
        throw new AssistantError(
          "conflict",
          "That turn position is already saved.",
        );
      }
      const record: AssistantTurnRecord = {
        id: input.id,
        session_id: input.session_id,
        sequence: input.sequence,
        modality: input.modality,
        state: input.state,
        request_id: input.request_id,
        request_fingerprint: input.request_fingerprint,
        question: input.question,
        response_text: input.response_text,
        plan: input.plan,
        decision: input.decision,
        presentation: input.presentation,
        action_refs: input.action_refs,
        created_at: input.created_at,
      };
      turns.push(record);
      return { ...record };
    },

    async deleteSession(input) {
      const index = sessions.findIndex(
        (session) =>
          session.id === input.session_id &&
          session.workspace_id === input.scope.workspace_id &&
          session.user_id === input.scope.user_id,
      );
      if (index < 0) return false;
      sessions.splice(index, 1);
      for (let i = turns.length - 1; i >= 0; i -= 1) {
        if (turns[i]?.session_id === input.session_id) turns.splice(i, 1);
      }
      for (const key of [...claims.keys()]) {
        if (key.startsWith(`${input.session_id}:`)) claims.delete(key);
      }
      return true;
    },

    async purgeExpired(now, limit) {
      let purged = 0;
      for (let i = sessions.length - 1; i >= 0 && purged < limit; i -= 1) {
        const session = sessions[i];
        if (session && Date.parse(session.expires_at) <= Date.parse(now)) {
          sessions.splice(i, 1);
          purged += 1;
        }
      }
      return purged;
    },

    async createGoal(input) {
      // The source must belong to this exact session, workspace and user. The
      // PostgreSQL store enforces the same rule inside its insert statement.
      const session = sessions.find(
        (candidate) =>
          candidate.id === input.session_id &&
          candidate.workspace_id === input.scope.workspace_id &&
          candidate.user_id === input.scope.user_id,
      );
      const sourceTurn =
        input.source_turn_id === null
          ? null
          : (turns.find((turn) => turn.id === input.source_turn_id) ?? null);
      const sourceAction =
        input.action_id === null
          ? null
          : (actions.get(input.action_id) ?? null);
      const owned =
        session !== undefined &&
        (input.source_turn_id !== null
          ? sourceTurn?.session_id === input.session_id
          : sourceAction !== null &&
            sourceAction.user_id === input.scope.user_id &&
            sourceAction.workspace_id === input.scope.workspace_id);
      if (!owned)
        throw new AssistantError(
          "forbidden",
          "That goal source is not available for this account.",
        );
      const existing = goals.find((goal) =>
        input.source_turn_id !== null
          ? goal.source_turn_id === input.source_turn_id &&
            goal.kind === input.kind
          : goal.action_id === input.action_id,
      );
      if (existing) {
        if (
          existing.session_id !== input.session_id ||
          existing.workspace_id !== input.scope.workspace_id ||
          existing.user_id !== input.scope.user_id
        )
          throw new AssistantError(
            "forbidden",
            "This account cannot use that goal.",
          );
        return { created: false, goal: { ...existing } };
      }
      const record: AssistantGoalRecord = {
        id: input.id,
        session_id: input.session_id,
        workspace_id: input.scope.workspace_id,
        user_id: input.scope.user_id,
        kind: input.kind,
        status: "PENDING",
        dispatch_state: "NOT_DISPATCHED",
        source_turn_id: input.source_turn_id,
        action_id: input.action_id,
        detail: GOAL_DETAIL.pendingDispatch,
        attempts: 0,
        verify_attempts: 0,
        created_at: input.created_at,
        updated_at: input.created_at,
        completed_at: null,
      };
      goals.push(record);
      return { created: true, goal: { ...record } };
    },

    async readGoal(input) {
      const found = goals.find(
        (goal) =>
          goal.id === input.goal_id &&
          goal.workspace_id === input.scope.workspace_id &&
          goal.user_id === input.scope.user_id,
      );
      return found ? { ...found } : null;
    },

    async listGoals(input) {
      return goals
        .filter(
          (goal) =>
            goal.session_id === input.session_id &&
            goal.workspace_id === input.scope.workspace_id &&
            goal.user_id === input.scope.user_id,
        )
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .slice(0, LIMITS.maxTurnsPerSession)
        .map((goal) => ({ ...goal }));
    },

    async readGoalSourceCheck(input) {
      const found = goals.find((goal) => goal.id === input.goal_id);
      if (!found) return null;
      const turn =
        found.source_turn_id === null
          ? null
          : (turns.find((candidate) => candidate.id === found.source_turn_id) ??
            null);
      const session = sessions.find(
        (candidate) => candidate.id === found.session_id,
      );
      const action =
        found.action_id === null
          ? null
          : (actions.get(found.action_id) ?? null);
      const turnOwned =
        turn !== null &&
        session !== undefined &&
        turn.session_id === found.session_id &&
        session.workspace_id === found.workspace_id &&
        session.user_id === found.user_id;
      const actionOwned =
        action !== null &&
        action.user_id === found.user_id &&
        action.workspace_id === found.workspace_id;
      return {
        goal: { ...found },
        source_owned: found.source_turn_id !== null ? turnOwned : actionOwned,
        turn: turn
          ? {
              state: turn.state,
              plan: turn.plan,
              decision: turn.decision,
              presentation: turn.presentation,
            }
          : null,
        action: action
          ? {
              status: action.status,
              executed_at: action.executed_at,
              verified_at: action.verified_at,
            }
          : null,
      };
    },

    async markGoalDispatched(input) {
      const found = goals.find(
        (goal) =>
          goal.id === input.goal_id &&
          goal.workspace_id === input.scope.workspace_id &&
          goal.user_id === input.scope.user_id &&
          goal.status !== "COMPLETED",
      );
      if (!found) return null;
      found.dispatch_state = "DISPATCHED";
      found.detail = GOAL_DETAIL.dispatched;
      found.updated_at = input.now;
      return { ...found };
    },

    async markGoalDispatchFailed(input) {
      const found = goals.find(
        (goal) =>
          goal.id === input.goal_id &&
          goal.workspace_id === input.scope.workspace_id &&
          goal.user_id === input.scope.user_id &&
          goal.status !== "COMPLETED",
      );
      if (!found) return null;
      found.dispatch_state = "DISPATCH_FAILED";
      found.detail = input.detail;
      found.updated_at = input.now;
      return { ...found };
    },

    async claimGoalDispatchAttempt(input) {
      // A single synchronous check-and-increment, mirroring the atomic UPDATE.
      const found = goals.find(
        (goal) =>
          goal.id === input.goal_id &&
          goal.workspace_id === input.scope.workspace_id &&
          goal.user_id === input.scope.user_id &&
          goal.status !== "COMPLETED" &&
          goal.attempts < input.max_attempts,
      );
      if (!found) return null;
      found.attempts += 1;
      found.updated_at = input.now;
      return { ...found };
    },

    async applyGoalVerification(input) {
      const found = goals.find(
        (goal) => goal.id === input.goal_id && goal.status !== "COMPLETED",
      );
      if (!found) return null;
      found.status = input.status;
      found.detail = input.detail;
      found.completed_at = input.completed_at;
      found.updated_at = input.now;
      found.verify_attempts = Math.min(found.verify_attempts + 1, 64);
      return { ...found };
    },
  };
}

export interface FakeUpstream extends NavoxUpstream {
  readonly calls: {
    account: string[];
    sessions: string[];
    today: { query: string; timezone: string | null }[];
    plan: {
      utterance: string;
      recentReferences: string[];
      sessionId: string | null;
    }[];
    email: { query: string; limit: number | undefined }[];
    subscriptions: { cookie: string; selector: string }[];
    cancellation: { cookie: string; id: string }[];
    trendingNews: string[];
    newsStory: { cookie: string; id: string }[];
    newsSummary: { cookie: string; id: string }[];
    weather: string[];
    classSources: string[];
    actions: { cookie: string; limit: number }[];
    meeting: string[];
  };
  account: AccountScope;
  today: TodayQueryResult | (() => Promise<TodayQueryResult>);
  todayError: AssistantError | null;
  plan: unknown | (() => Promise<unknown>);
  planError: AssistantError | null;
  email: unknown | (() => Promise<unknown>);
  emailError: AssistantError | null;
  subscriptions: unknown | (() => Promise<unknown>);
  subscriptionsError: AssistantError | null;
  cancellation: unknown | (() => Promise<unknown>);
  cancellationError: AssistantError | null;
  trendingNews: unknown | (() => Promise<unknown>);
  trendingNewsError: AssistantError | null;
  newsStory: unknown | (() => Promise<unknown>);
  newsStoryError: AssistantError | null;
  newsSummary: unknown | (() => Promise<unknown>);
  newsSummaryError: AssistantError | null;
  weather: unknown | (() => Promise<unknown>);
  weatherError: AssistantError | null;
  classSources: unknown | (() => Promise<unknown>);
  classSourcesError: AssistantError | null;
  actions: unknown | (() => Promise<unknown>);
  actionsError: AssistantError | null;
  meeting: Awaited<ReturnType<NavoxUpstream["getMeetingPrep"]>>;
  meetingError: AssistantError | null;
}

/**
 * The default plan mirrors the default-off deployment: no qualified planner is
 * configured, so an unsupported Today question keeps SPEC-002's own answer.
 */
export const UNCONFIGURED_PLANNER: AssistantError = new AssistantError(
  "unavailable",
  "No qualified AI provider is available for this request",
  { reason: PLANNER_NO_PROVIDER },
);

export const CLARIFY_PLAN = {
  version: 1,
  intents: [
    {
      route: "assistant.clarify",
      entity: { kind: "NONE", value: null, confidence: 1 },
      time: { kind: "NONE", expression: null, confidence: 1 },
      reference: { kind: "NONE", ordinal: null, turn_id: null },
      confidence: 1,
      requires_clarification: true,
      clarification: "I need a little more detail before I can look that up.",
    },
  ],
};

/** The SPEC-005 response envelope the real `/ai/assistant/intents` returns. */
export function intentEnvelope(
  plan: unknown,
  overrides?: Record<string, unknown>,
) {
  return {
    task_id: "aaaaaaaa-0000-4000-8000-000000000001",
    trace_id: "aaaaaaaa-0000-4000-8000-000000000002",
    plan,
    session_id: NAVOX_SESSION_ID,
    turn_sequence: 1,
    actions_executed: false,
    ...overrides,
  };
}

export interface FakeEmailResultInput {
  resource_id: string;
  source_type?: "EMAIL" | "EMAIL_THREAD";
  title?: string | null;
  source_version?: string | null;
  source_updated_at?: string | null;
  fresh_until?: string | null;
  connection_id?: string | null;
  external_resource_id?: string | null;
  excerpt_count?: number;
}

/** One SPEC-007-shaped search response. Excerpt text is never copied here. */
export function emailSearchPayload(
  results: readonly FakeEmailResultInput[],
  overrides?: {
    truncated?: boolean;
    unavailable_modes?: string[];
    source_issues?: unknown[];
    partial_reasons?: string[];
    suggested_followups?: string[];
    examined?: number;
  },
) {
  return {
    interpreted_mode: "SEARCH",
    intent: "FIND_RESOURCE",
    results: results.map((result) => ({
      source_type: result.source_type ?? "EMAIL",
      resource_id: result.resource_id,
      title: result.title === undefined ? "Renewal notice" : result.title,
      excerpts: Array.from(
        { length: result.excerpt_count ?? 1 },
        (_, index) => ({
          text: "excerpt text that must never be copied",
          start: index,
          end: index + 1,
        }),
      ),
      canonical_url: null,
      source_updated_at: result.source_updated_at ?? "2026-09-29T12:00:00.000Z",
      source_version:
        result.source_version === undefined ? "v1" : result.source_version,
      fresh_until:
        result.fresh_until === undefined
          ? "2026-10-01T12:00:00.000Z"
          : result.fresh_until,
      indexed_at: "2026-09-29T12:05:00.000Z",
      provenance: {
        connection_id:
          result.connection_id ?? "88888888-8888-4888-8888-888888888888",
        external_resource_id: result.external_resource_id ?? "mail-1",
      },
      origin: "CONNECTED",
    })),
    coverage: {
      candidate_bound: 200,
      examined: overrides?.examined ?? results.length,
      returned: results.length,
      truncated: overrides?.truncated ?? false,
      source_issues: overrides?.source_issues ?? [],
      partial_reasons: overrides?.partial_reasons ?? [],
    },
    unavailable_modes: overrides?.unavailable_modes ?? [],
    suggested_followups: overrides?.suggested_followups ?? [],
  };
}

export function createFakeUpstream(
  overrides?: Partial<FakeUpstream>,
): FakeUpstream {
  const upstream: FakeUpstream = {
    base_url: "https://navox.example/api/v1",
    calls: {
      account: [],
      sessions: [],
      today: [],
      plan: [],
      email: [],
      subscriptions: [],
      cancellation: [],
      trendingNews: [],
      newsStory: [],
      newsSummary: [],
      weather: [],
      classSources: [],
      actions: [],
      meeting: [],
    },
    account: {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      email: "operator@example.com",
    },
    today: {
      intent: "today",
      answer: "",
      items: [],
      supported_queries: [],
      details: [],
    },
    todayError: null,
    plan: intentEnvelope(CLARIFY_PLAN),
    planError: UNCONFIGURED_PLANNER,
    email: emailSearchPayload([]),
    emailError: null,
    subscriptions: { intent: "SEARCH", subscriptions: [] },
    subscriptionsError: null,
    cancellation: null,
    cancellationError: null,
    trendingNews: [],
    trendingNewsError: null,
    newsStory: null,
    newsStoryError: null,
    newsSummary: null,
    newsSummaryError: null,
    weather: { status: "disabled", unit: "celsius", temperature: null },
    weatherError: null,
    classSources: { complete: true, courses: [], events: [] },
    classSourcesError: null,
    actions: [],
    actionsError: null,
    meeting: null,
    meetingError: null,
    async fetchAccount(cookie) {
      upstream.calls.account.push(cookie);
      return upstream.account;
    },
    async createAssistantSession(cookie) {
      upstream.calls.sessions.push(cookie);
      return NAVOX_SESSION_ID;
    },
    async queryToday(cookie, input) {
      upstream.calls.account.push(cookie);
      upstream.calls.today.push(input);
      if (upstream.todayError) throw upstream.todayError;
      const result = upstream.today;
      return typeof result === "function" ? await result() : result;
    },
    async planIntents(cookie, input) {
      upstream.calls.account.push(cookie);
      upstream.calls.plan.push({
        utterance: input.utterance,
        recentReferences: [...input.recentReferences],
        sessionId: input.sessionId,
      });
      if (upstream.planError) throw upstream.planError;
      const plan = upstream.plan;
      return typeof plan === "function" ? await plan() : plan;
    },
    async searchEmail(cookie, input) {
      upstream.calls.account.push(cookie);
      upstream.calls.email.push({ query: input.query, limit: input.limit });
      if (upstream.emailError) throw upstream.emailError;
      const email = upstream.email;
      return typeof email === "function" ? await email() : email;
    },
    async querySubscriptions(cookie, selector) {
      upstream.calls.subscriptions.push({ cookie, selector });
      if (upstream.subscriptionsError) throw upstream.subscriptionsError;
      const result = upstream.subscriptions;
      return typeof result === "function" ? await result() : result;
    },
    async getSubscriptionCancellation(cookie, id) {
      upstream.calls.cancellation.push({ cookie, id });
      if (upstream.cancellationError) throw upstream.cancellationError;
      const result = upstream.cancellation;
      return typeof result === "function" ? await result() : result;
    },
    async getTrendingNews(cookie) {
      upstream.calls.trendingNews.push(cookie);
      if (upstream.trendingNewsError) throw upstream.trendingNewsError;
      const result = upstream.trendingNews;
      return typeof result === "function" ? await result() : result;
    },
    async getNewsStory(cookie, id) {
      upstream.calls.newsStory.push({ cookie, id });
      if (upstream.newsStoryError) throw upstream.newsStoryError;
      const result = upstream.newsStory;
      return typeof result === "function" ? await result() : result;
    },
    async getNewsSummary(cookie, id) {
      upstream.calls.newsSummary.push({ cookie, id });
      if (upstream.newsSummaryError) throw upstream.newsSummaryError;
      const result = upstream.newsSummary;
      return typeof result === "function" ? await result() : result;
    },
    async getWeather(cookie) {
      upstream.calls.weather.push(cookie);
      if (upstream.weatherError) throw upstream.weatherError;
      const result = upstream.weather;
      return typeof result === "function" ? await result() : result;
    },
    async getClassSources(cookie) {
      upstream.calls.classSources.push(cookie);
      if (upstream.classSourcesError) throw upstream.classSourcesError;
      const result = upstream.classSources;
      return typeof result === "function" ? await result() : result;
    },
    async listActions(cookie, input) {
      upstream.calls.actions.push({ cookie, limit: input.limit });
      if (upstream.actionsError) throw upstream.actionsError;
      const result = upstream.actions;
      return typeof result === "function" ? await result() : result;
    },
    async getMeetingPrep(cookie) {
      upstream.calls.meeting.push(cookie);
      if (upstream.meetingError) throw upstream.meetingError;
      return upstream.meeting;
    },
    async createKnowledgeEmailDraft() {
      throw new AssistantError(
        "unsupported",
        "No fake email draft configured.",
      );
    },
    async getCommunicationDraft() {
      throw new AssistantError(
        "unsupported",
        "No fake email draft configured.",
      );
    },
    async reviseCommunicationDraft() {
      throw new AssistantError(
        "unsupported",
        "No fake email draft configured.",
      );
    },
    async prepareCommunicationDraft() {
      throw new AssistantError(
        "unsupported",
        "No fake email action configured.",
      );
    },
    async approveCommunicationDraft() {
      throw new AssistantError(
        "unsupported",
        "No fake email action configured.",
      );
    },
    async getAction() {
      throw new AssistantError(
        "unsupported",
        "No fake email action configured.",
      );
    },
    ...overrides,
  };
  return upstream;
}
