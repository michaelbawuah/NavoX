import type { SearchResponse } from "@navox/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  answerNotice,
  clearRecentSearches,
  coverageNotes,
  exclusiveEndOfDay,
  needsFreshSearch,
  RequestGate,
  resourceTypeLabel,
  runLiveSearch,
  runSearch,
  SearchRequestError,
  safeSourceUrl,
  searchTime,
  startOfDay,
  waitForSearchRefresh,
} from "./search";

describe("bounded live search", () => {
  it("carries a stable dispatch ID separately from the cached query", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => response() });
    vi.stubGlobal("fetch", fetcher);
    await runLiveSearch({ query: "latest planning" }, "request-123");
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toContain("/search/live");
    expect(JSON.parse(options.body)).toEqual({
      request_id: "request-123",
      search: { query: "latest planning" },
    });
  });

  it("does not infer freshness from an unrelated substring", () => {
    expect(needsFreshSearch("snow known lives currentness")).toBe(false);
    expect(needsFreshSearch("latest planning")).toBe(true);
  });

  it("aborts a pending progressive update when its query changes", async () => {
    const controller = new AbortController();
    const pending = waitForSearchRefresh(controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("labels partial refresh without claiming fresh answers", () => {
    const notes = coverageNotes(
      response({
        refresh: {
          state: "PARTIAL",
          queued: 2,
          unavailable: 1,
          bounded: true,
        },
      }),
    );
    expect(notes.join(" ")).toContain("remain cached");
    expect(notes.join(" ")).toContain("could not be refreshed");
  });
});

function response(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    interpreted_mode: "SEARCH",
    intent: "FIND_RESOURCE",
    results: [],
    structured_facts: [],
    answer: null,
    answer_state: "NOT_REQUESTED",
    suggested_followups: [],
    trace_id: "trace",
    session_id: null,
    coverage: {
      candidate_bound: 200,
      examined: 0,
      returned: 0,
      truncated: false,
      stale_dropped: 0,
      exclusions_applied: 0,
      not_searchable: 0,
      partial_reasons: [],
    },
    unavailable_modes: ["SEMANTIC", "GRAPH"],
    exclusion_count: 0,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("connected search client", () => {
  it("posts the validated body with credentials and no cache", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response()), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await runSearch({
      query: "quarterly budget",
      mode: "SEARCH",
    });
    expect(result.interpreted_mode).toBe("SEARCH");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8000/api/v1/search/query");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
    expect(JSON.parse(String(init.body))).toEqual({
      query: "quarterly budget",
      mode: "SEARCH",
    });
  });

  it("maps status codes to operator-free guidance instead of raw errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 404 })),
    );
    await expect(runSearch({ query: "budget" })).rejects.toBeInstanceOf(
      SearchRequestError,
    );
    await expect(runSearch({ query: "budget" })).rejects.toThrow(
      /isn’t enabled for this workspace/,
    );
  });

  it("clears history through the dedicated endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ cleared: 3 }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await expect(clearRecentSearches()).resolves.toEqual({ cleared: 3 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8000/api/v1/search/recent");
    expect(init.method).toBe("DELETE");
  });
});

describe("truthful presentation helpers", () => {
  it("reports an unavailable answer instead of inventing one", () => {
    const ask = response({
      interpreted_mode: "ASK",
      answer_state: "UNAVAILABLE",
      answer: null,
    });
    expect(answerNotice(ask)).toMatch(/can’t write an answer yet/);
    expect(answerNotice(response())).toBeNull();
  });

  it("explains uncovered coverage without exposing scores", () => {
    const limited = response({
      coverage: {
        candidate_bound: 200,
        examined: 200,
        returned: 20,
        truncated: true,
        stale_dropped: 2,
        exclusions_applied: 1,
        not_searchable: 4,
        partial_reasons: [
          "SOURCE_CONTENT_NOT_RETAINED",
          "SEMANTIC_UNAVAILABLE",
        ],
      },
    });
    const notes = coverageNotes(limited);
    expect(notes).toHaveLength(4);
    expect(notes.join(" ")).toMatch(/content/);
    expect(notes.join(" ")).toMatch(/changed at the source/);
    expect(notes.join(" ")).toMatch(/limited slice/);
    expect(notes.join(" ")).toMatch(/Meaning-based search/);
    expect(notes.join(" ")).not.toMatch(/score|rank|similarity/i);
  });

  it("stays quiet when nothing was truncated", () => {
    expect(coverageNotes(response())).toEqual([]);
  });

  it("explains that a connected-source filter leaves native records out", () => {
    const filtered = response({
      coverage: {
        candidate_bound: 200,
        examined: 1,
        returned: 0,
        truncated: false,
        stale_dropped: 0,
        exclusions_applied: 0,
        not_searchable: 0,
        partial_reasons: ["CONNECTED_SOURCE_FILTER_EXCLUDES_NATIVE_DOMAINS"],
      },
    });
    expect(coverageNotes(filtered).join(" ")).toMatch(
      /commitments and subscriptions/,
    );
  });
});

describe("in-flight request guard", () => {
  it("makes a superseded response inert", () => {
    const gate = new RequestGate();
    const first = gate.next();
    const second = gate.next();
    expect(gate.isCurrent(first)).toBe(false);
    expect(gate.isCurrent(second)).toBe(true);
  });

  it("invalidates pending work when a source is hidden", () => {
    const gate = new RequestGate();
    const token = gate.next();
    gate.invalidate();
    expect(gate.isCurrent(token)).toBe(false);
    const fresh = gate.next();
    expect(gate.isCurrent(fresh)).toBe(true);
  });
});

describe("date window boundaries", () => {
  it("keeps the final second of the selected day", () => {
    expect(exclusiveEndOfDay("2026-10-02")).toBe("2026-10-03T00:00:00.000Z");
    expect(exclusiveEndOfDay("2026-12-31")).toBe("2027-01-01T00:00:00.000Z");
  });

  it("starts at midnight and rejects unparsable input", () => {
    expect(startOfDay("2026-10-02")).toBe("2026-10-02T00:00:00.000Z");
    expect(startOfDay("")).toBeNull();
    expect(exclusiveEndOfDay("not-a-date")).toBeNull();
  });
});

describe("source links and labels", () => {
  it("only accepts https links without embedded credentials", () => {
    expect(safeSourceUrl(null)).toBeNull();
    expect(safeSourceUrl("not a url")).toBeNull();
    expect(safeSourceUrl("javascript:alert(1)")).toBeNull();
    expect(safeSourceUrl("http://mail.example.com/m/1")).toBeNull();
    expect(safeSourceUrl("https://user:pass@mail.example.com/m/1")).toBeNull();
    expect(safeSourceUrl("https://mail.example.com/m/1")).toBe(
      "https://mail.example.com/m/1",
    );
  });

  it("labels known types and falls back safely for unknown ones", () => {
    expect(resourceTypeLabel("CALENDAR_EVENT")).toBe("Calendar");
    expect(resourceTypeLabel("SUBSCRIPTION")).toBe("Subscription");
    expect(resourceTypeLabel("MADE_UP" as never)).toBe("Other");
  });

  it("renders an explicit placeholder for missing timestamps", () => {
    expect(searchTime(null)).toBe("Time unavailable");
    expect(searchTime("not-a-date")).toBe("Time unavailable");
    expect(searchTime("2026-09-29T12:00:00Z")).not.toBe("Time unavailable");
  });
});

it("names an affected source without displaying provider diagnostics", () => {
  const result = response();
  result.coverage.source_issues = [
    { connection_id: "owned", source_label: "Gmail", state: "SYNC_FAILED" },
  ];
  expect(coverageNotes(result)).toContain(
    "Gmail could not refresh (sync failed). Its cached results may be incomplete.",
  );
});
