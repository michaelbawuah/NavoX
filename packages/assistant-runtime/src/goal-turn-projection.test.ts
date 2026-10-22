import type {
  AssistantBlock,
  AssistantPresentationPlan,
  CapabilityDecision,
  IntentPlan,
} from "@navox/contracts";
import { describe, expect, it } from "vitest";
import {
  deriveTurnGoalProjection,
  parseTurnGoalProjection,
  type TurnGoalProjection,
  verifyProjectedTurnGoal,
} from "./goal-turn-projection";
import { GOAL_DETAIL } from "./goals";

const PRIVATE = "PRIVATE-content-that-must-never-enter-a-projection";
const ITEM_ID = "44444444-4444-4444-8444-444444444444";
const OTHER_ITEM_ID = "55555555-5555-4555-8555-555555555555";

function source() {
  const plan: IntentPlan = {
    version: 1,
    intents: [
      {
        kind: "today.read",
        capability_id: "today.read",
        question: PRIVATE,
        confidence: 1,
        entity: { kind: "NONE", value: null, confidence: 1 },
        time: { kind: "NONE", expression: null, confidence: 1 },
        reference: { kind: "NONE", ordinal: null, turn_id: null },
        requires_clarification: false,
        clarification: null,
      },
    ],
  };
  const decision: CapabilityDecision = {
    kind: "DELEGATE",
    capability_id: "today.read",
    target: "today.query",
    reason: "today.today",
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: "READY",
  };
  const presentation: AssistantPresentationPlan = {
    presentation: "TEXT",
    speak: false,
    speech_text: null,
    delivery: "AUTOMATIC",
    blocks: [{ kind: "ANSWER", text: PRIVATE }],
  };
  return { state: "READY", plan, decision, presentation };
}

function meetingBlocks(commitmentId = ITEM_ID): AssistantBlock[] {
  return [
    {
      kind: "ITEM",
      item: {
        id: ITEM_ID,
        type: "meeting",
        title: PRIVATE,
        description: PRIVATE,
        status: "open",
        due_at: "2026-10-22T12:30:00.000Z",
        band: null,
        sources: [],
      },
    },
    {
      kind: "MEETING_BRIEFING",
      meeting: {
        commitment_id: commitmentId,
        title: PRIVATE,
        starts_at: "2026-10-22T12:30:00.000Z",
        minutes_until: 30,
        description: PRIVATE,
        related_commitments: [],
        prep_points: [PRIVATE],
      },
    },
  ];
}

describe("content-free saved turn goal projections", () => {
  it("captures a grounded briefing without retaining any source content", () => {
    const input = source();
    const projection = deriveTurnGoalProjection(input);
    expect(projection).toEqual({
      contract_version: 1,
      classification: "BRIEFING",
    });
    expect(Object.keys(projection).sort()).toEqual([
      "classification",
      "contract_version",
    ]);
    expect(JSON.stringify(projection)).not.toContain(PRIVATE);
    expect(input.presentation.blocks).toEqual([
      { kind: "ANSWER", text: PRIVATE },
    ]);
  });

  it("requires the meeting briefing to reference an item in that same source", () => {
    const input = source();
    input.presentation.blocks = meetingBlocks();
    expect(deriveTurnGoalProjection(input)).toEqual({
      contract_version: 1,
      classification: "MEETING_PREP",
    });
    expect(JSON.stringify(deriveTurnGoalProjection(input))).not.toContain(
      PRIVATE,
    );
    input.presentation.blocks = meetingBlocks(OTHER_ITEM_ID);
    expect(deriveTurnGoalProjection(input).classification).toBe("NOT_GROUNDED");
  });

  it("retains the existing distinction between not grounded and unreadable", () => {
    expect(
      deriveTurnGoalProjection({ ...source(), state: "UNAVAILABLE" })
        .classification,
    ).toBe("NOT_GROUNDED");
    expect(
      deriveTurnGoalProjection({ ...source(), decision: null }).classification,
    ).toBe("UNREADABLE");
  });

  it.each(["CLARIFY", "UNAVAILABLE", "WITHHELD", "READY-with-private-suffix"])(
    "does not attest to a source in state %s",
    (state) => {
      expect(
        deriveTurnGoalProjection({ ...source(), state }).classification,
      ).toBe("NOT_GROUNDED");
    },
  );

  it.each([
    { kind: "UNAVAILABLE" },
    { capability_id: "email.search" },
    { target: "search.query" },
    { reason: PRIVATE },
    { response_state: "UNAVAILABLE" },
    { requires_approval: true },
    { action_state: "EXECUTED" },
    { action_id: ITEM_ID },
  ])("does not certify a mismatched authority decision %j", (change) => {
    const input = source();
    expect(
      deriveTurnGoalProjection({
        ...input,
        decision: { ...input.decision, ...change },
      }).classification,
    ).toBe("NOT_GROUNDED");
  });

  it.each([
    { version: 99, intents: [] },
    { version: 1, intents: PRIVATE },
    { version: 1, intents: [{ kind: "execute-any-tool" }] },
    null,
    [],
  ])("classifies malformed intent plans as unreadable", (plan) => {
    expect(deriveTurnGoalProjection({ ...source(), plan }).classification).toBe(
      "UNREADABLE",
    );
  });

  it.each([
    { presentation: "TEXT", blocks: PRIVATE },
    {
      presentation: "TEXT",
      speak: false,
      speech_text: null,
      blocks: [{ kind: "RUN_ARBITRARY_CODE", text: PRIVATE }],
    },
    null,
    [PRIVATE],
  ])(
    "classifies malformed presentation payloads as unreadable",
    (presentation) => {
      expect(
        deriveTurnGoalProjection({ ...source(), presentation }).classification,
      ).toBe("UNREADABLE");
    },
  );

  it.each([
    { kind: PRIVATE },
    { requires_approval: "false" },
    { reason: { private: PRIVATE } },
    { action_id: PRIVATE },
  ])("never includes a malformed decision in its classification", (change) => {
    const input = source();
    const projection = deriveTurnGoalProjection({
      ...input,
      decision: { ...input.decision, ...change },
    });
    expect(projection.classification).toBe("UNREADABLE");
    expect(JSON.stringify(projection)).not.toContain(PRIVATE);
  });

  it("does not certify clarified or combined plans as a single grounded read", () => {
    const input = source();
    const intent = input.plan.intents[0];
    const clarified = {
      ...intent,
      requires_clarification: true,
      clarification: "Which day?",
    };
    expect(
      deriveTurnGoalProjection({
        ...input,
        plan: { version: 1, intents: [clarified] },
      }).classification,
    ).toBe("NOT_GROUNDED");
    expect(
      deriveTurnGoalProjection({
        ...input,
        plan: { version: 1, intents: [intent, intent] },
      }).classification,
    ).toBe("NOT_GROUNDED");
  });

  it("does not promote notices without an answer or item", () => {
    const input = source();
    input.presentation.blocks = [
      { kind: "NOTICE", state: "UNAVAILABLE", text: PRIVATE },
    ];
    expect(deriveTurnGoalProjection(input).classification).toBe("NOT_GROUNDED");
  });

  it("does not expose a throwing source accessor's private exception", () => {
    const input = source();
    Object.defineProperty(input, "decision", {
      get() {
        throw new Error(PRIVATE);
      },
    });
    expect(deriveTurnGoalProjection(input)).toEqual({
      contract_version: 1,
      classification: "UNREADABLE",
    });
  });
});

describe("projection reader", () => {
  const classifications: TurnGoalProjection["classification"][] = [
    "BRIEFING",
    "MEETING_PREP",
    "NOT_GROUNDED",
    "UNREADABLE",
  ];

  it.each(classifications)(
    "reads the exact v1 %s contract",
    (classification) => {
      const input = { contract_version: 1, classification };
      const parsed = parseTurnGoalProjection(input);
      expect(parsed).toEqual(input);
      expect(parsed).not.toBe(input);
    },
  );

  it.each([
    null,
    undefined,
    PRIVATE,
    [],
    {},
    { contract_version: 1 },
    { contract_version: "1", classification: "BRIEFING" },
    { contract_version: 2, classification: "BRIEFING" },
    { contract_version: 1, classification: "COMPLETED" },
    { contract_version: 1, classification: PRIVATE },
    { contract_version: 1, classification: "BRIEFING", source: PRIVATE },
    {
      contract_version: 1,
      classification: "BRIEFING",
      [Symbol("private")]: PRIVATE,
    },
  ])(
    "rejects unknown fields, versions and malformed stored values",
    (value) => {
      expect(() => parseTurnGoalProjection(value)).toThrow(
        "Stored goal verification is unavailable.",
      );
      try {
        parseTurnGoalProjection(value);
      } catch (error) {
        expect(String(error)).not.toContain(PRIVATE);
      }
    },
  );

  it("rejects inherited fields and accessors without evaluating them", () => {
    expect(() =>
      parseTurnGoalProjection(
        Object.create({ contract_version: 1, classification: "BRIEFING" }),
      ),
    ).toThrow();
    let reads = 0;
    const value = {
      contract_version: 1,
      get classification() {
        reads += 1;
        throw new Error(PRIVATE);
      },
    };
    expect(() => parseTurnGoalProjection(value)).toThrow(
      "Stored goal verification is unavailable.",
    );
    expect(reads).toBe(0);
  });
});

describe("projection-only turn goal verification", () => {
  it.each(["BRIEFING", "MEETING_PREP"] as const)(
    "completes only the matching %s turn goal",
    (kind) => {
      expect(
        verifyProjectedTurnGoal(kind, {
          contract_version: 1,
          classification: kind,
        }),
      ).toEqual({
        available: true,
        outcome: {
          status: "COMPLETED",
          detail: GOAL_DETAIL.verified,
          completed: true,
        },
      });
    },
  );

  it.each([
    ["BRIEFING", "MEETING_PREP"],
    ["MEETING_PREP", "BRIEFING"],
    ["BRIEFING", "NOT_GROUNDED"],
    ["COMMUNICATION_ACTION", "BRIEFING"],
    ["COMMUNICATION_ACTION", "NOT_GROUNDED"],
  ] as const)(
    "does not verify goal %s from classification %s",
    (kind, classification) => {
      expect(
        verifyProjectedTurnGoal(kind, { contract_version: 1, classification }),
      ).toEqual({
        available: true,
        outcome: {
          status: "FAILED",
          detail: GOAL_DETAIL.turnNotReady,
          completed: false,
        },
      });
    },
  );

  it.each([
    null,
    undefined,
    { contract_version: 2, classification: "BRIEFING" },
    { contract_version: 1, classification: "BRIEFING", private: PRIVATE },
  ])(
    "leaves missing or invalid evidence unavailable without a failure assertion",
    (value) => {
      expect(verifyProjectedTurnGoal("BRIEFING", value)).toEqual({
        available: false,
        outcome: null,
      });
    },
  );

  it("distinguishes a captured unreadable source from unavailable proof", () => {
    expect(
      verifyProjectedTurnGoal("BRIEFING", {
        contract_version: 1,
        classification: "UNREADABLE",
      }),
    ).toEqual({
      available: true,
      outcome: {
        status: "FAILED",
        detail: GOAL_DETAIL.turnUnreadable,
        completed: false,
      },
    });
  });
});
