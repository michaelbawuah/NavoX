import { describe, expect, it } from "vitest";
import {
  parseIntentPlanEnvelope,
  parseUpstreamIntentPlan,
  planTurn,
} from "./planner";

const TURN_ID = "99999999-9999-4999-8999-999999999999";
const SESSION_ID = "55555555-5555-4555-8555-555555555555";
const RECENT_TURNS = [
  { turn_id: TURN_ID, question: "What did Sarah email me?" },
];

function upstreamPlan(intent: Record<string, unknown>) {
  return {
    version: 1,
    intents: [
      {
        route: "email.search",
        entity: { kind: "PERSON", value: "Sarah", confidence: 0.9 },
        time: { kind: "NONE", expression: null, confidence: 1 },
        reference: { kind: "NONE", ordinal: null },
        confidence: 0.8,
        requires_clarification: false,
        clarification: null,
        ...intent,
      },
    ],
  };
}

/** The exact envelope the SPEC-005 `/ai/assistant/intents` route returns. */
function pythonEnvelope(
  plan: unknown,
  overrides: Record<string, unknown> = {},
) {
  return {
    task_id: "aaaaaaaa-0000-4000-8000-000000000001",
    trace_id: "aaaaaaaa-0000-4000-8000-000000000002",
    plan,
    session_id: SESSION_ID,
    turn_sequence: 1,
    actions_executed: false,
    ...overrides,
  };
}

describe("bounded M1 planner", () => {
  it("keeps the operator's original wording for the owning service", () => {
    const plan = planTurn({ text: "  What am I missing today?  " });
    expect(plan.intents).toHaveLength(1);
    expect(plan.intents[0]?.question).toBe("What am I missing today?");
    expect(plan.intents[0]?.capability_id).toBe("today.read");
  });

  it("does not keyword-route; classification stays with SPEC-002", () => {
    for (const text of [
      "Delete all my files",
      "Book a flight",
      "What renewals are coming up?",
    ]) {
      const plan = planTurn({ text });
      expect(plan.intents).toHaveLength(1);
      expect(plan.intents[0]?.capability_id).toBe("today.read");
      expect(plan.intents[0]?.question).toBe(text);
    }
  });

  it("refuses empty text", () => {
    expect(() => planTurn({ text: "   " })).toThrow(/Question/i);
  });
});

describe("SPEC-005 intent bridge", () => {
  it("binds compound intents only to ordered exact user spans", () => {
    const first = upstreamPlan({}).intents[0];
    const second = {
      ...first,
      route: "weather.read",
      entity: { kind: "NONE", value: null, confidence: 1 },
    };
    const utterance = "What did Sarah email me and what's the weather today?";
    const plan = {
      version: 1,
      intents: [
        { ...first, question: "What did Sarah email me" },
        { ...second, question: "what's the weather today?" },
      ],
    };
    expect(
      parseUpstreamIntentPlan(plan, { utterance, recentTurns: [] }).intents.map(
        (intent) => intent.question,
      ),
    ).toEqual(["What did Sarah email me", "what's the weather today?"]);
    for (const invalid of [
      {
        ...plan,
        intents: [{ ...first, question: "What did Sarah email me" }, second],
      },
      {
        ...plan,
        intents: [
          { ...first, question: "What did Sarah email me" },
          { ...second, question: "What did Sarah email me" },
        ],
      },
      {
        ...plan,
        intents: [
          { ...first, question: "What did Sarah email me" },
          { ...second, question: "what's the weather tomorrow?" },
        ],
      },
    ]) {
      expect(() =>
        parseUpstreamIntentPlan(invalid, { utterance, recentTurns: [] }),
      ).toThrow();
    }
  });

  it("pins the operator's question and maps the route to an owned capability", () => {
    const plan = parseUpstreamIntentPlan(upstreamPlan({}), {
      utterance: "What did Sarah email me about the renewal?",
      recentTurns: RECENT_TURNS,
    });
    expect(plan.version).toBe(1);
    expect(plan.intents).toHaveLength(1);
    const [intent] = plan.intents;
    expect(intent?.kind).toBe("email.search");
    expect(intent?.capability_id).toBe("email.search");
    expect(intent?.question).toBe("What did Sarah email me about the renewal?");
    expect(intent?.entity).toEqual({
      kind: "PERSON",
      value: "Sarah",
      confidence: 0.9,
    });
    expect(intent?.reference).toEqual({
      kind: "NONE",
      ordinal: null,
      turn_id: null,
    });
    expect(intent?.requires_clarification).toBe(false);
  });

  it("accepts the time.now route in the current plan version", () => {
    const plan = parseUpstreamIntentPlan(
      upstreamPlan({
        route: "time.now",
        entity: { kind: "NONE", value: null, confidence: 1 },
        time: { kind: "RELATIVE", expression: "now", confidence: 0.9 },
      }),
      { utterance: "What time is it right now?", recentTurns: RECENT_TURNS },
    );
    expect(plan.intents[0]?.kind).toBe("time.now");
    expect(plan.intents[0]?.capability_id).toBe("time.now");
    expect(plan.intents[0]?.question).toBe("What time is it right now?");
  });

  it("binds a follow-up ordinal to a session-owned turn selector", () => {
    const plan = parseUpstreamIntentPlan(
      upstreamPlan({
        entity: { kind: "NONE", value: null, confidence: 0.4 },
        reference: { kind: "RECENT_TURN", ordinal: 1 },
      }),
      {
        utterance: "What about the second one?",
        recentTurns: RECENT_TURNS,
      },
    );
    expect(plan.intents[0]?.reference).toEqual({
      kind: "RECENT_TURN",
      ordinal: 1,
      turn_id: TURN_ID,
    });
  });

  it("refuses a follow-up the conversation does not own", () => {
    expect(() =>
      parseUpstreamIntentPlan(
        upstreamPlan({ reference: { kind: "RECENT_TURN", ordinal: 2 } }),
        { utterance: "What about that one?", recentTurns: RECENT_TURNS },
      ),
    ).toThrow(/does not own/i);
    expect(() =>
      parseUpstreamIntentPlan(
        upstreamPlan({ reference: { kind: "RECENT_TURN", ordinal: 1 } }),
        { utterance: "What about that one?", recentTurns: [] },
      ),
    ).toThrow(/does not own/i);
  });

  it("refuses a route, field or selector this runtime does not own", () => {
    const cases = [
      upstreamPlan({ route: "files.delete" }),
      upstreamPlan({ capability_id: "email.send" }),
      upstreamPlan({ tool_name: "gmail.send" }),
      upstreamPlan({ action_grant: { send: true } }),
      upstreamPlan({ workspace_id: "22222222-2222-4222-8222-222222222222" }),
      upstreamPlan({ user_id: "11111111-1111-4111-8111-111111111111" }),
      upstreamPlan({
        reference: { kind: "RECENT_TURN", ordinal: 1, turn_id: TURN_ID },
      }),
      { version: 2, intents: upstreamPlan({}).intents },
      { version: 1, intents: [] },
      {
        version: 1,
        intents: Array.from({ length: 5 }, () => upstreamPlan({}).intents[0]),
      },
    ];
    for (const payload of cases) {
      expect(() =>
        parseUpstreamIntentPlan(payload, {
          utterance: "Do the thing",
          recentTurns: RECENT_TURNS,
        }),
      ).toThrow(/planner|route|own/i);
    }
  });

  it("refuses unknown, oversized or incoherent slots", () => {
    const cases = [
      upstreamPlan({
        entity: { kind: "PLANET", value: "Mars", confidence: 1 },
      }),
      upstreamPlan({ entity: { kind: "NONE", value: "Sarah", confidence: 1 } }),
      upstreamPlan({ entity: { kind: "PERSON", value: null, confidence: 1 } }),
      upstreamPlan({
        entity: { kind: "PERSON", value: "x".repeat(201), confidence: 1 },
      }),
      upstreamPlan({
        time: { kind: "TOMORROW", expression: "then", confidence: 1 },
      }),
      upstreamPlan({ reference: { kind: "NONE", ordinal: 1 } }),
      upstreamPlan({ reference: { kind: "RECENT_TURN", ordinal: null } }),
      upstreamPlan({ reference: { kind: "RECENT_TURN", ordinal: 5 } }),
      upstreamPlan({ confidence: 2 }),
      upstreamPlan({
        route: "assistant.clarify",
        requires_clarification: false,
      }),
      upstreamPlan({ requires_clarification: true, clarification: "   " }),
      upstreamPlan({ clarification: "Which email?" }),
      upstreamPlan({
        clarification: "x".repeat(241),
        requires_clarification: true,
      }),
    ];
    for (const payload of cases) {
      expect(() =>
        parseUpstreamIntentPlan(payload, {
          utterance: "What did Sarah email me?",
          recentTurns: RECENT_TURNS,
        }),
      ).toThrow(
        /plan|intent|clarification|confidence|reference|entity|time|ordinal|bound/i,
      );
    }
  });

  it("accepts a grounded clarification plan without delegating anything", () => {
    const plan = parseUpstreamIntentPlan(
      upstreamPlan({
        route: "assistant.clarify",
        entity: { kind: "NONE", value: null, confidence: 1 },
        time: { kind: "NONE", expression: null, confidence: 1 },
        reference: { kind: "NONE", ordinal: null },
        requires_clarification: true,
        clarification: "Which Sarah do you mean?",
      }),
      { utterance: "What did Sarah email me?", recentTurns: RECENT_TURNS },
    );
    expect(plan.intents[0]?.kind).toBe("assistant.clarify");
    expect(plan.intents[0]?.capability_id).toBeNull();
    expect(plan.intents[0]?.clarification).toBe("Which Sarah do you mean?");
  });
});

describe("SPEC-005 response envelope", () => {
  it("unwraps the real Python response and binds the plan inside it", () => {
    const plan = parseIntentPlanEnvelope(
      pythonEnvelope(
        upstreamPlan({
          reference: { kind: "RECENT_TURN", ordinal: 1 },
        }),
      ),
      {
        utterance: "What about that one?",
        recentTurns: RECENT_TURNS,
        sessionId: SESSION_ID,
      },
    );
    expect(plan.intents[0]?.kind).toBe("email.search");
    expect(plan.intents[0]?.question).toBe("What about that one?");
    expect(plan.intents[0]?.reference.turn_id).toBe(TURN_ID);
  });

  it("refuses an envelope that claims an action result", () => {
    for (const overrides of [
      { actions_executed: true },
      { actions_executed: "false" },
      { actions_executed: undefined },
      { actions_executed: null },
    ]) {
      expect(() =>
        parseIntentPlanEnvelope(pythonEnvelope(upstreamPlan({}), overrides), {
          utterance: "Do the thing",
          recentTurns: RECENT_TURNS,
          sessionId: SESSION_ID,
        }),
      ).toThrow(/ran nothing|planner/i);
    }
  });

  it("refuses a session mismatch and a missing plan", () => {
    expect(() =>
      parseIntentPlanEnvelope(
        pythonEnvelope(upstreamPlan({}), {
          session_id: "66666666-6666-4666-8666-666666666666",
        }),
        {
          utterance: "Do the thing",
          recentTurns: RECENT_TURNS,
          sessionId: SESSION_ID,
        },
      ),
    ).toThrow(/another session/i);
    expect(() =>
      parseIntentPlanEnvelope(
        pythonEnvelope(upstreamPlan({}), { session_id: null }),
        {
          utterance: "Do the thing",
          recentTurns: RECENT_TURNS,
          sessionId: SESSION_ID,
        },
      ),
    ).toThrow(/another session/i);
    const missingPlan: Record<string, unknown> = pythonEnvelope(undefined);
    delete missingPlan.plan;
    expect(() =>
      parseIntentPlanEnvelope(missingPlan, {
        utterance: "Do the thing",
        recentTurns: RECENT_TURNS,
        sessionId: SESSION_ID,
      }),
    ).toThrow(/no plan/i);
  });

  it("refuses unknown authority fields and invalid identifiers", () => {
    for (const overrides of [
      { workspace_id: "22222222-2222-4222-8222-222222222222" },
      { user_id: "11111111-1111-4111-8111-111111111111" },
      { action_grant: { send: true } },
      { tool_name: "gmail.send" },
      { task_id: "not-a-uuid" },
      { trace_id: null },
      { turn_sequence: -1 },
      { turn_sequence: "1" },
    ]) {
      expect(() =>
        parseIntentPlanEnvelope(pythonEnvelope(upstreamPlan({}), overrides), {
          utterance: "Do the thing",
          recentTurns: RECENT_TURNS,
          sessionId: SESSION_ID,
        }),
      ).toThrow(/planner/i);
    }
    // A null turn position is valid for a plan that was not appended to a turn.
    expect(
      parseIntentPlanEnvelope(
        pythonEnvelope(upstreamPlan({}), { turn_sequence: null }),
        {
          utterance: "Do the thing",
          recentTurns: RECENT_TURNS,
          sessionId: SESSION_ID,
        },
      ).intents[0]?.kind,
    ).toBe("email.search");
  });

  it("still refuses an upstream plan that supplies its own turn selector", () => {
    expect(() =>
      parseIntentPlanEnvelope(
        pythonEnvelope(
          upstreamPlan({
            reference: { kind: "RECENT_TURN", ordinal: 1, turn_id: TURN_ID },
          }),
        ),
        {
          utterance: "What about that one?",
          recentTurns: RECENT_TURNS,
          sessionId: SESSION_ID,
        },
      ),
    ).toThrow(/turn selector/i);
  });
});
