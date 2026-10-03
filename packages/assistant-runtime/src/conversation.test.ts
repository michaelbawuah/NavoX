import { describe, expect, it, vi } from "vitest";
import {
  conversationReferences,
  parseConversationAnswer,
} from "./conversation";
import { AssistantError } from "./errors";
import { createNavoxUpstream, type FetchLike } from "./gateway";
import { createAssistantRuntime } from "./runtime";
import {
  CLARIFY_PLAN,
  createFakeUpstream,
  createMemoryStore,
  intentEnvelope,
  NAVOX_SESSION_ID,
  OTHER_SCOPE,
  REQUEST_ID,
  UNCONFIGURED_PLANNER,
} from "./testing/fakes";

const COOKIE = "navox_session=test";
const NOW = new Date("2026-10-03T16:00:00.000Z");
const TASK_ID = "aaaaaaaa-0000-4000-8000-000000000003";

function envelope(
  answer = "Photosynthesis lets plants use sunlight to make food.",
) {
  return {
    task_id: TASK_ID,
    trace_id: "aaaaaaaa-0000-4000-8000-000000000004",
    session_id: NAVOX_SESSION_ID,
    turn_sequence: 1,
    answer,
    actions_executed: false,
  };
}

async function setup() {
  const store = createMemoryStore();
  const upstream = createFakeUpstream({
    plan: intentEnvelope(CLARIFY_PLAN),
    planError: null,
    conversation: envelope(),
    conversationError: null,
  });
  let nextId = 0;
  const runtime = createAssistantRuntime({
    store,
    upstream,
    now: () => NOW,
    newId: () =>
      `00000000-0000-4000-8000-${String(++nextId).padStart(12, "0")}`,
  });
  const session = await runtime.createSession({ cookie: COOKIE });
  const submit = (text = "Explain photosynthesis", requestId = REQUEST_ID) =>
    runtime.submitTurn({
      cookie: COOKIE,
      session_id: session.id,
      body: { request_id: requestId, text, modality: "TEXT" },
    });
  return { store, upstream, runtime, session, submit };
}

describe("ordinary conversation in the saved assistant", () => {
  it("presents a verified model answer, with no capability or action delegation", async () => {
    const { submit, upstream } = await setup();
    const response = await submit();
    expect(response.turn.state).toBe("READY");
    expect(response.turn.plan?.intents).toHaveLength(1);
    expect(response.turn.plan?.intents[0]?.kind).toBe("assistant.delivery");
    expect(response.turn.decision).toMatchObject({
      kind: "PRESENT",
      reason: `conversation.answer:${TASK_ID}`,
      capability_id: null,
      target: null,
      requires_approval: false,
      action_state: "NONE",
      action_id: null,
    });
    expect(response.turn.presentation?.blocks).toEqual([
      { kind: "ANSWER", text: envelope().answer },
    ]);
    expect(response.turn.action_refs).toEqual([]);
    expect(upstream.calls.conversation).toEqual([
      {
        cookie: COOKIE,
        utterance: "Explain photosynthesis",
        sessionId: NAVOX_SESSION_ID,
        recentTurns: [],
      },
    ]);
    expect(upstream.calls.today).toEqual([]);
    expect(upstream.calls.email).toEqual([]);
    expect(upstream.calls.actions).toEqual([]);
    const replay = await submit();
    expect(replay.turn.id).toBe(response.turn.id);
    expect(upstream.calls.conversation).toHaveLength(1);
  });

  it.each([
    { actions_executed: true },
    { actions_executed: "false" },
    { session_id: "bbbbbbbb-0000-4000-8000-000000000003" },
    { task_id: "unverified" },
    { trace_id: null },
    { turn_sequence: 0 },
    { turn_sequence: 1.5 },
    { answer: " " },
    { answer: "x".repeat(3001) },
    { answer: "Unsafe\u0000text" },
    { capability_id: "email.search" },
    { target: { user_id: "foreign" } },
  ])("refuses an invalid or authoritative response %j", async (change) => {
    const { submit, upstream } = await setup();
    upstream.conversation = { ...envelope(), ...change };
    const result = await submit();
    expect(result.turn.state).toBe("UNAVAILABLE");
    expect(result.turn.decision?.kind).toBe("REFUSED");
    expect(result.turn.action_refs).toEqual([]);
    expect(
      result.turn.presentation?.blocks.some((block) => block.kind === "ANSWER"),
    ).toBe(false);
    expect(upstream.calls.email).toEqual([]);
    expect(upstream.calls.today).toEqual([]);
  });

  it("preserves a validated clarification if the new route is not authorized", async () => {
    const { submit, upstream } = await setup();
    upstream.conversationError = new AssistantError(
      "forbidden",
      "No policy scope.",
    );
    const result = await submit();
    expect(result.turn.state).toBe("CLARIFY");
    expect(result.turn.decision?.reason).toBe("plan.clarify");
    expect(result.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "CLARIFY",
        text: CLARIFY_PLAN.intents[0]!.clarification,
      },
    ]);
  });

  it.each(["Thank you", "How are you doing?"])(
    "does not replace an unavailable model with a canned reply for %s",
    async (text) => {
      const { submit, upstream } = await setup();
      upstream.conversationError = new AssistantError(
        "unsupported",
        "Not enabled.",
      );
      const result = await submit(text);
      expect(result.turn.state).toBe("UNAVAILABLE");
      expect(result.turn.presentation?.blocks).toEqual([
        {
          kind: "NOTICE",
          state: "UNAVAILABLE",
          text: "NavoX conversation is temporarily unavailable. Please try again shortly.",
        },
      ]);
      expect(upstream.calls.plan).toEqual([]);
      expect(result.turn.action_refs).toEqual([]);
    },
  );

  it("gives a clear account message for a policy-denied social response", async () => {
    const { submit, upstream } = await setup();
    upstream.conversationError = new AssistantError(
      "forbidden",
      "No policy scope.",
    );
    const result = await submit("Thanks");
    expect(result.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "WITHHELD",
        text: "NavoX conversation isn't available for this account yet.",
      },
    ]);
  });

  it("never uses conversation to bypass an invalid planner envelope", async () => {
    const { submit, upstream } = await setup();
    upstream.plan = intentEnvelope(CLARIFY_PLAN, { actions_executed: true });
    const result = await submit();
    expect(result.turn.decision?.kind).toBe("REFUSED");
    expect(upstream.calls.conversation).toEqual([]);
  });

  it("can use the separately authorized conversation route when no planner is qualified", async () => {
    const { submit, upstream } = await setup();
    upstream.planError = UNCONFIGURED_PLANNER;
    const result = await submit();
    expect(result.turn.state).toBe("READY");
    expect(result.turn.decision?.reason).toBe(`conversation.answer:${TASK_ID}`);
    expect(upstream.calls.today).toHaveLength(1);
    expect(upstream.calls.conversation).toHaveLength(1);
    expect(result.turn.action_refs).toEqual([]);
  });

  it("keeps the exact Today fallback ahead of ordinary conversation", async () => {
    const { submit, upstream } = await setup();
    upstream.planError = UNCONFIGURED_PLANNER;
    upstream.today = {
      intent: "today",
      answer: "No saved item currently needs your attention.",
      items: [],
      supported_queries: ["What am I missing today?"],
      details: [],
    };
    const result = await submit("What am I missing today?");
    expect(result.turn.state).toBe("READY");
    expect(upstream.calls.conversation).toEqual([]);
  });

  it("does not treat a planner outage as permission to call another model route", async () => {
    const { submit, upstream } = await setup();
    upstream.planError = new AssistantError("unavailable", "Transport outage.");
    const result = await submit();
    expect(result.turn.state).toBe("UNAVAILABLE");
    expect(upstream.calls.conversation).toEqual([]);
    expect(upstream.calls.today).toEqual([]);
  });

  it("does not consume a named clarification or an unresolved prior-turn reference", async () => {
    for (const slots of [
      { entity: { kind: "PERSON", value: "Sarah", confidence: 1 } },
      { reference: { kind: "RECENT_TURN", ordinal: 1, turn_id: null } },
    ]) {
      const { submit, upstream } = await setup();
      upstream.plan = intentEnvelope({
        version: 1,
        intents: [{ ...CLARIFY_PLAN.intents[0], ...slots }],
      });
      await submit("What should Sarah do next?");
      expect(upstream.calls.conversation).toEqual([]);
    }
  });

  it("uses a bound follow-up only when its source is a verified conversation answer", async () => {
    const { submit, upstream } = await setup();
    await submit();
    upstream.plan = intentEnvelope({
      version: 1,
      intents: [
        {
          ...CLARIFY_PLAN.intents[0],
          reference: { kind: "RECENT_TURN", ordinal: 1, turn_id: null },
        },
      ],
    });
    upstream.conversation = {
      ...envelope("Plants use sunlight to make food."),
      task_id: "aaaaaaaa-0000-4000-8000-000000000005",
      turn_sequence: 2,
    };
    const result = await submit(
      "Make it shorter",
      "bbbbbbbb-0000-4000-8000-000000000006",
    );
    expect(result.turn.state).toBe("READY");
    expect(upstream.calls.conversation.at(-1)?.recentTurns).toEqual([
      {
        task_id: TASK_ID,
        question: "Explain photosynthesis",
        answer: envelope().answer,
      },
    ]);
    expect(result.turn.presentation?.blocks).toEqual([
      { kind: "ANSWER", text: "Plants use sunlight to make food." },
    ]);
  });

  it("never forwards a referenced private email answer to ordinary conversation", async () => {
    const { submit, upstream, store } = await setup();
    await submit();
    const privateTurn = store.turns[0]!;
    privateTurn.decision = {
      ...privateTurn.decision,
      reason: "email.read",
      capability_id: "email.search",
    };
    privateTurn.question = "What did Sarah send me?";
    privateTurn.response_text = "Private email content.";
    privateTurn.presentation = {
      ...privateTurn.presentation,
      blocks: [{ kind: "ANSWER", text: "Private email content." }],
    };
    upstream.plan = intentEnvelope({
      version: 1,
      intents: [
        {
          ...CLARIFY_PLAN.intents[0],
          reference: { kind: "RECENT_TURN", ordinal: 1, turn_id: null },
        },
      ],
    });
    const result = await submit(
      "Make it shorter",
      "bbbbbbbb-0000-4000-8000-000000000006",
    );
    expect(result.turn.state).toBe("CLARIFY");
    expect(upstream.calls.conversation).toHaveLength(1);
    expect(conversationReferences(store.turns, privateTurn.session_id)).toEqual(
      [],
    );
  });

  it("does not partially answer a mixed request through the ordinary route", async () => {
    const { submit, upstream } = await setup();
    upstream.plan = intentEnvelope({
      version: 1,
      intents: [
        { ...CLARIFY_PLAN.intents[0], question: "Explain photosynthesis" },
        { ...CLARIFY_PLAN.intents[0], question: "send it to Sarah" },
      ],
    });
    const result = await submit("Explain photosynthesis and send it to Sarah");
    expect(result.turn.state).toBe("CLARIFY");
    expect(upstream.calls.conversation).toEqual([]);
    expect(result.turn.action_refs).toEqual([]);
  });

  it("rechecks account ownership before calling the model", async () => {
    const { submit, upstream } = await setup();
    upstream.plan = async () => {
      upstream.account = { ...OTHER_SCOPE, email: "other@example.com" };
      return intentEnvelope(CLARIFY_PLAN);
    };
    const result = await submit();
    expect(result.turn.state).toBe("WITHHELD");
    expect(upstream.calls.conversation).toEqual([]);
  });

  it("discards the answer if the signed-in account changes during its generation", async () => {
    const { submit, upstream } = await setup();
    upstream.conversation = async () => {
      upstream.account = { ...OTHER_SCOPE, email: "other@example.com" };
      return envelope("This answer must not be shown.");
    };
    const result = await submit();
    expect(result.turn.state).toBe("WITHHELD");
    expect(
      result.turn.presentation?.blocks.some((block) => block.kind === "ANSWER"),
    ).toBe(false);
  });

  it("does not forward another account's saved conversation", async () => {
    const { submit, upstream } = await setup();
    upstream.account = { ...OTHER_SCOPE, email: "other@example.com" };
    await expect(submit()).rejects.toMatchObject({ code: "not_found" });
    expect(upstream.calls.plan).toEqual([]);
    expect(upstream.calls.conversation).toEqual([]);
  });

  it("forwards only the last four verified plain conversation answers in this session", async () => {
    const { submit, upstream, store, session } = await setup();
    for (let index = 1; index <= 5; index += 1) {
      upstream.conversation = {
        ...envelope(`Explanation ${index}.`),
        task_id: `aaaaaaaa-0000-4000-8000-${String(index).padStart(12, "0")}`,
        turn_sequence: index,
      };
      await submit(
        `Explain example ${index}`,
        `bbbbbbbb-0000-4000-8000-${String(index).padStart(12, "0")}`,
      );
    }
    const source = store.turns[0]!;
    store.turns.push({
      ...source,
      id: "cccccccc-0000-4000-8000-000000000001",
      sequence: 6,
      question: "Private email question",
      response_text: "Private email contents",
      decision: { ...source.decision, reason: "email.read" },
      presentation: {
        ...source.presentation,
        blocks: [{ kind: "ANSWER", text: "Private email contents" }],
      },
    });
    store.turns.push({
      ...source,
      session_id: "cccccccc-0000-4000-8000-000000000002",
      sequence: 7,
    });
    store.turns.push({
      ...source,
      sequence: 8,
      presentation: {
        ...source.presentation,
        blocks: [
          { kind: "NOTICE", state: "UNAVAILABLE", text: "Evidence details" },
        ],
      },
    });
    const refs = conversationReferences(store.turns, session.id);
    expect(refs).toEqual(
      [2, 3, 4, 5].map((index) => ({
        task_id: `aaaaaaaa-0000-4000-8000-${String(index).padStart(12, "0")}`,
        question: `Explain example ${index}`,
        answer: `Explanation ${index}.`,
      })),
    );
    store.turns.splice(5);
    await submit(
      "Make the last explanation shorter",
      "bbbbbbbb-0000-4000-8000-000000000006",
    );
    expect(upstream.calls.conversation.at(-1)?.recentTurns).toEqual(refs);
  });
});

describe("conversation API transport", () => {
  it("uses the existing authenticated session and exact bounded-context contract", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify(envelope())),
    ) as unknown as FetchLike;
    const upstream = createNavoxUpstream({
      baseUrl: "https://navox.example/api/v1",
      fetchImpl,
    });
    const recentTurns = [
      {
        task_id: TASK_ID,
        question: "Explain photosynthesis",
        answer: envelope().answer,
      },
    ];
    await upstream.answerConversation(COOKIE, {
      sessionId: NAVOX_SESSION_ID,
      utterance: "Make it shorter",
      recentTurns,
    });
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0]!;
    expect(url).toBe("https://navox.example/api/v1/ai/assistant/conversation");
    expect(init.headers.cookie).toBe(COOKIE);
    expect(init.cache).toBe("no-store");
    expect(JSON.parse(init.body)).toEqual({
      session_id: NAVOX_SESSION_ID,
      utterance: "Make it shorter",
      recent_turns: recentTurns,
    });
    expect(parseConversationAnswer(envelope(), NAVOX_SESSION_ID).task_id).toBe(
      TASK_ID,
    );
  });
});
