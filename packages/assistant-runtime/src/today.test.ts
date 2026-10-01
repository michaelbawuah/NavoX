import { describe, expect, it } from "vitest";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";
import { blocksFromToday, decideToday, parseTodayQueryResult } from "./today";

const item = {
  id: "task-1",
  type: "commitment",
  title: "Send the vendor recap",
  description: "Waiting on finance",
  status: "open",
  due_at: "2026-10-01T15:00:00+00:00",
  band: "attention",
  sources: [
    {
      provider: "google",
      source_type: "EMAIL",
      external_resource_id: "message-1",
      evidence_id: "evidence-1",
      connection_id: "connection-1",
      observed_at: "2026-09-29T09:00:00+00:00",
      evidence_locator: { kind: "message" },
    },
  ],
};

describe("SPEC-002 Today payloads", () => {
  it("validates and keeps only selectors, never evidence text", () => {
    const parsed = parseTodayQueryResult({
      intent: "today",
      answer: "1 item needs attention now.",
      items: [item],
      supported_queries: ["What needs my attention?"],
      details: ["Attention band: 0.81"],
    });
    expect(parsed.items[0]?.title).toBe("Send the vendor recap");
    expect(parsed.items[0]?.sources[0]).toEqual({
      provider: "google",
      source_type: "EMAIL",
      external_resource_id: "message-1",
      evidence_id: "evidence-1",
      connection_id: "connection-1",
      observed_at: "2026-09-29T09:00:00+00:00",
    });
    expect(JSON.stringify(parsed)).not.toContain("evidence_locator");
  });

  it("fails closed on a malformed or oversized payload", () => {
    expect(() => parseTodayQueryResult(null)).toThrow(AssistantError);
    expect(() => parseTodayQueryResult({ intent: "today" })).toThrow(
      /could not verify/i,
    );
    expect(() =>
      parseTodayQueryResult({
        intent: "today",
        answer: "x".repeat(LIMITS.maxAnswerLength + 1),
        items: [],
        supported_queries: [],
        details: [],
      }),
    ).toThrow(/could not verify/i);
    expect(() =>
      parseTodayQueryResult({
        intent: "today",
        answer: "ok",
        items: [{ id: 1, type: "task", title: "t", status: "open" }],
        supported_queries: [],
        details: [],
      }),
    ).toThrow(/could not verify/i);
  });

  it("refuses an intent outside the SPEC-002 vocabulary", () => {
    for (const intent of ["tomorrow", "weather", "TODAY", "", "send_email"]) {
      expect(() =>
        parseTodayQueryResult({
          intent,
          answer: "Something confident",
          items: [],
          supported_queries: [],
          details: [],
        }),
      ).toThrow(/could not verify/i);
    }
    // The known vocabulary still parses.
    for (const intent of [
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
    ]) {
      expect(
        parseTodayQueryResult({
          intent,
          answer: "An answer",
          items: [],
          supported_queries: [],
          details: [],
        }).intent,
      ).toBe(intent);
    }
  });

  it("refuses a supported intent that carries no answer text", () => {
    expect(() =>
      parseTodayQueryResult({
        intent: "today",
        answer: "   ",
        items: [],
        supported_queries: [],
        details: [],
      }),
    ).toThrow(/could not verify/i);
  });

  it("records an unsupported question as CLARIFY without inventing facts", () => {
    const parsed = parseTodayQueryResult({
      intent: "unsupported",
      answer: "I can answer read-only questions about today.",
      items: [],
      supported_queries: ["What needs my attention?"],
      details: [],
    });
    const decision = decideToday(parsed);
    expect(decision.kind).toBe("CLARIFY");
    expect(decision.response_state).toBe("CLARIFY");
    expect(decision.capability_id).toBeNull();
    const blocks = blocksFromToday(parsed);
    expect(blocks.map((block) => block.kind)).toEqual([
      "ANSWER",
      "SUGGESTIONS",
    ]);
  });

  it("records a supported answer as READY with typed blocks", () => {
    const parsed = parseTodayQueryResult({
      intent: "today",
      answer: "1 item needs attention now.",
      items: [item],
      supported_queries: [],
      details: ["Attention band: 0.81"],
    });
    const decision = decideToday(parsed);
    expect(decision.kind).toBe("DELEGATE");
    expect(decision.response_state).toBe("READY");
    expect(decision.requires_approval).toBe(false);
    expect(decision.action_state).toBe("NONE");
    const kinds = blocksFromToday(parsed).map((block) => block.kind);
    expect(kinds).toEqual(["ANSWER", "ITEM", "DETAILS", "CITATIONS"]);
  });

  it("renders an empty supported answer without a fabricated item", () => {
    const parsed = parseTodayQueryResult({
      intent: "attention",
      answer: "Nothing currently needs your attention.",
      items: [],
      supported_queries: [],
      details: [],
    });
    const blocks = blocksFromToday(parsed);
    expect(blocks).toEqual([
      { kind: "ANSWER", text: "Nothing currently needs your attention." },
    ]);
  });

  it("keeps an empty unsupported payload consistent as CLARIFY", () => {
    const parsed = parseTodayQueryResult({
      intent: "unsupported",
      answer: "",
      items: [],
      supported_queries: [],
      details: [],
    });
    expect(decideToday(parsed).response_state).toBe("CLARIFY");
    const blocks = blocksFromToday(parsed);
    expect(blocks).toEqual([
      {
        kind: "NOTICE",
        state: "CLARIFY",
        text: "Today returned no answer for this question.",
      },
    ]);
  });

  it("never emits an UNAVAILABLE notice alongside a READY decision", () => {
    for (const intent of ["today", "attention", "this_week", "handleable"]) {
      const parsed = parseTodayQueryResult({
        intent,
        answer: "An answer",
        items: [],
        supported_queries: [],
        details: [],
      });
      const decision = decideToday(parsed);
      const notices = blocksFromToday(parsed).flatMap((block) =>
        block.kind === "NOTICE" ? [block.state] : [],
      );
      expect(decision.response_state).toBe("READY");
      expect(notices).toEqual([]);
    }
  });
});
