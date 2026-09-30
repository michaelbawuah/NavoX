import type { AnswerCitation, AskResponse } from "@navox/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AskRequestError,
  askNotice,
  citationLabel,
  coverageNotes,
  followupReferent,
  isAnswered,
  runAsk,
  SubmitLedger,
} from "./knowledge-ask";

const citation: AnswerCitation = {
  resource_id: "3d2b6b1a-0000-4000-8000-000000000001",
  source_type: "EMAIL",
  title: "Quarterly planning review",
  canonical_url: "https://mail.example.com/message-1",
  source_version: "etag-1",
  source_updated_at: "2026-09-28T09:00:00Z",
  excerpt_index: 0,
  excerpt_text: "The quarterly planning review covers the budget forecast.",
  fact_id: null,
  fact_label: null,
  fact_value: null,
  authority: "Source system",
  sensitivity: "PERSONAL",
  origin: "CONNECTED",
};

function answer(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    session_id: "9f0f6a1e-0000-4000-8000-000000000010",
    turn_id: "9f0f6a1e-0000-4000-8000-000000000011",
    sequence: 1,
    status: "COMPLETED",
    answer_state: "READY",
    citations: [citation],
    results: [],
    coverage: {
      examined: 2,
      returned: 1,
      truncated: false,
      evidence_resources: 1,
      partial_reasons: [],
    },
    suggested_followups: ["What else does #1 support?"],
    trace_id: "trace",
    extractive: true,
    replay: false,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("grounded ask helpers", () => {
  it("labels a ready answer as extractive and quotes the stored span", () => {
    const response = answer();
    expect(isAnswered(response)).toBe(true);
    expect(askNotice(response)).toContain("quoted from a source you can open");
    expect(citationLabel(citation)).toBe("Quoted passage 1");
  });

  it("reports unavailable and withheld states honestly", () => {
    expect(askNotice(answer({ answer_state: "UNAVAILABLE" }))).toContain(
      "Answers are unavailable",
    );
    expect(askNotice(answer({ answer_state: "WITHHELD" }))).toContain(
      "withheld",
    );
    expect(askNotice(answer({ answer_state: "INSUFFICIENT" }))).toContain(
      "don’t cover",
    );
    expect(
      isAnswered(answer({ answer_state: "WITHHELD", citations: [] })),
    ).toBe(false);
  });

  it("explains partial coverage without leaking scores or content", () => {
    const notes = coverageNotes(
      answer({
        coverage: {
          examined: 3,
          returned: 1,
          truncated: true,
          evidence_resources: 1,
          partial_reasons: [
            "ASK_NATIVE_DOMAINS_EXCLUDED",
            "ASK_WITHHELD_SOURCE_CHANGED",
          ],
        },
      }),
    );
    expect(notes.join(" ")).toContain("News records stay in search");
    expect(notes.join(" ")).toContain("changed or was revoked");
    expect(notes.join(" ")).not.toMatch(/score|similarity|relevance/i);
  });

  it("uses numbered referents for follow-ups", () => {
    expect(followupReferent(3)).toBe("#3");
  });
});

describe("grounded ask client", () => {
  it("reuses one paid identifier for a retry of the same submission", () => {
    const ledger = new SubmitLedger();
    const key = JSON.stringify({ question: "budget", sessionId: null });
    const first = ledger.requestIdFor(key);
    expect(ledger.requestIdFor(key)).toBe(first);
    ledger.resolve();
    expect(ledger.requestIdFor(key)).not.toBe(first);
    const second = ledger.requestIdFor(key);
    ledger.forget();
    const third = ledger.requestIdFor(key);
    expect(third).not.toBe(second);
    expect(ledger.requestIdFor("alpha")).not.toBe(ledger.requestIdFor("beta"));
  });

  it("posts the question with a request identifier", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => answer(),
    });
    vi.stubGlobal("fetch", fetchMock);
    const response = await runAsk({
      question: "quarterly budget",
      request_id: "9f0f6a1e-0000-4000-8000-0000000000aa",
    });
    expect(response.answer_state).toBe("READY");
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/knowledge/ask");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toMatchObject({
      question: "quarterly budget",
      request_id: "9f0f6a1e-0000-4000-8000-0000000000aa",
    });
  });

  it("maps replay, quota and validation statuses to honest messages", async () => {
    for (const [status, fragment] of [
      [409, "already sent"],
      [429, "Too many questions"],
      [422, "wasn’t valid"],
    ] as [number, string][]) {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue({ ok: false, status, json: async () => ({}) }),
      );
      let caught: unknown = null;
      try {
        await runAsk({
          question: "q",
          request_id: "9f0f6a1e-0000-4000-8000-0000000000bb",
        });
      } catch (error) {
        caught = error;
      }
      expect(caught).toBeInstanceOf(AskRequestError);
      expect((caught as AskRequestError).status).toBe(status);
      expect((caught as AskRequestError).message).toContain(fragment);
    }
  });
});
