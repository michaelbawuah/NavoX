import type { AssistantMeetingBriefing } from "@navox/contracts";
import {
  CONVERSATION_OUTPUT_LIMIT,
  CONVERSATION_OUTPUT_LIMIT_MESSAGE,
  type ConversationReference,
} from "./conversation";
import { AssistantError, isAssistantError, toAssistantError } from "./errors";
import { LIMITS } from "./limits";
import { parseMeetingPrep } from "./meeting";
import { parseTodayQueryResult, type TodayQueryResult } from "./today";
import { assertUuid, isRecord } from "./validate";

export type FetchLike = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

export interface AccountScope {
  user_id: string;
  workspace_id: string;
  email: string | null;
}

/** Server-configured NavoX API adapters. The base URL is never client input. */
export interface NavoxUpstream {
  readonly base_url: string;
  fetchAccount(cookie: string): Promise<AccountScope>;
  createAssistantSession(cookie: string): Promise<string>;
  queryToday(
    cookie: string,
    input: { query: string; timezone: string | null },
  ): Promise<TodayQueryResult>;
  getMeetingPrep(cookie: string): Promise<AssistantMeetingBriefing | null>;
  /**
   * Raw SPEC-005 plan. It stays `unknown` on purpose: the runtime validates and
   * binds it before anything may use it.
   */
  planIntents(
    cookie: string,
    input: {
      utterance: string;
      recentReferences: readonly string[];
      sessionId: string | null;
    },
  ): Promise<unknown>;
  /** Text-only model response. The runtime validates its session and authority. */
  answerConversation(
    cookie: string,
    input: {
      utterance: string;
      recentTurns: readonly ConversationReference[];
      sessionId: string;
    },
  ): Promise<unknown>;
  /**
   * Raw read-only SPEC-007 connected search. Validated by the runtime before
   * any result becomes a citation or an item.
   */
  searchEmail(
    cookie: string,
    input: { query: string; limit?: number },
  ): Promise<unknown>;
  /**
   * Raw SPEC-007 resource detail for one exact selector. The owning service
   * re-checks workspace, exclusions and the stored revision, so this is the
   * fresh current-source read a content or navigation answer is built from.
   * The runtime validates every field before any excerpt or URL is used.
   */
  getResourceDetail(cookie: string, resourceId: string): Promise<unknown>;
  /**
   * Raw SPEC-007 class-navigation target for one exact selector. The owning
   * service re-checks the connection owner and returns only a verified URL.
   */
  getClassNavigationTarget(
    cookie: string,
    input: { connectionId: string; resourceId: string },
  ): Promise<unknown>;
  querySubscriptions(cookie: string, selector: string | null): Promise<unknown>;
  getSubscriptionCancellation(cookie: string, id: string): Promise<unknown>;
  getTrendingNews(cookie: string, region?: "world" | "us"): Promise<unknown>;
  getNewsStory(cookie: string, id: string): Promise<unknown>;
  getNewsSummary(cookie: string, id: string): Promise<unknown>;
  getWeather(cookie: string): Promise<unknown>;
  getClassSources(cookie: string): Promise<unknown>;
  /**
   * Raw read-only list from the existing authenticated SPEC-001/003 action
   * ledger. The route is already scoped by workspace and user; this runtime
   * binds only the minimal facts and never copies an action payload.
   */
  listActions(cookie: string, input: { limit: number }): Promise<unknown>;
  createKnowledgeEmailDraft(
    cookie: string,
    input: { sourceId: string; instructions: string },
  ): Promise<unknown>;
  getCommunicationDraft(cookie: string, draftId: string): Promise<unknown>;
  reviseCommunicationDraft(
    cookie: string,
    draftId: string,
    input: {
      expectedVersion: number;
      to: string;
      subject: string;
      body: string;
    },
  ): Promise<unknown>;
  prepareCommunicationDraft(
    cookie: string,
    draftId: string,
    input: { expectedVersion: number; requestId: string },
  ): Promise<unknown>;
  approveCommunicationDraft(
    cookie: string,
    draftId: string,
    input: {
      requestId: string;
      expectedPayloadHash: string;
      draftVersion: number;
    },
  ): Promise<unknown>;
  getAction(cookie: string, actionId: string): Promise<unknown>;
}

export interface NavoxUpstreamConfig {
  baseUrl: string;
  fetchImpl?: FetchLike;
  timeoutMs?: number;
}

const SIGNED_OUT = "Sign in to use the assistant.";

/**
 * The SPEC-005 intent route has exactly one 503 meaning: no qualified AI
 * provider is available for this request (provider policy unconfigured, or no
 * eligible model produced a validated plan). That is the only condition this
 * runtime may answer from SPEC-002's own classification instead.
 */
export const PLANNER_NO_PROVIDER = "planner_no_provider";

function statusError(status: number, path: string): AssistantError {
  if (status === 401) return new AssistantError("unauthorized", SIGNED_OUT);
  if (status === 403) {
    return new AssistantError(
      "forbidden",
      "This account cannot use that assistant capability.",
    );
  }
  if (status === 404)
    return new AssistantError("not_found", "That assistant resource is gone.");
  if (status === 409)
    return new AssistantError(
      "conflict",
      "That draft or approval changed. Reload it.",
    );
  if (status === 422)
    return new AssistantError("invalid_request", "That request was not valid.");
  if (status === 429) {
    return new AssistantError(
      "unavailable",
      "NavoX is busy right now. Please retry shortly.",
    );
  }
  return new AssistantError(
    "unavailable",
    `The NavoX API did not answer ${path} correctly.`,
  );
}

/**
 * The planner is a bounded internal service. Its own transport and validation
 * failures are qualified unavailability, never a reason to guess a route.
 */
function plannerStatusError(status: number): AssistantError {
  if (status === 401) return new AssistantError("unauthorized", SIGNED_OUT);
  if (status === 403) {
    return new AssistantError(
      "forbidden",
      "This account cannot use that assistant capability.",
    );
  }
  if (status === 409) {
    return new AssistantError(
      "conflict",
      "That conversation changed. Please ask again.",
    );
  }
  if (status === 503) {
    return new AssistantError(
      "unavailable",
      "The assistant could not plan that request right now.",
      { retryable: true, reason: PLANNER_NO_PROVIDER },
    );
  }
  return new AssistantError(
    "unavailable",
    "The assistant could not plan that request right now.",
    { retryable: true },
  );
}

function searchStatusError(status: number): AssistantError {
  if (status === 401) return new AssistantError("unauthorized", SIGNED_OUT);
  if (status === 403) {
    return new AssistantError(
      "forbidden",
      "This account cannot read those connected sources.",
    );
  }
  if (status === 404) {
    return new AssistantError(
      "unsupported",
      "Connected email search is not enabled in this deployment.",
    );
  }
  return new AssistantError(
    "unavailable",
    "Connected email search is not reachable right now.",
    { retryable: true },
  );
}

function parseAccount(payload: unknown): AccountScope {
  if (!isRecord(payload)) {
    throw new AssistantError(
      "unavailable",
      "The NavoX API returned an unreadable account.",
    );
  }
  const workspace = payload.workspace;
  if (!isRecord(workspace)) {
    throw new AssistantError(
      "unavailable",
      "The NavoX API returned an unreadable account.",
    );
  }
  try {
    return {
      user_id: assertUuid(payload.id, "account ID"),
      workspace_id: assertUuid(workspace.id, "workspace ID"),
      email: typeof payload.email === "string" ? payload.email : null,
    };
  } catch (error) {
    if (isAssistantError(error) && error.code === "unavailable") throw error;
    throw new AssistantError(
      "unavailable",
      "The NavoX API returned an unreadable account.",
    );
  }
}

export function createNavoxUpstream(
  config: NavoxUpstreamConfig,
): NavoxUpstream {
  const baseUrl = config.baseUrl.replace(/\/+$/, "");
  if (!baseUrl) {
    throw new AssistantError(
      "misconfigured",
      "The NavoX API base URL is not configured.",
    );
  }
  const fetchImpl = config.fetchImpl ?? ((input, init) => fetch(input, init));
  const timeoutMs = config.timeoutMs ?? LIMITS.requestTimeoutMs;

  async function send(
    path: string,
    input: { method: "GET" | "POST" | "PATCH"; cookie: string; body?: unknown },
  ): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const headers: Record<string, string> = {};
    if (input.cookie) headers.cookie = input.cookie;
    if (input.body !== undefined) headers["content-type"] = "application/json";
    try {
      return await fetchImpl(`${baseUrl}${path}`, {
        method: input.method,
        headers,
        body: input.body === undefined ? undefined : JSON.stringify(input.body),
        cache: "no-store",
        signal: controller.signal,
      });
    } catch {
      throw new AssistantError(
        "unavailable",
        "The NavoX API is not reachable right now.",
      );
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    base_url: baseUrl,

    async fetchAccount(cookie: string): Promise<AccountScope> {
      if (!cookie) throw new AssistantError("unauthorized", SIGNED_OUT);
      const response = await send("/auth/me", { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, "/auth/me");
      try {
        return parseAccount(await response.json());
      } catch (error) {
        throw toAssistantError(error);
      }
    },

    async createAssistantSession(cookie: string): Promise<string> {
      const response = await send("/ai/sessions", { method: "POST", cookie });
      if (!response.ok) throw statusError(response.status, "/ai/sessions");
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        throw new AssistantError(
          "unavailable",
          "The NavoX API did not return a session.",
        );
      }
      if (!isRecord(payload)) {
        throw new AssistantError(
          "unavailable",
          "The NavoX API did not return a session.",
        );
      }
      try {
        return assertUuid(payload.id, "assistant session ID");
      } catch {
        throw new AssistantError(
          "unavailable",
          "The NavoX API did not return a session.",
        );
      }
    },

    async queryToday(
      cookie: string,
      input: { query: string; timezone: string | null },
    ): Promise<TodayQueryResult> {
      const response = await send("/today/query", {
        method: "POST",
        cookie,
        body: { query: input.query, timezone: input.timezone },
      });
      if (!response.ok) throw statusError(response.status, "/today/query");
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        throw new AssistantError(
          "unavailable",
          "Today did not return a readable answer.",
        );
      }
      return parseTodayQueryResult(payload);
    },

    async getMeetingPrep(
      cookie: string,
    ): Promise<AssistantMeetingBriefing | null> {
      const path = "/proactive/meeting-prep";
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      try {
        return parseMeetingPrep(await response.json());
      } catch (error) {
        if (isAssistantError(error)) throw error;
        throw new AssistantError(
          "unavailable",
          "Meeting preparation was unreadable.",
        );
      }
    },

    async planIntents(
      cookie: string,
      input: {
        utterance: string;
        recentReferences: readonly string[];
        sessionId: string | null;
      },
    ): Promise<unknown> {
      const response = await send("/ai/assistant/intents", {
        method: "POST",
        cookie,
        body: {
          utterance: input.utterance,
          recent_references: [...input.recentReferences],
          session_id: input.sessionId,
        },
      });
      if (!response.ok) throw plannerStatusError(response.status);
      try {
        return await response.json();
      } catch {
        throw new AssistantError(
          "unavailable",
          "The assistant planner did not return a readable plan.",
        );
      }
    },

    async answerConversation(cookie, input) {
      const path = "/ai/assistant/conversation";
      const response = await send(path, {
        method: "POST",
        cookie,
        body: {
          utterance: input.utterance,
          recent_turns: [...input.recentTurns],
          session_id: input.sessionId,
        },
      });
      if (!response.ok) {
        if (response.status === 422) {
          let failure: unknown;
          try {
            failure = await response.json();
          } catch {
            // An unreadable error does not become a trusted limit classification.
          }
          if (
            isRecord(failure) &&
            isRecord(failure.detail) &&
            failure.detail.code === CONVERSATION_OUTPUT_LIMIT
          ) {
            throw new AssistantError(
              "invalid_request",
              CONVERSATION_OUTPUT_LIMIT_MESSAGE,
              { reason: CONVERSATION_OUTPUT_LIMIT },
            );
          }
        }
        if (response.status === 404) {
          throw new AssistantError(
            "unsupported",
            "Conversation answers are not enabled in this deployment.",
          );
        }
        throw statusError(response.status, path);
      }
      try {
        return await response.json();
      } catch {
        throw new AssistantError(
          "unavailable",
          "The assistant did not return a readable conversation answer.",
        );
      }
    },

    async searchEmail(
      cookie: string,
      input: { query: string; limit?: number },
    ): Promise<unknown> {
      const response = await send("/search/query", {
        method: "POST",
        cookie,
        body: {
          query: input.query,
          mode: "SEARCH",
          types: ["EMAIL", "EMAIL_THREAD"],
          limit: input.limit ?? LIMITS.maxEmailResults,
        },
      });
      if (!response.ok) throw searchStatusError(response.status);
      try {
        return await response.json();
      } catch {
        throw new AssistantError(
          "unavailable",
          "Connected search did not return a readable result.",
        );
      }
    },

    async getResourceDetail(
      cookie: string,
      resourceId: string,
    ): Promise<unknown> {
      const path = `/knowledge/resources/${encodeURIComponent(resourceId)}`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The source detail was unreadable.",
        );
      });
    },

    async getClassNavigationTarget(cookie, input): Promise<unknown> {
      const query = new URLSearchParams({
        connection_id: input.connectionId,
        resource_id: input.resourceId,
      });
      const path = `/knowledge/class-navigation?${query.toString()}`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "Class navigation was unreadable.",
        );
      });
    },

    async querySubscriptions(cookie, selector): Promise<unknown> {
      const path = "/subscriptions/query";
      const response = await send(path, {
        method: "POST",
        cookie,
        body:
          selector === null
            ? { intent: "UPCOMING", days: 30 }
            : { intent: "SEARCH", text: selector },
      });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "Subscriptions returned an unreadable result.",
        );
      });
    },

    async getSubscriptionCancellation(cookie, id): Promise<unknown> {
      const path = `/subscriptions/${encodeURIComponent(id)}/cancellation`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "Cancellation status was unreadable.",
        );
      });
    },

    async getTrendingNews(cookie, region): Promise<unknown> {
      const path = region ? `/news/categories/${region}` : "/news/trending";
      const response = await send(path, { method: "GET", cookie });
      if (response.status === 503) {
        const payload: unknown = await response.json().catch(() => null);
        if (
          isRecord(payload) &&
          payload.detail === "News is not available yet."
        )
          throw new AssistantError(
            "unsupported",
            "News is not connected yet.",
            { reason: "news_disabled" },
          );
      }
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "News returned an unreadable feed.",
        );
      });
    },

    async getNewsStory(cookie, id): Promise<unknown> {
      const path = `/news/stories/${encodeURIComponent(id)}`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "News returned an unreadable story.",
        );
      });
    },

    async getNewsSummary(cookie, id): Promise<unknown> {
      const path = `/news/stories/${encodeURIComponent(id)}/summary`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "News returned an unreadable summary.",
        );
      });
    },

    async getWeather(cookie): Promise<unknown> {
      const path = "/workspace/weather";
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "Weather returned an unreadable result.",
        );
      });
    },

    async getClassSources(cookie): Promise<unknown> {
      const path = "/knowledge/class-sources";
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "Class sources returned an unreadable result.",
        );
      });
    },

    async listActions(cookie, input): Promise<unknown> {
      const path = `/actions?limit=${encodeURIComponent(String(input.limit))}`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, "/actions");
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The action ledger returned an unreadable result.",
        );
      });
    },

    async createKnowledgeEmailDraft(cookie, input): Promise<unknown> {
      const path = "/communication-drafts/from-knowledge-email";
      const response = await send(path, {
        method: "POST",
        cookie,
        body: { source_id: input.sourceId, instructions: input.instructions },
      });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The draft response was unreadable.",
        );
      });
    },

    async getCommunicationDraft(cookie, draftId): Promise<unknown> {
      const path = `/communication-drafts/${encodeURIComponent(draftId)}`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The draft response was unreadable.",
        );
      });
    },

    async reviseCommunicationDraft(cookie, draftId, input): Promise<unknown> {
      const path = `/communication-drafts/${encodeURIComponent(draftId)}`;
      const response = await send(path, {
        method: "PATCH",
        cookie,
        body: {
          expected_version: input.expectedVersion,
          to: [input.to],
          subject: input.subject,
          body: input.body,
        },
      });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The draft response was unreadable.",
        );
      });
    },

    async prepareCommunicationDraft(cookie, draftId, input): Promise<unknown> {
      const path = `/communication-drafts/${encodeURIComponent(draftId)}/prepare`;
      const response = await send(path, {
        method: "POST",
        cookie,
        body: {
          expected_version: input.expectedVersion,
          request_id: input.requestId,
          reply_to_source: true,
        },
      });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The approval response was unreadable.",
        );
      });
    },

    async approveCommunicationDraft(cookie, draftId, input): Promise<unknown> {
      const path = `/communication-drafts/${encodeURIComponent(draftId)}/approve`;
      const response = await send(path, {
        method: "POST",
        cookie,
        body: {
          request_id: input.requestId,
          expected_payload_hash: input.expectedPayloadHash,
          draft_version: input.draftVersion,
        },
      });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The approval response was unreadable.",
        );
      });
    },

    async getAction(cookie, actionId): Promise<unknown> {
      const path = `/actions/${encodeURIComponent(actionId)}`;
      const response = await send(path, { method: "GET", cookie });
      if (!response.ok) throw statusError(response.status, path);
      return response.json().catch(() => {
        throw new AssistantError(
          "unavailable",
          "The action response was unreadable.",
        );
      });
    },
  };
}
