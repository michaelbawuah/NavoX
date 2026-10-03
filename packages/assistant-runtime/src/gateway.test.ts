import { describe, expect, it, vi } from "vitest";
import { AssistantError } from "./errors";
import { createNavoxUpstream, type FetchLike } from "./gateway";

const base = "https://navox.example/api/v1";
const cookie = "navox_session=abc123";
const account = {
  id: "11111111-1111-4111-8111-111111111111",
  workspace: { id: "22222222-2222-4222-8222-222222222222" },
  email: "operator@example.com",
};

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("NavoX API gateway", () => {
  it("derives the account from the forwarded session cookie", async () => {
    const fetchImpl = vi.fn(async () => json(account)) as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: `${base}/`, fetchImpl });
    const scope = await upstream.fetchAccount(cookie);
    expect(scope).toEqual({
      user_id: account.id,
      workspace_id: account.workspace.id,
      email: "operator@example.com",
    });
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${base}/auth/me`);
    expect(init.credentials).toBeUndefined();
    expect(init.headers.cookie).toBe(cookie);
    expect(init.cache).toBe("no-store");
  });

  it("refuses an empty cookie before calling the API", async () => {
    const fetchImpl = vi.fn() as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: base, fetchImpl });
    await expect(upstream.fetchAccount("")).rejects.toMatchObject({
      code: "unauthorized",
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("maps upstream statuses onto qualified failures", async () => {
    const cases: [number, string][] = [
      [401, "unauthorized"],
      [403, "forbidden"],
      [404, "not_found"],
      [422, "invalid_request"],
      [429, "unavailable"],
      [500, "unavailable"],
    ];
    for (const [status, code] of cases) {
      const upstream = createNavoxUpstream({
        baseUrl: base,
        fetchImpl: async () => json({ detail: "nope" }, status),
      });
      await expect(upstream.fetchAccount(cookie)).rejects.toMatchObject({
        code,
      });
    }
  });

  it("distinguishes disabled News from a transient upstream failure", async () => {
    const disabled = createNavoxUpstream({
      baseUrl: base,
      fetchImpl: async () =>
        json({ detail: "News is not available yet." }, 503),
    });
    await expect(disabled.getTrendingNews(cookie)).rejects.toMatchObject({
      code: "unsupported",
      reason: "news_disabled",
      retryable: false,
    });
    const failed = createNavoxUpstream({
      baseUrl: base,
      fetchImpl: async () =>
        json({ detail: "private upstream diagnostic" }, 503),
    });
    await expect(failed.getTrendingNews(cookie)).rejects.toMatchObject({
      code: "unavailable",
      reason: null,
      retryable: true,
    });
  });

  it("turns a network failure into an unavailable failure", async () => {
    const upstream = createNavoxUpstream({
      baseUrl: base,
      fetchImpl: async () => {
        throw new TypeError("fetch failed");
      },
    });
    await expect(upstream.fetchAccount(cookie)).rejects.toMatchObject({
      code: "unavailable",
      retryable: true,
    });
  });

  it("creates a SPEC-005 session and validates its id", async () => {
    const upstream = createNavoxUpstream({
      baseUrl: base,
      fetchImpl: async () =>
        json({ id: "55555555-5555-4555-8555-555555555555" }, 201),
    });
    await expect(upstream.createAssistantSession(cookie)).resolves.toBe(
      "55555555-5555-4555-8555-555555555555",
    );
    const bad = createNavoxUpstream({
      baseUrl: base,
      fetchImpl: async () => json({ id: "x" }),
    });
    await expect(bad.createAssistantSession(cookie)).rejects.toMatchObject({
      code: "unavailable",
    });
  });

  it("sends the original question and bounded timezone to Today", async () => {
    const fetchImpl = vi.fn(async () =>
      json({
        intent: "today",
        answer: "Nothing needs your attention right now.",
        items: [],
        supported_queries: [],
        details: [],
      }),
    ) as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: base, fetchImpl });
    const result = await upstream.queryToday(cookie, {
      query: "What am I missing today?",
      timezone: "America/New_York",
    });
    expect(result.intent).toBe("today");
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${base}/today/query`);
    expect(JSON.parse(init.body)).toEqual({
      query: "What am I missing today?",
      timezone: "America/New_York",
    });
  });

  it("reads the authenticated existing meeting-prep service", async () => {
    const fetchImpl = vi.fn(async () =>
      json({
        commitment_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title: "Project review",
        starts_at: "2026-10-01T14:00:00Z",
        minutes_until: 45,
        description: null,
        related_commitments: [],
        prep_points: ["Review the plan"],
      }),
    ) as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: base, fetchImpl });
    await expect(upstream.getMeetingPrep(cookie)).resolves.toMatchObject({
      title: "Project review",
    });
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${base}/proactive/meeting-prep`);
    expect(init.method).toBe("GET");
    expect(init.headers.cookie).toBe(cookie);
  });

  it("never trusts an unreadable account payload", async () => {
    const upstream = createNavoxUpstream({
      baseUrl: base,
      fetchImpl: async () => json({}),
    });
    const failure = await upstream.fetchAccount(cookie).catch((error) => error);
    expect(failure).toBeInstanceOf(AssistantError);
    expect((failure as AssistantError).message).toMatch(/unreadable account/i);
  });

  it("posts a bounded, scoped planning request to SPEC-005", async () => {
    const plan = { version: 1, intents: [] };
    const fetchImpl = vi.fn(async () => json(plan)) as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: base, fetchImpl });
    await expect(
      upstream.planIntents(cookie, {
        utterance: "What did Sarah email me?",
        recentReferences: ["What needs my attention?"],
        sessionId: "55555555-5555-4555-8555-555555555555",
      }),
    ).resolves.toEqual(plan);
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${base}/ai/assistant/intents`);
    expect(init.headers.cookie).toBe(cookie);
    expect(JSON.parse(init.body)).toEqual({
      utterance: "What did Sarah email me?",
      recent_references: ["What needs my attention?"],
      session_id: "55555555-5555-4555-8555-555555555555",
    });
  });

  it("keeps a planning transport failure qualified and never guesses", async () => {
    for (const status of [409, 422, 500, 503]) {
      const upstream = createNavoxUpstream({
        baseUrl: base,
        fetchImpl: async () => json({ detail: "no" }, status),
      });
      const failure = await upstream
        .planIntents(cookie, {
          utterance: "Do something",
          recentReferences: [],
          sessionId: null,
        })
        .catch((error) => error);
      expect(failure).toBeInstanceOf(AssistantError);
      expect((failure as AssistantError).code).toBe(
        status === 409 ? "conflict" : "unavailable",
      );
    }
  });

  it("marks only the explicit no-provider 503 for the SPEC-002 fallback", async () => {
    const noProvider = createNavoxUpstream({
      baseUrl: base,
      fetchImpl: async () =>
        json(
          { detail: "No qualified AI provider is available for this request" },
          503,
        ),
    });
    const failure = await noProvider
      .planIntents(cookie, {
        utterance: "Do something",
        recentReferences: [],
        sessionId: null,
      })
      .catch((error) => error);
    expect(failure).toBeInstanceOf(AssistantError);
    expect((failure as AssistantError).reason).toBe("planner_no_provider");
    expect((failure as AssistantError).code).toBe("unavailable");

    for (const status of [422, 429, 500, 502]) {
      const upstream = createNavoxUpstream({
        baseUrl: base,
        fetchImpl: async () => json({ detail: "no" }, status),
      });
      const other = await upstream
        .planIntents(cookie, {
          utterance: "Do something",
          recentReferences: [],
          sessionId: null,
        })
        .catch((error) => error);
      expect((other as AssistantError).code, String(status)).toBe(
        "unavailable",
      );
      expect((other as AssistantError).reason, String(status)).toBeNull();
    }
  });

  it("searches connected email through SPEC-007 with exact types", async () => {
    const payload = { results: [], coverage: { truncated: false } };
    const fetchImpl = vi.fn(async () => json(payload)) as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: base, fetchImpl });
    await expect(
      upstream.searchEmail(cookie, { query: "Sarah renewal", limit: 5 }),
    ).resolves.toEqual(payload);
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${base}/search/query`);
    expect(init.headers.cookie).toBe(cookie);
    expect(JSON.parse(init.body)).toEqual({
      query: "Sarah renewal",
      mode: "SEARCH",
      types: ["EMAIL", "EMAIL_THREAD"],
      limit: 5,
    });
  });

  it("maps a disabled or failed connected search onto a qualified failure", async () => {
    const cases: [number, string][] = [
      [401, "unauthorized"],
      [403, "forbidden"],
      [404, "unsupported"],
      [429, "unavailable"],
      [503, "unavailable"],
    ];
    for (const [status, code] of cases) {
      const upstream = createNavoxUpstream({
        baseUrl: base,
        fetchImpl: async () => json({ detail: "no" }, status),
      });
      await expect(
        upstream.searchEmail(cookie, { query: "Sarah" }),
      ).rejects.toMatchObject({ code });
    }
  });

  it("uses only read-only SPEC-004 subscription paths with the forwarded cookie", async () => {
    const fetchImpl = vi.fn(async () => json(null)) as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: base, fetchImpl });
    await upstream.querySubscriptions(cookie, "Netflix");
    await upstream.getSubscriptionCancellation(
      cookie,
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    );
    const calls = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls[0][0]).toBe(`${base}/subscriptions/query`);
    expect(calls[0][1].method).toBe("POST");
    expect(calls[0][1].headers.cookie).toBe(cookie);
    expect(JSON.parse(calls[0][1].body)).toEqual({
      intent: "SEARCH",
      text: "Netflix",
    });
    expect(calls[1][0]).toBe(
      `${base}/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/cancellation`,
    );
    expect(calls[1][1].method).toBe("GET");
    expect(calls[1][1].headers.cookie).toBe(cookie);
  });

  it("uses only authenticated SPEC-006 read paths for trends, detail and summary", async () => {
    const fetchImpl = vi.fn(async () => json([])) as unknown as FetchLike;
    const upstream = createNavoxUpstream({ baseUrl: base, fetchImpl });
    const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    await upstream.getTrendingNews(cookie);
    await upstream.getNewsStory(cookie, id);
    await upstream.getNewsSummary(cookie, id);
    const calls = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.map((call) => call[0])).toEqual([
      `${base}/news/trending`,
      `${base}/news/stories/${id}`,
      `${base}/news/stories/${id}/summary`,
    ]);
    expect(calls.every((call) => call[1].method === "GET")).toBe(true);
    expect(calls.every((call) => call[1].headers.cookie === cookie)).toBe(true);
  });
});
