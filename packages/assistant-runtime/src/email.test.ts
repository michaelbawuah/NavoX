import { describe, expect, it } from "vitest";
import {
  decideEmailSearch,
  parseEmailResourceDetail,
  parseEmailSearchOutcome,
} from "./email";
import { emailSearchPayload } from "./testing/fakes";

const NOW = new Date("2026-09-30T12:00:00.000Z");
const RESOURCE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RESOURCE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function outcome(payload: unknown) {
  return parseEmailSearchOutcome(payload);
}

describe("SPEC-007 email resolution", () => {
  const CURRENT_CONTENT = {
    state: "READY" as const,
    excerpts: [
      { source: "content" as const, text: "Your renewal is confirmed." },
    ],
    source_version: "v1",
    fresh_until: "2026-10-01T12:00:00.000Z",
  };

  it("resolves one exact match with verified source content only", () => {
    const parsed = outcome(
      emailSearchPayload([
        {
          resource_id: RESOURCE_A,
          title: "Renewal confirmation",
          external_resource_id: "message-1",
        },
      ]),
    );
    expect(parsed.results).toHaveLength(1);
    expect(parsed.results[0]?.excerpt_count).toBe(1);
    const decision = decideEmailSearch(parsed, {
      now: NOW,
      content: CURRENT_CONTENT,
    });
    expect(decision.state).toBe("READY");
    expect(decision.decision.kind).toBe("DELEGATE");
    expect(decision.decision.capability_id).toBe("email.search");
    expect(decision.decision.requires_approval).toBe(false);
    expect(decision.decision.action_state).toBe("NONE");
    expect(decision.blocks.map((block) => block.kind)).toEqual([
      "ANSWER",
      "ITEM",
      "EVIDENCE",
      "CITATIONS",
    ]);
    const evidence = decision.blocks.find((block) => block.kind === "EVIDENCE");
    expect(evidence).toMatchObject({
      kind: "EVIDENCE",
      evidence_id: RESOURCE_A,
      source_type: "EMAIL",
      source_version: "v1",
      fresh_until: "2026-10-01T12:00:00.000Z",
    });
    // The durable block carries selectors and version metadata only.
    expect(JSON.stringify(evidence)).not.toContain(
      "Your renewal is confirmed.",
    );
    const citations = decision.blocks.find(
      (block) => block.kind === "CITATIONS",
    );
    expect(citations).toMatchObject({
      kind: "CITATIONS",
      citations: [
        {
          provider: "navox",
          source_type: "EMAIL",
          evidence_id: RESOURCE_A,
          external_resource_id: "message-1",
        },
      ],
    });
    // The search listing's own excerpt text and any canonical URL are never
    // presented as the message: only the fresh resource detail is.
    expect(JSON.stringify(decision.blocks)).not.toContain(
      "excerpt text that must never be copied",
    );
    expect(JSON.stringify(decision.blocks)).not.toContain("canonical_url");
  });

  it("qualifies a unique email for every unverifiable content state", () => {
    const parsed = outcome(
      emailSearchPayload([
        { resource_id: RESOURCE_A, title: "Renewal notice" },
      ]),
    );
    const cases = [
      { content: undefined, reason: "email.search.unverified" },
      {
        content: { state: "UNAVAILABLE" as const, reason: "changed" as const },
        reason: "email.search.changed",
      },
      {
        content: { state: "UNAVAILABLE" as const, reason: "stale" as const },
        reason: "email.search.stale",
      },
      {
        content: {
          state: "UNAVAILABLE" as const,
          reason: "no_content" as const,
        },
        reason: "email.search.incomplete",
      },
      {
        content: {
          state: "UNAVAILABLE" as const,
          reason: "unverified" as const,
        },
        reason: "email.search.unverified",
      },
    ];
    for (const entry of cases) {
      const decision = decideEmailSearch(parsed, {
        now: NOW,
        content: entry.content,
      });
      expect(decision.state).toBe("CLARIFY");
      expect(decision.decision.reason).toBe(entry.reason);
      expect(decision.decision.action_state).toBe("NONE");
      expect(decision.blocks.some((b) => b.kind === "ITEM")).toBe(true);
      expect(decision.blocks.some((b) => b.kind === "EVIDENCE")).toBe(false);
      const notice = decision.blocks.find((block) => block.kind === "NOTICE");
      expect(notice).toMatchObject({ kind: "NOTICE", state: "CLARIFY" });
    }
  });

  it("never marks a title-only or drifted resource detail as current content", () => {
    const expectation = {
      resource_id: RESOURCE_A,
      source_type: "EMAIL" as const,
      source_version: "v1",
      fresh_until: "2026-10-01T12:00:00.000Z",
    };
    const detail = {
      resource_id: RESOURCE_A,
      source_type: "EMAIL",
      title: "Renewal confirmation",
      source_version: "v1",
      fresh_until: "2026-10-01T12:00:00.000Z",
      chunks: [],
    };
    // A title with no content chunk is incomplete, never the message.
    expect(parseEmailResourceDetail(detail, expectation, { now: NOW })).toEqual(
      { state: "UNAVAILABLE", reason: "no_content" },
    );
    // The detail must be exactly the revision the search named.
    expect(
      parseEmailResourceDetail(
        { ...detail, chunks: [{ text_content: "Body" }], source_version: "v2" },
        expectation,
        { now: NOW },
      ),
    ).toEqual({ state: "UNAVAILABLE", reason: "changed" });
    // An unknown search version cannot be verified.
    expect(
      parseEmailResourceDetail(
        { ...detail, chunks: [{ text_content: "Body" }] },
        { ...expectation, source_version: null },
        { now: NOW },
      ),
    ).toEqual({ state: "UNAVAILABLE", reason: "changed" });
    // An expired or unknown freshness bound is stale/unverified.
    expect(
      parseEmailResourceDetail(
        {
          ...detail,
          chunks: [{ text_content: "Body" }],
          fresh_until: "2026-09-01T00:00:00.000Z",
        },
        { ...expectation, fresh_until: "2026-09-01T00:00:00.000Z" },
        { now: NOW },
      ),
    ).toEqual({ state: "UNAVAILABLE", reason: "stale" });
    expect(
      parseEmailResourceDetail(
        { ...detail, chunks: [{ text_content: "Body" }], fresh_until: null },
        expectation,
        { now: NOW },
      ),
    ).toEqual({ state: "UNAVAILABLE", reason: "unverified" });
    // A moved freshness authority is not the same indexed answer either.
    expect(
      parseEmailResourceDetail(
        {
          ...detail,
          chunks: [{ text_content: "Body" }],
          fresh_until: "2026-10-02T12:00:00.000Z",
        },
        expectation,
        { now: NOW },
      ),
    ).toEqual({ state: "UNAVAILABLE", reason: "unverified" });
    // A matching revision with a real chunk is the only READY shape.
    expect(
      parseEmailResourceDetail(
        {
          ...detail,
          chunks: [
            { text_content: null },
            { text_content: "  Your renewal is confirmed.  " },
          ],
        },
        expectation,
        { now: NOW },
      ),
    ).toMatchObject({
      state: "READY",
      source_version: "v1",
      excerpts: [{ source: "content", text: "Your renewal is confirmed." }],
    });
  });

  it("never treats a thread as one message or quotes its listing", () => {
    const decision = decideEmailSearch(
      parseEmailSearchOutcome(
        emailSearchPayload([
          {
            resource_id: RESOURCE_A,
            source_type: "EMAIL_THREAD",
            title: "Renewal thread",
          },
        ]),
      ),
      { now: NOW, content: { state: "THREAD_ONLY" } },
    );
    expect(decision.state).toBe("CLARIFY");
    expect(decision.decision.reason).toBe("email.search.thread_only");
    expect(decision.decision.action_state).toBe("NONE");
    expect(decision.blocks.some((block) => block.kind === "EVIDENCE")).toBe(
      false,
    );
    // The thread citation survives so it can still be inspected safely.
    const citations = decision.blocks.find(
      (block) => block.kind === "CITATIONS",
    );
    expect(citations).toMatchObject({
      kind: "CITATIONS",
      citations: [{ source_type: "EMAIL_THREAD", evidence_id: RESOURCE_A }],
    });
  });

  it("asks which one when several emails match", () => {
    const parsed = parseEmailSearchOutcome(
      emailSearchPayload([
        { resource_id: RESOURCE_A, title: "Renewal notice" },
        { resource_id: RESOURCE_B, title: "Renewal receipt" },
      ]),
    );
    const decision = decideEmailSearch(parsed, { now: NOW });
    expect(decision.state).toBe("CLARIFY");
    expect(decision.decision.kind).toBe("CLARIFY");
    expect(decision.decision.action_state).toBe("NONE");
    const answer = decision.blocks.find((block) => block.kind === "ANSWER");
    expect(answer).toMatchObject({
      kind: "ANSWER",
      text: "I found 2 matching emails. Which one did you mean?",
    });
    expect(
      decision.blocks.filter((block) => block.kind === "ITEM"),
    ).toHaveLength(2);
  });

  it("keeps an empty result honest instead of inventing a match", () => {
    const decision = decideEmailSearch(outcome(emailSearchPayload([])), {
      now: NOW,
    });
    expect(decision.state).toBe("CLARIFY");
    expect(decision.decision.reason).toBe("email.search.no_match");
    expect(decision.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "CLARIFY",
        text: "I could not find a matching email in your connected sources. Nothing was changed.",
      },
    ]);
  });

  it("cannot establish a unique target from incomplete coverage", () => {
    const cases: {
      label: string;
      overrides: Parameters<typeof emailSearchPayload>[1];
      expectSourceCaveat: boolean;
    }[] = [
      {
        label: "truncated results",
        overrides: { truncated: true },
        expectSourceCaveat: false,
      },
      {
        label: "unavailable search modes",
        overrides: { unavailable_modes: ["SEMANTIC"] },
        expectSourceCaveat: false,
      },
      {
        label: "connected source issues",
        overrides: {
          source_issues: [
            {
              connection_id: "88888888-8888-4888-8888-888888888888",
              source_label: "Gmail",
              state: "DEGRADED",
            },
          ],
        },
        expectSourceCaveat: true,
      },
      {
        label: "partial reasons",
        overrides: { partial_reasons: ["SEMANTIC_PARTIAL_COVERAGE"] },
        expectSourceCaveat: false,
      },
    ];
    for (const entry of cases) {
      const decision = decideEmailSearch(
        parseEmailSearchOutcome(
          emailSearchPayload([{ resource_id: RESOURCE_A }], entry.overrides),
        ),
        { now: NOW },
      );
      expect(decision.state, entry.label).toBe("CLARIFY");
      expect(decision.decision.kind, entry.label).toBe("CLARIFY");
      expect(decision.decision.reason, entry.label).toBe(
        "email.search.incomplete",
      );
      expect(decision.decision.action_state, entry.label).toBe("NONE");
      expect(decision.decision.requires_approval, entry.label).toBe(false);
      const answer = decision.blocks.find((block) => block.kind === "ANSWER");
      expect(answer, entry.label).toMatchObject({
        kind: "ANSWER",
        text: "I found one possible matching email, but the search coverage was incomplete.",
      });
      const details = decision.blocks.find((block) => block.kind === "DETAILS");
      expect(details, entry.label).toMatchObject({ kind: "DETAILS" });
      const lines =
        details?.kind === "DETAILS" ? details.lines : ([] as string[]);
      expect(lines).toContain(
        "Search coverage was incomplete, so other matches may exist.",
      );
      expect(
        lines.includes("Some connected sources could not be searched."),
      ).toBe(entry.expectSourceCaveat);
      // The candidate is still shown for the user to choose from.
      expect(
        decision.blocks.some((block) => block.kind === "ITEM"),
        entry.label,
      ).toBe(true);
    }
  });

  it("keeps an incomplete empty result honest and bounded", () => {
    const decision = decideEmailSearch(
      parseEmailSearchOutcome(
        emailSearchPayload([], {
          truncated: true,
          suggested_followups: ["Search all mail"],
        }),
      ),
      { now: NOW },
    );
    expect(decision.state).toBe("CLARIFY");
    expect(decision.decision.reason).toBe("email.search.no_match");
    expect(decision.blocks.map((block) => block.kind)).toEqual([
      "NOTICE",
      "DETAILS",
      "SUGGESTIONS",
    ]);
    expect(decision.decision.action_state).toBe("NONE");
  });

  it("requires unexpired, known freshness for a grounded answer", () => {
    const stale = decideEmailSearch(
      parseEmailSearchOutcome(
        emailSearchPayload([
          {
            resource_id: RESOURCE_A,
            fresh_until: "2026-09-01T00:00:00.000Z",
          },
        ]),
      ),
      { now: NOW, content: CURRENT_CONTENT },
    );
    expect(stale.blocks.find((block) => block.kind === "ITEM")).toMatchObject({
      kind: "ITEM",
      item: { status: "STALE" },
    });
    expect(
      stale.blocks.some(
        (block) =>
          block.kind === "DETAILS" &&
          block.lines.includes(
            "A matching source may have changed since it was indexed.",
          ),
      ),
    ).toBe(true);
    // A stale freshness bound is qualified, never presented as current.
    expect(stale.state).toBe("CLARIFY");
    expect(stale.decision.reason).toBe("email.search.stale");

    for (const input of [
      { resource_id: RESOURCE_A, source_version: null },
      { resource_id: RESOURCE_A, fresh_until: null },
    ]) {
      const unverified = decideEmailSearch(
        parseEmailSearchOutcome(emailSearchPayload([input])),
        { now: NOW, content: CURRENT_CONTENT },
      );
      expect(
        unverified.blocks.find((block) => block.kind === "ITEM"),
      ).toMatchObject({ kind: "ITEM", item: { status: "UNVERIFIED" } });
      expect(unverified.state).toBe("CLARIFY");
      expect(unverified.decision.reason).toBe("email.search.unverified");
      expect(
        unverified.blocks.some(
          (block) =>
            block.kind === "DETAILS" &&
            block.lines.includes(
              "A matching source may have changed since it was indexed.",
            ),
        ),
      ).toBe(true);
    }
  });

  it("refuses a response this runtime cannot verify", () => {
    const cases: unknown[] = [
      null,
      {},
      { ...emailSearchPayload([{ resource_id: RESOURCE_A }]), coverage: {} },
      emailSearchPayload([{ resource_id: "not-a-uuid" }]),
      emailSearchPayload([
        { resource_id: RESOURCE_A, source_type: "NEWS_STORY" as never },
      ]),
      emailSearchPayload([{ resource_id: RESOURCE_A }], {
        unavailable_modes: "SEMANTIC" as never,
      }),
    ];
    for (const payload of cases) {
      expect(() => parseEmailSearchOutcome(payload)).toThrow(
        /could not verify/i,
      );
    }
  });
});
