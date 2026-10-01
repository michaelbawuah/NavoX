import { describe, expect, it } from "vitest";
import { decideEmailSearch, parseEmailSearchOutcome } from "./email";
import { emailSearchPayload } from "./testing/fakes";

const NOW = new Date("2026-09-30T12:00:00.000Z");
const RESOURCE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RESOURCE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function outcome(payload: unknown) {
  return parseEmailSearchOutcome(payload);
}

describe("SPEC-007 email resolution", () => {
  it("resolves one exact match without copying evidence text", () => {
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
    const decision = decideEmailSearch(parsed, { now: NOW });
    expect(decision.state).toBe("READY");
    expect(decision.decision.kind).toBe("DELEGATE");
    expect(decision.decision.capability_id).toBe("email.search");
    expect(decision.decision.requires_approval).toBe(false);
    expect(decision.decision.action_state).toBe("NONE");
    expect(decision.blocks.map((block) => block.kind)).toEqual([
      "ANSWER",
      "ITEM",
      "CITATIONS",
    ]);
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
    expect(JSON.stringify(decision.blocks)).not.toContain("excerpt text");
    expect(JSON.stringify(decision.blocks)).not.toContain("canonical_url");
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

  it("labels freshness from the owning service's own metadata", () => {
    const stale = decideEmailSearch(
      parseEmailSearchOutcome(
        emailSearchPayload([
          {
            resource_id: RESOURCE_A,
            fresh_until: "2026-09-01T00:00:00.000Z",
          },
        ]),
      ),
      { now: NOW },
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
    // A stale but complete, unique match is still a grounded answer.
    expect(stale.state).toBe("READY");

    for (const input of [
      { resource_id: RESOURCE_A, source_version: null },
      { resource_id: RESOURCE_A, fresh_until: null },
    ]) {
      const unverified = decideEmailSearch(
        parseEmailSearchOutcome(emailSearchPayload([input])),
        { now: NOW },
      );
      expect(
        unverified.blocks.find((block) => block.kind === "ITEM"),
      ).toMatchObject({ kind: "ITEM", item: { status: "UNVERIFIED" } });
      expect(unverified.state).toBe("READY");
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
