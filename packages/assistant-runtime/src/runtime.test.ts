import { describe, expect, it, vi } from "vitest";
import { parseEmailDraft } from "./email-actions";
import { AssistantError } from "./errors";
import { createNavoxUpstream } from "./gateway";
import { LIMITS } from "./limits";
import { createAssistantRuntime } from "./runtime";
import {
  CLARIFY_PLAN,
  createFakeUpstream,
  createMemoryStore,
  emailSearchPayload,
  intentEnvelope,
  NAVOX_SESSION_ID,
  OTHER_SCOPE,
  REQUEST_ID,
  SCOPE,
} from "./testing/fakes";
import type { TodayQueryResult } from "./today";
import { spokenTextForTurn } from "./voice";

const NOW = new Date("2026-09-30T12:00:00.000Z");
const COOKIE = "navox_session=abc123";

function ids() {
  let counter = 0;
  return () => {
    counter += 1;
    return `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`;
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

async function waitUntil(predicate: () => boolean, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("condition was never reached");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const DEFAULT_TODAY: TodayQueryResult = {
  intent: "today",
  answer: "1 item needs attention now.",
  items: [],
  supported_queries: [
    "What am I missing today?",
    "What needs my attention?",
    "What is today?",
  ],
  details: [],
};

interface SetupOverrides {
  today?: TodayQueryResult;
  todayError?: AssistantError | null;
  plan?: unknown;
  planError?: AssistantError | null;
  email?: unknown;
  emailError?: AssistantError | null;
}

function setup(overrides?: SetupOverrides) {
  const store = createMemoryStore();
  const fake: Parameters<typeof createFakeUpstream>[0] = {
    today: overrides?.today ?? DEFAULT_TODAY,
    todayError: overrides?.todayError ?? null,
  };
  if (overrides && "plan" in overrides) fake.plan = overrides.plan;
  if (overrides && "planError" in overrides)
    fake.planError = overrides.planError ?? null;
  if (overrides && "email" in overrides) fake.email = overrides.email;
  if (overrides && "emailError" in overrides)
    fake.emailError = overrides.emailError ?? null;
  const upstream = createFakeUpstream(fake);
  const runtime = createAssistantRuntime({
    store,
    upstream,
    now: () => NOW,
    newId: ids(),
  });
  return { store, upstream, runtime };
}

async function withSession(overrides?: SetupOverrides) {
  const context = setup(overrides);
  const session = await context.runtime.createSession({ cookie: COOKIE });
  return { ...context, sessionId: session.id };
}

describe("assistant session lifecycle", () => {
  it("creates the SPEC-005 session first and persists only its scope", async () => {
    const { runtime, store, upstream } = setup();
    const session = await runtime.createSession({ cookie: COOKIE });
    expect(upstream.calls.sessions).toEqual([COOKIE]);
    expect(store.sessions).toHaveLength(1);
    expect(store.sessions[0]?.navox_session_id).toBe(NAVOX_SESSION_ID);
    expect(store.sessions[0]?.workspace_id).toBe(SCOPE.workspace_id);
    expect(store.sessions[0]?.user_id).toBe(SCOPE.user_id);
    expect(session.turns).toEqual([]);
    expect(session.status).toBe("active");
    expect(session.expires_at).toBe("2026-10-30T12:00:00.000Z");
  });

  it("refuses a session that belongs to another workspace", async () => {
    const context = await withSession();
    const session = context.store.sessions[0];
    expect(session).toBeDefined();
    if (session) session.workspace_id = OTHER_SCOPE.workspace_id;
    await expect(
      context.runtime.readSession({
        cookie: COOKIE,
        session_id: context.sessionId,
      }),
    ).rejects.toMatchObject({ code: "not_found" });
  });

  it("refuses an expired session and a deleted session", async () => {
    const context = await withSession();
    const record = context.store.sessions[0];
    if (record) record.expires_at = "2026-09-01T00:00:00.000Z";
    await expect(
      context.runtime.readSession({
        cookie: COOKIE,
        session_id: context.sessionId,
      }),
    ).rejects.toMatchObject({ code: "expired" });

    const fresh = await withSession();
    await fresh.runtime.deleteSession({
      cookie: COOKIE,
      session_id: fresh.sessionId,
    });
    expect(fresh.store.sessions).toHaveLength(0);
    await expect(
      fresh.runtime.readSession({
        cookie: COOKIE,
        session_id: fresh.sessionId,
      }),
    ).rejects.toMatchObject({ code: "not_found" });
    await expect(
      fresh.runtime.deleteSession({
        cookie: COOKIE,
        session_id: fresh.sessionId,
      }),
    ).rejects.toMatchObject({ code: "not_found" });
  });

  it("purges expired rows on demand", async () => {
    const context = await withSession();
    const record = context.store.sessions[0];
    if (record) record.expires_at = "2026-09-01T00:00:00.000Z";
    await expect(context.runtime.purgeExpired()).resolves.toBe(1);
    expect(context.store.sessions).toHaveLength(0);
  });
});

describe("assistant email action boundary", () => {
  it("uses the current version at the end of the service's ascending history", () => {
    const parsed = parseEmailDraft({
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      binding_kind: "KNOWLEDGE_EMAIL",
      commitment_id: null,
      source_id: EMAIL_RESULT,
      current_version: 2,
      status: "review",
      action_id: null,
      versions: [
        {
          version: 1,
          to: ["sarah@example.com"],
          subject: "Old",
          body: "Old text",
          created_by: "AI",
          payload_hash: "a".repeat(64),
        },
        {
          version: 2,
          to: ["sarah@example.com"],
          subject: "New",
          body: "New text",
          created_by: "USER",
          payload_hash: "b".repeat(64),
        },
      ],
    });
    expect(parsed.versions.at(-1)?.subject).toBe("New");
  });
  async function readyEmail() {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: emailSearchPayload([{ resource_id: EMAIL_RESULT }]),
    });
    const answer = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    expect(answer.turn.state).toBe("READY");
    return { ...context, turnId: answer.turn.id };
  }

  it("requires a session-owned resolved email before invoking draft generation", async () => {
    const context = await readyEmail();
    const generate = vi.fn(async () => ({}));
    context.upstream.createKnowledgeEmailDraft = generate;
    await expect(
      context.runtime.createEmailDraft({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          source_turn_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          instructions: "Reply",
        },
      }),
    ).rejects.toMatchObject({ code: "not_found" });
    expect(generate).not.toHaveBeenCalled();
  });

  it("binds exact draft review and approval to the source, version and hash", async () => {
    const context = await readyEmail();
    const draftId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const actionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const draft = {
      id: draftId,
      binding_kind: "KNOWLEDGE_EMAIL",
      commitment_id: null,
      source_id: EMAIL_RESULT,
      current_version: 1,
      status: "awaiting_approval",
      action_id: actionId,
      versions: [
        {
          version: 1,
          to: ["sarah@example.com"],
          subject: "Re: Renewal",
          body: "Thanks, Sarah.",
          created_by: "AI",
          payload_hash: "a".repeat(64),
        },
      ],
    };
    const action = {
      id: actionId,
      status: "awaiting_approval",
      payload_hash: "b".repeat(64),
      result: {},
      payload: {
        sender: "me@example.com",
        to: "sarah@example.com",
        subject: "Re: Renewal",
        body_text: "Thanks, Sarah.",
        draft_id: draftId,
        draft_version: 1,
        connection_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        post_send_state: "unchanged",
        reply: { source_message_id: "mail-1", thread_id: "thread-1" },
      },
      approval: {
        status: "pending",
        expires_at: "2026-10-01T12:00:00Z",
        action_payload_hash: "b".repeat(64),
      },
    };
    context.upstream.getCommunicationDraft = vi.fn(async () => draft);
    context.upstream.getAction = vi.fn(async () => action);
    context.upstream.prepareCommunicationDraft = vi.fn(async () => action);
    context.store.actions.set(actionId, {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "awaiting_approval",
      executed_at: null,
      verified_at: null,
    });
    context.upstream.approveCommunicationDraft = vi.fn(async () => ({
      ...action,
      status: "approved",
      approval: { ...action.approval, status: "approved" },
    }));
    const prepared = await context.runtime.prepareEmailDraft({
      cookie: COOKIE,
      session_id: context.sessionId,
      draft_id: draftId,
      body: {
        source_turn_id: context.turnId,
        expected_version: 1,
        request_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      },
    });
    expect(prepared.payload.to).toBe("sarah@example.com");
    await expect(
      context.runtime.approveEmailDraft({
        cookie: COOKIE,
        session_id: context.sessionId,
        draft_id: draftId,
        body: {
          source_turn_id: context.turnId,
          request_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
          expected_payload_hash: "c".repeat(64),
          draft_version: 1,
          confirmed: true,
        },
      }),
    ).rejects.toMatchObject({ code: "conflict" });
    expect(context.upstream.approveCommunicationDraft).not.toHaveBeenCalled();
    const approved = await context.runtime.approveEmailDraft({
      cookie: COOKIE,
      session_id: context.sessionId,
      draft_id: draftId,
      body: {
        source_turn_id: context.turnId,
        request_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
        expected_payload_hash: prepared.payload_hash,
        draft_version: 1,
        confirmed: true,
      },
    });
    expect(approved.status).toBe("approved");
  });
});

describe("typed and voice turns", () => {
  it("shows structured meeting prep only when Today and Proactive agree on the exact meeting", async () => {
    const meetingId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const context = await withSession({
      today: {
        intent: "meeting_prep",
        answer: "One meeting is coming up.",
        items: [
          {
            id: meetingId,
            type: "meeting",
            title: "Project review",
            description: null,
            status: "confirmed",
            due_at: "2026-09-30T12:45:00Z",
            band: "briefing",
            sources: [],
          },
        ],
        supported_queries: ["Prepare me for my next meeting"],
        details: ["Review the plan"],
      },
    });
    context.upstream.meeting = {
      commitment_id: meetingId,
      title: "Project review",
      starts_at: "2026-09-30T12:45:00Z",
      minutes_until: 45,
      description: "Discuss the plan",
      related_commitments: [],
      prep_points: ["Review the plan"],
    };
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Prepare me for my next meeting",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.presentation?.blocks).toContainEqual({
      kind: "MEETING_BRIEFING",
      meeting: context.upstream.meeting,
    });
    expect(context.upstream.calls.meeting).toEqual([COOKIE]);
    expect(context.upstream.calls.plan).toHaveLength(1);
    context.upstream.meeting = {
      ...context.upstream.meeting,
      commitment_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    };
    const changed = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "Prepare me for my next meeting",
        modality: "TEXT",
      },
    });
    expect(changed.turn.state).toBe("UNAVAILABLE");
    expect(
      changed.turn.presentation?.blocks.some(
        (block) => block.kind === "MEETING_BRIEFING",
      ),
    ).toBe(false);
    expect(changed.turn.action_refs).toEqual([]);
    context.upstream.meetingError = new AssistantError(
      "unavailable",
      "Meeting source failed",
    );
    const failed = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777779",
        text: "Prepare me for my next meeting",
        modality: "TEXT",
      },
    });
    expect(failed.turn.state).toBe("UNAVAILABLE");
    expect(
      failed.turn.presentation?.blocks.some(
        (block) => block.kind === "MEETING_BRIEFING",
      ),
    ).toBe(false);
  });

  it("answers a typed question from Today without speaking", async () => {
    const context = await withSession();
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    expect(response.replay).toBe(false);
    expect(response.turn.state).toBe("READY");
    expect(response.turn.modality).toBe("TEXT");
    expect(response.turn.presentation?.speak).toBe(false);
    expect(response.turn.presentation?.speech_text).toBeNull();
    expect(response.turn.question).toBe("What am I missing today?");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.action_refs).toEqual([]);
    expect(context.upstream.calls.today[0]?.query).toBe(
      "What am I missing today?",
    );
  });

  it("shares one session between a typed and a clicked voice turn", async () => {
    const context = await withSession();
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    const voice = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "What needs my attention?",
        modality: "VOICE",
        timezone: "America/New_York",
      },
    });
    expect(voice.turn.modality).toBe("VOICE");
    expect(voice.turn.sequence).toBe(2);
    expect(voice.turn.presentation?.speak).toBe(true);
    expect(voice.turn.presentation?.speech_text).toBe(
      "1 item needs attention now.",
    );
    const session = await context.runtime.readSession({
      cookie: COOKIE,
      session_id: context.sessionId,
    });
    expect(session.turns.map((turn) => turn.sequence)).toEqual([1, 2]);
  });

  it("passes the optional IANA timezone through to Today", async () => {
    const context = await withSession();
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
        timezone: "Europe/Berlin",
      },
    });
    expect(context.upstream.calls.today[0]).toEqual({
      query: "What am I missing today?",
      timezone: "Europe/Berlin",
    });
  });

  it("keeps an unsupported question honest", async () => {
    const context = await withSession({
      today: {
        intent: "unsupported",
        answer: "I can answer read-only questions about today.",
        items: [],
        supported_queries: ["What needs my attention?"],
        details: [],
      },
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Book me a flight",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(
      response.turn.presentation?.blocks.map((block) => block.kind),
    ).toEqual(["NOTICE"]);
    expect(
      response.turn.presentation?.blocks.some((block) => block.kind === "ITEM"),
    ).toBe(false);
  });

  it("records an empty Today answer as READY with no invented item", async () => {
    const context = await withSession({
      today: {
        intent: "attention",
        answer: "Nothing currently needs your attention.",
        items: [],
        supported_queries: ["What needs my attention?"],
        details: [],
      },
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What needs my attention?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.presentation?.blocks).toEqual([
      { kind: "ANSWER", text: "Nothing currently needs your attention." },
    ]);
  });
});

describe("qualified failures", () => {
  it("keeps an upstream failure as UNAVAILABLE without a fabricated answer", async () => {
    const context = await withSession({
      todayError: new AssistantError("unavailable", "Today is down"),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(response.turn.decision?.reason).toBe("today.unavailable");
    const blocks = response.turn.presentation?.blocks ?? [];
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ kind: "NOTICE", state: "UNAVAILABLE" });
    expect(JSON.stringify(blocks)).not.toMatch(/attention now/i);
    expect(context.store.turns).toHaveLength(1);
  });

  it("keeps a permission change as WITHHELD", async () => {
    const context = await withSession({
      todayError: new AssistantError("forbidden", "Denied"),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("WITHHELD");
    expect(response.turn.presentation?.speak).toBe(false);
  });

  it("propagates a signed-out upstream and saves nothing", async () => {
    const { runtime, store, upstream } = setup();
    upstream.fetchAccount = async () => {
      throw new AssistantError("unauthorized", "Sign in");
    };
    await expect(runtime.createSession({ cookie: "" })).rejects.toMatchObject({
      code: "unauthorized",
    });
    expect(store.sessions).toHaveLength(0);
  });

  it("refuses client authority fields and bounded-input violations", async () => {
    const context = await withSession();
    await expect(
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "What am I missing today?",
          modality: "TEXT",
          workspace_id: OTHER_SCOPE.workspace_id,
        },
      }),
    ).rejects.toMatchObject({ code: "invalid_request" });
    await expect(
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "x".repeat(LIMITS.maxQuestionLength + 1),
          modality: "TEXT",
        },
      }),
    ).rejects.toMatchObject({ code: "invalid_request" });
    expect(context.store.turns).toHaveLength(0);
  });

  it("refuses to grow a session past its turn budget", async () => {
    const context = await withSession();
    const record = context.store.sessions[0];
    if (record) record.next_sequence = LIMITS.maxTurnsPerSession + 1;
    await expect(
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "What is today?",
          modality: "TEXT",
        },
      }),
    ).rejects.toMatchObject({ code: "unsupported" });
    expect(context.upstream.calls.today).toHaveLength(0);
  });

  it("denies a submit against another workspace's session", async () => {
    const context = await withSession();
    const record = context.store.sessions[0];
    if (record) record.workspace_id = OTHER_SCOPE.workspace_id;
    await expect(
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "What is today?",
          modality: "TEXT",
        },
      }),
    ).rejects.toMatchObject({ code: "not_found" });
    expect(context.upstream.calls.today).toHaveLength(0);
  });
});

describe("request idempotency", () => {
  it("replays an exact retry without calling Today twice", async () => {
    const context = await withSession();
    const body = {
      request_id: REQUEST_ID,
      text: "What is today?",
      modality: "TEXT" as const,
    };
    const first = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    const second = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    expect(second.replay).toBe(true);
    expect(second.turn.id).toBe(first.turn.id);
    expect(second.turn.sequence).toBe(first.turn.sequence);
    expect(context.upstream.calls.today).toHaveLength(1);
    expect(context.store.turns).toHaveLength(1);
  });

  it("still replays a saved request after the session reaches its turn limit", async () => {
    const context = await withSession();
    const body = {
      request_id: REQUEST_ID,
      text: "What is today?",
      modality: "TEXT" as const,
    };
    const first = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    const record = context.store.sessions[0];
    if (record) record.next_sequence = LIMITS.maxTurnsPerSession + 1;
    const replay = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    expect(replay.replay).toBe(true);
    expect(replay.turn.id).toBe(first.turn.id);
    expect(context.upstream.calls.today).toHaveLength(1);
  });

  it("refuses a changed payload behind the same request id", async () => {
    const context = await withSession();
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What is today?",
        modality: "TEXT",
      },
    });
    await expect(
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "What is tomorrow?",
          modality: "TEXT",
        },
      }),
    ).rejects.toMatchObject({ code: "conflict" });
    expect(context.store.turns).toHaveLength(1);
  });

  it("keeps one turn when the same request arrives concurrently", async () => {
    const context = await withSession();
    const body = {
      request_id: REQUEST_ID,
      text: "What is today?",
      modality: "TEXT" as const,
    };
    const [a, b] = await Promise.all([
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body,
      }),
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body,
      }),
    ]);
    expect(context.store.turns).toHaveLength(1);
    expect(a.turn.id).toBe(b.turn.id);
    expect([a.replay, b.replay].filter((replay) => replay)).toHaveLength(1);
  });
});

describe("durable per-request claims", () => {
  async function sessionWithDelayedToday() {
    const store = createMemoryStore();
    const gate = deferred<TodayQueryResult>();
    let todayCalls = 0;
    const upstream = createFakeUpstream({
      today: async () => {
        todayCalls += 1;
        return gate.promise;
      },
    });
    const runtime = createAssistantRuntime({
      store,
      upstream,
      now: () => NOW,
      newId: ids(),
    });
    const session = await runtime.createSession({ cookie: COOKIE });
    return {
      store,
      upstream,
      runtime,
      gate,
      todayCalls: () => todayCalls,
      sessionId: session.id,
    };
  }

  it("calls Today once for concurrent duplicates of a delayed request", async () => {
    const context = await sessionWithDelayedToday();
    const body = {
      request_id: REQUEST_ID,
      text: "What is today?",
      modality: "TEXT" as const,
    };
    const first = context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    await waitUntil(() => context.todayCalls() === 1);
    const second = context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    context.gate.resolve(DEFAULT_TODAY);
    const [owner, duplicate] = await Promise.all([first, second]);

    expect(context.todayCalls()).toBe(1);
    expect(context.store.turns).toHaveLength(1);
    expect(owner.turn.id).toBe(duplicate.turn.id);
    expect(owner.replay).toBe(false);
    expect(duplicate.replay).toBe(true);
  });

  it("refuses a changed payload for an in-flight id without another Today call", async () => {
    const context = await sessionWithDelayedToday();
    const first = context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What is today?",
        modality: "TEXT",
      },
    });
    await waitUntil(() => context.todayCalls() === 1);

    const failure = await context.runtime
      .submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "A different question",
          modality: "TEXT",
        },
      })
      .catch((error) => error);
    expect(failure).toBeInstanceOf(AssistantError);
    expect(failure.code).toBe("conflict");
    expect(context.todayCalls()).toBe(1);

    context.gate.resolve(DEFAULT_TODAY);
    await first;
    expect(context.store.turns).toHaveLength(1);
  });

  it("releases the claim when the owner fails before storing a turn", async () => {
    const context = await withSession({
      todayError: new AssistantError("unauthorized", "Sign in"),
    });
    await expect(
      context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "What is today?",
          modality: "TEXT",
        },
      }),
    ).rejects.toMatchObject({ code: "unauthorized" });
    expect(context.store.claims.size).toBe(0);
    expect(context.store.turns).toHaveLength(0);
  });

  it("takes over an abandoned claim after the stale window", async () => {
    const context = await withSession();
    context.store.claims.set(`${context.sessionId}:${REQUEST_ID}`, {
      request_id: REQUEST_ID,
      request_fingerprint: "f".repeat(64),
      status: "PENDING",
      turn_id: null,
      updated_at: "2020-01-01T00:00:00.000Z",
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What is today?",
        modality: "TEXT",
      },
    });
    expect(response.replay).toBe(false);
    expect(context.upstream.calls.today).toHaveLength(1);
    expect(context.store.turns).toHaveLength(1);
  });

  it("resolves the claim so later retries replay from the fast path", async () => {
    const context = await withSession();
    const body = {
      request_id: REQUEST_ID,
      text: "What is today?",
      modality: "TEXT" as const,
    };
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    const claim = context.store.claims.get(
      `${context.sessionId}:${REQUEST_ID}`,
    );
    expect(claim?.status).toBe("RESOLVED");
    expect(claim?.turn_id).toBe(context.store.turns[0]?.id);
    const replay = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body,
    });
    expect(replay.replay).toBe(true);
    expect(context.upstream.calls.today).toHaveLength(1);
  });
});

const UNSUPPORTED_TODAY: TodayQueryResult = {
  intent: "unsupported",
  answer: "I can answer read-only questions about today.",
  items: [],
  supported_queries: ["What needs my attention?"],
  details: [],
};

describe("current weather routing", () => {
  it("uses the configured workspace observation even when Today recognizes today", async () => {
    const context = await withSession({
      today: { ...DEFAULT_TODAY, intent: "today" },
      planError: null,
      plan: intentEnvelope(
        emailPlan({
          route: "weather.read",
          entity: { kind: "NONE", value: null, confidence: 1 },
          time: { kind: "RELATIVE", expression: "today", confidence: 0.9 },
        }),
      ),
    });
    context.upstream.weather = {
      status: "ready",
      city: "Ithaca, New York, United States",
      temperature: 18,
      unit: "celsius",
      description: "Cloudy",
      observed_at: "2026-09-30T11:45:00Z",
    };
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What's the weather today?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.capability_id).toBe("weather.read");
    expect(context.upstream.calls.today).toEqual([]);
    expect(context.upstream.calls.weather).toEqual([COOKIE]);
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "Ithaca",
    );
    expect(response.turn.action_refs).toEqual([]);
  });
});

describe("next class routing", () => {
  it("uses the class projection and never invents a time from a Canvas course", async () => {
    const context = await withSession({
      planError: null,
      plan: intentEnvelope(
        emailPlan({
          route: "class.next",
          entity: { kind: "NONE", value: null, confidence: 1 },
        }),
      ),
    });
    context.upstream.classSources = {
      complete: true,
      courses: [
        {
          course_id: "42",
          course_name: "Economics 3120",
          course_code: "ECON 3120",
          section: null,
          source: {
            resource_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            connection_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            external_resource_id: "course:42",
            updated_at: null,
          },
        },
      ],
      events: [],
    };
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What class do I have next?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.capability_id).toBe("class.next");
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "can't determine",
    );
    expect(context.upstream.calls.classSources).toEqual([COOKIE]);
    expect(context.upstream.calls.today).toEqual([]);
  });
});

describe("current time routing", () => {
  it("answers from the injected clock without touching another service", async () => {
    const context = await withSession({
      planError: null,
      plan: intentEnvelope(
        emailPlan({
          route: "time.now",
          entity: { kind: "NONE", value: null, confidence: 1 },
          time: { kind: "RELATIVE", expression: "now", confidence: 0.9 },
        }),
      ),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What time is it right now?",
        modality: "TEXT",
        timezone: "America/New_York",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.capability_id).toBe("time.now");
    expect(response.turn.decision?.reason).toBe("time.now.local");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.decision?.action_state).toBe("NONE");
    expect(response.turn.action_refs).toEqual([]);
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "8:00 AM",
    );
    expect(context.upstream.calls.plan).toHaveLength(1);
    expect(context.upstream.calls.today).toEqual([]);
    expect(context.upstream.calls.weather).toEqual([]);
    expect(context.upstream.calls.classSources).toEqual([]);
    expect(context.upstream.calls.trendingNews).toEqual([]);
    expect(context.upstream.calls.email).toEqual([]);
  });

  it("labels the UTC fallback when no valid timezone is supplied", async () => {
    const context = await withSession({
      planError: null,
      plan: intentEnvelope(
        emailPlan({
          route: "time.now",
          entity: { kind: "NONE", value: null, confidence: 1 },
          time: { kind: "NONE", expression: null, confidence: 1 },
        }),
      ),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What time is it?",
        modality: "TEXT",
        timezone: null,
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.capability_id).toBe("time.now");
    expect(response.turn.decision?.reason).toBe("time.now.utc_fallback");
    const blocks = JSON.stringify(response.turn.presentation?.blocks);
    expect(blocks).toContain("12:00 PM");
    expect(blocks).toContain("UTC");
    expect(context.upstream.calls.today).toEqual([]);
    expect(context.upstream.calls.classSources).toEqual([]);
  });
});

const EMAIL_RESULT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const SECOND_RESULT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";

function emailPlan(intent: Record<string, unknown> = {}) {
  return {
    version: 1,
    intents: [
      {
        route: "email.search",
        entity: { kind: "PERSON", value: "Sarah", confidence: 0.9 },
        time: { kind: "NONE", expression: null, confidence: 1 },
        reference: { kind: "NONE", ordinal: null },
        confidence: 0.9,
        requires_clarification: false,
        clarification: null,
        ...intent,
      },
    ],
  };
}

const SUBSCRIPTION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SUBSCRIPTION_ROW = {
  id: SUBSCRIPTION_ID,
  revision: 1,
  name: "Netflix",
  plan_name: "Premium",
  status: "ACTIVE",
  next_renewal_at: "2026-10-15T12:00:00Z",
  last_verified_at: null,
  updated_at: "2026-09-30T12:00:00Z",
};

function subscriptionPlan(entity = "Netflix") {
  return intentEnvelope(
    emailPlan({
      route: "subscription.search",
      entity: { kind: "ORGANIZATION", value: entity, confidence: 0.9 },
    }),
  );
}

async function subscriptionTurn(
  context: Awaited<ReturnType<typeof withSession>>,
  requestId = REQUEST_ID,
) {
  return context.runtime.submitTurn({
    cookie: COOKIE,
    session_id: context.sessionId,
    body: {
      request_id: requestId,
      text: "When does Netflix renew?",
      modality: "TEXT",
    },
  });
}

describe("SPEC-004 subscription lookup in a turn", () => {
  it("routes a renewal question through SPEC-004 even when Today would classify it", async () => {
    const context = await withSession({
      today: { ...DEFAULT_TODAY, intent: "renewals" },
      planError: null,
      plan: subscriptionPlan(),
    });
    context.upstream.subscriptions = {
      intent: "SEARCH",
      subscriptions: [SUBSCRIPTION_ROW],
    };
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "When does Netflix renew today?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.capability_id).toBe("subscription.search");
    expect(context.upstream.calls.today).toEqual([]);
    expect(context.upstream.calls.subscriptions).toEqual([
      { cookie: COOKIE, selector: "Netflix" },
    ]);
  });

  it("answers one grounded record and never starts an action", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: subscriptionPlan(),
    });
    context.upstream.subscriptions = {
      intent: "SEARCH",
      subscriptions: [SUBSCRIPTION_ROW],
    };
    const response = await subscriptionTurn(context);
    expect(context.upstream.calls.subscriptions).toEqual([
      { cookie: COOKIE, selector: "Netflix" },
    ]);
    expect(context.upstream.calls.cancellation).toEqual([
      { cookie: COOKIE, id: SUBSCRIPTION_ID },
    ]);
    expect(context.upstream.calls.email).toEqual([]);
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.capability_id).toBe("subscription.search");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.action_refs).toEqual([]);
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "No cancellation attempt is recorded",
    );
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "access end date is unknown",
    );
  });

  it("clarifies zero, multiple or ungrounded matches without a cancellation read", async () => {
    const none = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: subscriptionPlan(),
    });
    expect((await subscriptionTurn(none)).turn.state).toBe("CLARIFY");
    expect(none.upstream.calls.cancellation).toEqual([]);
    const many = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: subscriptionPlan(),
    });
    many.upstream.subscriptions = {
      intent: "SEARCH",
      subscriptions: [
        SUBSCRIPTION_ROW,
        {
          ...SUBSCRIPTION_ROW,
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          plan_name: "Basic",
        },
      ],
    };
    expect((await subscriptionTurn(many)).turn.state).toBe("CLARIFY");
    expect(many.upstream.calls.cancellation).toEqual([]);
    const ungrounded = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: subscriptionPlan("Spotify"),
    });
    expect((await subscriptionTurn(ungrounded)).turn.state).toBe("CLARIFY");
    expect(ungrounded.upstream.calls.subscriptions).toEqual([]);
  });

  it("withholds a result when the account changes before cancellation lookup", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: subscriptionPlan(),
    });
    context.upstream.subscriptions = () => {
      context.upstream.account = {
        ...context.upstream.account,
        workspace_id: OTHER_SCOPE.workspace_id,
      };
      return Promise.resolve({
        intent: "SEARCH",
        subscriptions: [SUBSCRIPTION_ROW],
      });
    };
    const response = await subscriptionTurn(context);
    expect(response.turn.state).toBe("WITHHELD");
    expect(context.upstream.calls.cancellation).toEqual([]);
    expect(JSON.stringify(response.turn.presentation?.blocks)).not.toContain(
      "Netflix",
    );
  });

  it("qualifies a source outage or malformed response without guessing a date", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: subscriptionPlan(),
    });
    context.upstream.subscriptionsError = new AssistantError(
      "unavailable",
      "SPEC-004 outage",
    );
    const unavailable = await subscriptionTurn(context);
    expect(unavailable.turn.state).toBe("UNAVAILABLE");
    expect(unavailable.turn.action_refs).toEqual([]);
    context.upstream.subscriptionsError = null;
    context.upstream.subscriptions = {
      intent: "SEARCH",
      subscriptions: [{ ...SUBSCRIPTION_ROW, next_renewal_at: "tomorrow" }],
    };
    const malformed = await subscriptionTurn(
      context,
      "77777777-7777-4777-8777-777777777778",
    );
    expect(malformed.turn.state).toBe("UNAVAILABLE");
    expect(context.upstream.calls.cancellation).toEqual([]);
  });

  it("qualifies a cancellation read outage and a stale cancellation preview", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: subscriptionPlan(),
    });
    context.upstream.subscriptions = {
      intent: "SEARCH",
      subscriptions: [SUBSCRIPTION_ROW],
    };
    context.upstream.cancellationError = new AssistantError(
      "unavailable",
      "SPEC-004 cancellation outage",
    );
    expect((await subscriptionTurn(context)).turn.state).toBe("UNAVAILABLE");
    context.upstream.cancellationError = null;
    context.upstream.cancellation = {
      id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      obligation_id: SUBSCRIPTION_ID,
      status: "VERIFIED_CANCELLED",
      verification_status: "VERIFIED_CANCELLED",
      preview: {
        target: {
          obligation_id: SUBSCRIPTION_ID,
          user_id: SCOPE.user_id,
          workspace_id: SCOPE.workspace_id,
          revision: "0",
        },
        access_ends_at: "2026-10-15T12:00:00Z",
      },
    };
    const stale = await subscriptionTurn(
      context,
      "77777777-7777-4777-8777-777777777778",
    );
    expect(stale.turn.state).toBe("UNAVAILABLE");
    expect(JSON.stringify(stale.turn.presentation?.blocks)).not.toContain(
      "cancellation is verified",
    );
    expect(stale.turn.action_refs).toEqual([]);
  });
});

const NEWS_STORY_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const NEWS_SOURCE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const NEWS_URL = "https://publisher.example/jane";
const NEWS_STORY = {
  id: NEWS_STORY_ID,
  version: 1,
  headline: "Jane Doe announces a project",
  description: "A story about Jane Doe",
  verification_status: "ATTRIBUTED",
  source_count: 1,
  published_at: "2026-09-30T09:00:00Z",
  last_updated_at: "2026-09-30T10:00:00Z",
  retrieved_at: "2026-09-30T11:00:00Z",
  evidence_pending: false,
  sources: [
    {
      id: NEWS_SOURCE_ID,
      source_name: "Example Publisher",
      canonical_url: NEWS_URL,
      expires_at: "2026-10-01T11:00:00Z",
    },
  ],
};
const NEWS_SUMMARY = {
  status: "READY",
  headline: NEWS_STORY.headline,
  headline_source_url: NEWS_URL,
  headline_attribution: "Example Publisher",
  headline_status: "ATTRIBUTED",
  as_of: "2026-09-30T11:30:00Z",
  actions_executed: false,
  sections: [
    {
      heading: "what_happened",
      facts: [
        {
          claim_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          text: "Jane Doe announced the project.",
          status: "ATTRIBUTED",
          source_name: "Example Publisher",
          source_url: NEWS_URL,
        },
      ],
    },
  ],
};

function newsPlan(entity: string | null) {
  return intentEnvelope(
    emailPlan({
      route: "news.read",
      entity:
        entity === null
          ? { kind: "NONE", value: null, confidence: 1 }
          : { kind: "PERSON", value: entity, confidence: 0.9 },
    }),
  );
}

async function newsTurn(
  context: Awaited<ReturnType<typeof withSession>>,
  text: string,
  requestId = REQUEST_ID,
) {
  return context.runtime.submitTurn({
    cookie: COOKIE,
    session_id: context.sessionId,
    body: { request_id: requestId, text, modality: "TEXT" },
  });
}

describe("SPEC-006 News lookup in a turn", () => {
  it("shows generic trending headlines as attributed activity, not verified fact", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan(null),
    });
    context.upstream.trendingNews = [NEWS_STORY];
    const response = await newsTurn(context, "What's trending?");
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.capability_id).toBe("news.read");
    expect(context.upstream.calls.trendingNews).toEqual([COOKIE]);
    expect(context.upstream.calls.newsStory).toEqual([]);
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "does not verify",
    );
    expect(response.turn.action_refs).toEqual([]);
  });

  it("explains one grounded story through a fresh detail and source-backed summary", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan("Jane Doe"),
    });
    context.upstream.trendingNews = [NEWS_STORY];
    context.upstream.newsStory = NEWS_STORY;
    context.upstream.newsSummary = NEWS_SUMMARY;
    const response = await newsTurn(context, "Why is Jane Doe trending?");
    expect(response.turn.state).toBe("READY");
    expect(context.upstream.calls.newsStory).toEqual([
      { cookie: COOKIE, id: NEWS_STORY_ID },
    ]);
    expect(context.upstream.calls.newsSummary).toEqual([
      { cookie: COOKIE, id: NEWS_STORY_ID },
    ]);
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "ATTRIBUTED: Jane Doe announced",
    );
    expect(response.turn.action_refs).toEqual([]);
  });

  it("clarifies missing, ambiguous or ungrounded named stories before summary", async () => {
    const missing = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan("Jane Doe"),
    });
    expect(
      (await newsTurn(missing, "Why is Jane Doe trending?")).turn.state,
    ).toBe("CLARIFY");
    expect(missing.upstream.calls.newsStory).toEqual([]);
    const ambiguous = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan("Jane Doe"),
    });
    ambiguous.upstream.trendingNews = [
      NEWS_STORY,
      { ...NEWS_STORY, id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" },
    ];
    expect(
      (await newsTurn(ambiguous, "Why is Jane Doe trending?")).turn.state,
    ).toBe("CLARIFY");
    expect(ambiguous.upstream.calls.newsStory).toEqual([]);
    const ungrounded = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan("Other"),
    });
    expect(
      (await newsTurn(ungrounded, "Why is Jane Doe trending?")).turn.state,
    ).toBe("CLARIFY");
    expect(ungrounded.upstream.calls.trendingNews).toEqual([]);
  });

  it("qualifies a changed story, account switch or unavailable summary", async () => {
    const changed = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan("Jane Doe"),
    });
    changed.upstream.trendingNews = [NEWS_STORY];
    changed.upstream.newsStory = { ...NEWS_STORY, version: 2 };
    expect(
      (await newsTurn(changed, "Why is Jane Doe trending?")).turn.state,
    ).toBe("UNAVAILABLE");
    expect(changed.upstream.calls.newsSummary).toEqual([]);
    const switched = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan("Jane Doe"),
    });
    switched.upstream.trendingNews = () => {
      switched.upstream.account = {
        ...switched.upstream.account,
        workspace_id: OTHER_SCOPE.workspace_id,
      };
      return Promise.resolve([NEWS_STORY]);
    };
    expect(
      (await newsTurn(switched, "Why is Jane Doe trending?")).turn.state,
    ).toBe("WITHHELD");
    expect(switched.upstream.calls.newsStory).toEqual([]);
    const pending = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan("Jane Doe"),
    });
    pending.upstream.trendingNews = [NEWS_STORY];
    pending.upstream.newsStory = NEWS_STORY;
    pending.upstream.newsSummary = {
      status: "PENDING",
      actions_executed: false,
    };
    expect(
      (await newsTurn(pending, "Why is Jane Doe trending?")).turn.state,
    ).toBe("UNAVAILABLE");
  });

  it("qualifies a SPEC-006 feed outage without inventing an answer", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: newsPlan(null),
    });
    context.upstream.trendingNewsError = new AssistantError(
      "unavailable",
      "SPEC-006 feed outage",
    );
    const response = await newsTurn(context, "What's trending?");
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(response.turn.decision?.reason).toBe("news.unavailable");
    expect(response.turn.action_refs).toEqual([]);
    expect(context.upstream.calls.newsStory).toEqual([]);
  });
});

describe("compound read-only turns", () => {
  it("answers a time and next-class question in one turn", async () => {
    const question = "What time is it, and what class do I have next?";
    const plan = intentEnvelope({
      version: 1,
      intents: [
        {
          ...emailPlan().intents[0],
          route: "time.now",
          question: "What time is it",
          entity: { kind: "NONE", value: null, confidence: 1 },
          time: { kind: "NONE", expression: null, confidence: 1 },
        },
        {
          ...emailPlan().intents[0],
          route: "class.next",
          question: "what class do I have next?",
          entity: { kind: "NONE", value: null, confidence: 1 },
        },
      ],
    });
    const context = await withSession({ planError: null, plan });
    context.upstream.classSources = {
      complete: true,
      courses: [],
      events: [
        {
          provider: "google",
          course_id: null,
          title: "ECON 3120 Lecture",
          start_at: "2026-09-30T14:00:00Z",
          end_at: "2026-09-30T15:00:00Z",
          location: "Room 201",
          meeting_url: null,
          status: "SCHEDULED",
          explicit_class_meeting: true,
          all_day: false,
          fresh_until: "2026-10-01T00:00:00Z",
          source: {
            resource_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            connection_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            external_resource_id: "event:1",
            updated_at: "2026-09-30T10:00:00Z",
          },
        },
      ],
    };
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: question,
        modality: "TEXT",
        timezone: "America/New_York",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.reason).toBe("plan.combined");
    expect(response.turn.action_refs).toEqual([]);
    const answer = response.turn.presentation?.blocks[0];
    expect(answer).toMatchObject({ kind: "ANSWER" });
    if (answer?.kind === "ANSWER") {
      expect(answer.text).toContain("Time:");
      expect(answer.text).toContain("8:00 AM");
      expect(answer.text).toContain("Next class:");
      expect(answer.text).toContain("ECON 3120");
    }
    // The class answer comes from the class projection, never from the clock.
    expect(context.upstream.calls.classSources).toEqual([COOKIE]);
    expect(context.upstream.calls.today).toEqual([]);
    expect(context.upstream.calls.weather).toEqual([]);
  });

  it("combines weather, next class, Today and News in one scoped turn", async () => {
    const fullQuestion =
      "What's the weather today, what class do I have next, what needs my attention, and what's trending?";
    const fullPlan = intentEnvelope({
      version: 1,
      intents: [
        {
          ...emailPlan().intents[0],
          route: "weather.read",
          question: "What's the weather today",
          entity: { kind: "NONE", value: null, confidence: 1 },
          time: { kind: "RELATIVE", expression: "today", confidence: 1 },
        },
        {
          ...emailPlan().intents[0],
          route: "class.next",
          question: "what class do I have next",
          entity: { kind: "NONE", value: null, confidence: 1 },
        },
        {
          ...emailPlan().intents[0],
          route: "today.read",
          question: "what needs my attention",
          entity: { kind: "NONE", value: null, confidence: 1 },
        },
        {
          ...emailPlan().intents[0],
          route: "news.read",
          question: "what's trending?",
          entity: { kind: "NONE", value: null, confidence: 1 },
        },
      ],
    });
    const context = await withSession({ planError: null, plan: fullPlan });
    context.upstream.weather = {
      status: "ready",
      city: "Ithaca, New York, United States",
      temperature: 18,
      unit: "celsius",
      description: "Cloudy",
      observed_at: "2026-09-30T11:45:00Z",
    };
    context.upstream.classSources = {
      complete: true,
      courses: [],
      events: [
        {
          provider: "google",
          course_id: null,
          title: "ECON 3120 Lecture",
          start_at: "2026-09-30T14:00:00Z",
          end_at: "2026-09-30T15:00:00Z",
          location: "Room 201",
          meeting_url: null,
          status: "SCHEDULED",
          explicit_class_meeting: true,
          all_day: false,
          fresh_until: "2026-10-01T00:00:00Z",
          source: {
            resource_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            connection_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            external_resource_id: "event:1",
            updated_at: "2026-09-30T10:00:00Z",
          },
        },
      ],
    };
    context.upstream.trendingNews = [NEWS_STORY];
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: fullQuestion,
        modality: "TEXT",
        timezone: "America/New_York",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.reason).toBe("plan.combined");
    expect(context.upstream.calls.classSources).toEqual([COOKIE]);
    expect(context.upstream.calls.today).toHaveLength(1);
    expect(context.upstream.calls.trendingNews).toEqual([COOKIE]);
    expect(response.turn.action_refs).toEqual([]);
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "ECON 3120",
    );
  });

  const question =
    "What's the weather today, what needs my attention, and what's trending?";
  const plan = intentEnvelope({
    version: 1,
    intents: [
      {
        ...emailPlan().intents[0],
        route: "weather.read",
        question: "What's the weather today",
        entity: { kind: "NONE", value: null, confidence: 1 },
        time: { kind: "RELATIVE", expression: "today", confidence: 0.9 },
      },
      {
        ...emailPlan().intents[0],
        route: "today.read",
        question: "what needs my attention",
        entity: { kind: "NONE", value: null, confidence: 1 },
      },
      {
        ...emailPlan().intents[0],
        route: "news.read",
        question: "what's trending?",
        entity: { kind: "NONE", value: null, confidence: 1 },
      },
    ],
  });

  it("combines scoped weather, Today and News facts without an action", async () => {
    const context = await withSession({ planError: null, plan });
    context.upstream.weather = {
      status: "ready",
      city: "Ithaca, New York, United States",
      temperature: 18,
      unit: "celsius",
      description: "Cloudy",
      observed_at: "2026-09-30T11:45:00Z",
    };
    context.upstream.trendingNews = [NEWS_STORY];
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: { request_id: REQUEST_ID, text: question, modality: "VOICE" },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.reason).toBe("plan.combined");
    expect(response.turn.action_refs).toEqual([]);
    expect(context.upstream.calls.weather).toEqual([COOKIE]);
    expect(context.upstream.calls.today[0]?.query).toBe(
      "what needs my attention",
    );
    expect(context.upstream.calls.trendingNews).toEqual([COOKIE]);
    const answer = response.turn.presentation?.blocks[0];
    expect(answer).toMatchObject({ kind: "ANSWER" });
    if (answer?.kind === "ANSWER") {
      expect(answer.text).toContain("Weather:");
      expect(answer.text).toContain("Today:");
      expect(answer.text).toContain("News:");
    }
    expect(response.turn.presentation?.speak).toBe(true);
  });

  it("labels partial source failure and withholds earlier facts after an account switch", async () => {
    const partial = await withSession({ planError: null, plan });
    partial.upstream.weather = {
      status: "unavailable",
      unit: "celsius",
      temperature: null,
    };
    partial.upstream.trendingNews = [NEWS_STORY];
    const response = await partial.runtime.submitTurn({
      cookie: COOKIE,
      session_id: partial.sessionId,
      body: { request_id: REQUEST_ID, text: question, modality: "TEXT" },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.reason).toBe("plan.partial");
    expect(JSON.stringify(response.turn.presentation?.blocks)).toContain(
      "weather reading is unavailable",
    );

    const switched = await withSession({ planError: null, plan });
    switched.upstream.weather = () => {
      switched.upstream.account = {
        ...switched.upstream.account,
        workspace_id: OTHER_SCOPE.workspace_id,
      };
      return Promise.resolve({ status: "unavailable", unit: "celsius" });
    };
    const withheld = await switched.runtime.submitTurn({
      cookie: COOKIE,
      session_id: switched.sessionId,
      body: { request_id: REQUEST_ID, text: question, modality: "TEXT" },
    });
    expect(withheld.turn.state).toBe("WITHHELD");
    expect(switched.upstream.calls.today).toEqual([]);
    expect(switched.upstream.calls.trendingNews).toEqual([]);
    expect(JSON.stringify(withheld.turn.presentation?.blocks)).not.toContain(
      "Ithaca",
    );
  });
});

describe("SPEC-005 intent bridge in a turn", () => {
  it("delegates an unsupported question to the planner and read-only search", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: emailSearchPayload([
        {
          resource_id: EMAIL_RESULT,
          title: "Renewal confirmation",
          external_resource_id: "message-1",
        },
      ]),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me about the renewal?",
        modality: "TEXT",
      },
    });
    expect(context.upstream.calls.plan).toEqual([
      {
        utterance: "What did Sarah email me about the renewal?",
        recentReferences: [],
        sessionId: NAVOX_SESSION_ID,
      },
    ]);
    expect(context.upstream.calls.email).toEqual([
      {
        query: "What did Sarah email me about the renewal?",
        limit: LIMITS.maxEmailResults,
      },
    ]);
    expect(response.turn.state).toBe("READY");
    expect(response.turn.plan?.intents[0]?.kind).toBe("email.search");
    expect(response.turn.decision?.capability_id).toBe("email.search");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.decision?.action_state).toBe("NONE");
    expect(response.turn.action_refs).toEqual([]);
    const citations = response.turn.presentation?.blocks.find(
      (block) => block.kind === "CITATIONS",
    );
    expect(citations).toMatchObject({
      kind: "CITATIONS",
      citations: [
        { evidence_id: EMAIL_RESULT, external_resource_id: "message-1" },
      ],
    });
  });

  it("sends only the operator's own questions and binds the follow-up selector", async () => {
    const context = await withSession({
      today: DEFAULT_TODAY,
    });
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    const first = context.store.turns[0];
    expect(first).toBeDefined();
    context.upstream.today = UNSUPPORTED_TODAY;
    context.upstream.planError = null;
    context.upstream.plan = intentEnvelope(
      emailPlan({
        entity: { kind: "NONE", value: null, confidence: 0.4 },
        reference: { kind: "RECENT_TURN", ordinal: 1 },
      }),
    );
    context.upstream.email = emailSearchPayload([
      { resource_id: EMAIL_RESULT, title: "Renewal confirmation" },
    ]);
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "What about that one?",
        modality: "VOICE",
      },
    });
    expect(context.upstream.calls.plan).toEqual([
      {
        utterance: "What did Sarah email me?",
        recentReferences: [],
        sessionId: NAVOX_SESSION_ID,
      },
      {
        utterance: "What about that one?",
        recentReferences: ["What did Sarah email me?"],
        sessionId: NAVOX_SESSION_ID,
      },
    ]);
    expect(response.turn.plan?.intents[0]?.reference).toEqual({
      kind: "RECENT_TURN",
      ordinal: 1,
      turn_id: first?.id,
    });
    expect(response.turn.state).toBe("READY");
  });

  it("keeps an ambiguous match as a clarification and never acts", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: emailSearchPayload([
        { resource_id: EMAIL_RESULT, title: "Renewal notice" },
        { resource_id: SECOND_RESULT, title: "Renewal receipt" },
      ]),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me about the renewal?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.kind).toBe("CLARIFY");
    expect(response.turn.decision?.action_state).toBe("NONE");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.action_refs).toEqual([]);
  });

  it("resolves a clicked message with a fresh complete search before offering a draft", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: emailSearchPayload([
        { resource_id: EMAIL_RESULT, title: "Renewal notice" },
        { resource_id: SECOND_RESULT, title: "Renewal receipt" },
      ]),
    });
    const ambiguous = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me about the renewal?",
        modality: "TEXT",
      },
    });
    const selected = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "Select an email",
        modality: "TEXT",
        referents: [ambiguous.turn.id, SECOND_RESULT],
      },
    });
    expect(selected.turn.state).toBe("READY");
    expect(selected.turn.decision?.reason).toBe("email.search.found_one");
    expect(
      selected.turn.presentation?.blocks.filter(
        (block) => block.kind === "ITEM",
      ),
    ).toMatchObject([{ item: { id: SECOND_RESULT } }]);
    expect(context.upstream.calls.today).toHaveLength(0);
    expect(context.upstream.calls.plan).toHaveLength(1);
    expect(context.upstream.calls.email).toHaveLength(2);
    const generate = vi.fn(async () => ({}));
    context.upstream.createKnowledgeEmailDraft = generate;
    await expect(
      context.runtime.createEmailDraft({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: { source_turn_id: selected.turn.id, instructions: "Reply" },
      }),
    ).rejects.toMatchObject({ code: "unavailable" });
    expect(generate).toHaveBeenCalledWith(COOKIE, {
      sourceId: SECOND_RESULT,
      instructions: "Reply",
    });
  });

  it("refuses an unlisted, vanished or incomplete clicked email without a draft", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: emailSearchPayload([
        { resource_id: EMAIL_RESULT },
        { resource_id: SECOND_RESULT },
      ]),
    });
    const ambiguous = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Find Sarah's email",
        modality: "TEXT",
      },
    });
    const generate = vi.fn(async () => ({}));
    context.upstream.createKnowledgeEmailDraft = generate;
    const forged = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "Select an email",
        modality: "TEXT",
        referents: [ambiguous.turn.id, "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"],
      },
    });
    expect(forged.turn.state).not.toBe("READY");
    expect(context.upstream.calls.email).toHaveLength(1);
    context.upstream.email = emailSearchPayload([
      { resource_id: EMAIL_RESULT },
    ]);
    const vanished = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777779",
        text: "Select an email",
        modality: "TEXT",
        referents: [ambiguous.turn.id, SECOND_RESULT],
      },
    });
    expect(vanished.turn.state).not.toBe("READY");
    context.upstream.email = emailSearchPayload(
      [{ resource_id: SECOND_RESULT }],
      { truncated: true },
    );
    const incomplete = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777780",
        text: "Select an email",
        modality: "TEXT",
        referents: [ambiguous.turn.id, SECOND_RESULT],
      },
    });
    expect(incomplete.turn.state).not.toBe("READY");
    expect(generate).not.toHaveBeenCalled();
  });

  it("asks the planner's clarification instead of guessing a route", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(CLARIFY_PLAN),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Do the thing with the stuff",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "CLARIFY",
        text: "I need a little more detail before I can look that up.",
      },
    ]);
    expect(context.upstream.calls.email).toHaveLength(0);
  });

  it("keeps a compound plan as a clarification and executes nothing", async () => {
    const [single] = emailPlan().intents;
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope({
        version: 1,
        intents: [
          { ...single, question: "What did Sarah email me" },
          {
            route: "assistant.clarify",
            question: "what is on my calendar?",
            entity: { kind: "NONE", value: null, confidence: 1 },
            time: { kind: "NONE", expression: null, confidence: 1 },
            reference: { kind: "NONE", ordinal: null },
            confidence: 1,
            requires_clarification: true,
            clarification: "Which do you want first?",
          },
        ],
      }),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me and what is on my calendar?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.reason).toBe("plan.multi_intent");
    expect(response.turn.decision?.action_state).toBe("NONE");
    expect(response.turn.action_refs).toEqual([]);
    expect(context.upstream.calls.email).toHaveLength(0);
    expect(response.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "CLARIFY",
        text: "One part of that request needs clarification. Which read-only part should I handle first?",
      },
    ]);
  });

  it("refuses an unowned route or injected authority without delegating", async () => {
    for (const plan of [
      intentEnvelope(emailPlan({ route: "email.send" })),
      intentEnvelope(emailPlan({ action_grant: { send: true } })),
      intentEnvelope(emailPlan({ capability_id: "email.send" })),
    ]) {
      const context = await withSession({
        today: UNSUPPORTED_TODAY,
        planError: null,
        plan,
      });
      const response = await context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "Send that to Sarah",
          modality: "TEXT",
        },
      });
      expect(response.turn.state).toBe("UNAVAILABLE");
      expect(response.turn.decision?.kind).toBe("REFUSED");
      expect(response.turn.decision?.action_state).toBe("NONE");
      expect(response.turn.action_refs).toEqual([]);
      expect(context.upstream.calls.email).toHaveLength(0);
      expect(response.turn.plan?.intents[0]?.kind).toBe("today.read");
    }
  });

  it("does not re-ask Today when the planner names the Today route", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope({
        version: 1,
        intents: [
          {
            route: "today.read",
            entity: { kind: "NONE", value: null, confidence: 0.5 },
            time: { kind: "NONE", expression: null, confidence: 1 },
            reference: { kind: "NONE", ordinal: null },
            confidence: 0.5,
            requires_clarification: false,
            clarification: null,
          },
        ],
      }),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Anything about the thing?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("CLARIFY");
    expect(context.upstream.calls.today).toHaveLength(1);
    expect(context.upstream.calls.email).toHaveLength(0);
  });

  it("fails honestly when no planner can route a non-Today request", async () => {
    const context = await withSession({ today: UNSUPPORTED_TODAY });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Book me a flight",
        modality: "TEXT",
      },
    });
    expect(context.upstream.calls.plan).toHaveLength(1);
    expect(context.upstream.calls.email).toHaveLength(0);
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(
      response.turn.presentation?.blocks.map((block) => block.kind),
    ).toEqual(["NOTICE"]);
    expect(response.turn.plan?.intents[0]?.kind).toBe("today.read");
  });

  it("rechecks the current account before delegating to connected search", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: emailSearchPayload([{ resource_id: EMAIL_RESULT }]),
    });
    let accounts = 0;
    context.upstream.fetchAccount = async () => {
      accounts += 1;
      // The session was created before this override, so the first observed
      // call is the turn scope and the second is the pre-delegation recheck.
      return accounts === 1
        ? {
            user_id: SCOPE.user_id,
            workspace_id: SCOPE.workspace_id,
            email: "operator@example.com",
          }
        : {
            user_id: OTHER_SCOPE.user_id,
            workspace_id: OTHER_SCOPE.workspace_id,
            email: "someone-else@example.com",
          };
    };
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("WITHHELD");
    expect(response.turn.decision?.kind).toBe("WITHHELD");
    expect(accounts).toBe(2);
    expect(context.upstream.calls.email).toHaveLength(0);
  });

  it("keeps a disabled or forbidden connected search qualified", async () => {
    const disabled = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      emailError: new AssistantError(
        "unsupported",
        "Connected email search is not enabled in this deployment.",
      ),
    });
    const disabledResponse = await disabled.runtime.submitTurn({
      cookie: COOKIE,
      session_id: disabled.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    expect(disabledResponse.turn.state).toBe("UNAVAILABLE");
    expect(disabledResponse.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "UNAVAILABLE",
        text: "That assistant lookup is not enabled in this deployment. Nothing was changed.",
      },
    ]);
    expect(disabledResponse.turn.action_refs).toEqual([]);

    const forbidden = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      emailError: new AssistantError("forbidden", "Denied"),
    });
    const forbiddenResponse = await forbidden.runtime.submitTurn({
      cookie: COOKIE,
      session_id: forbidden.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    expect(forbiddenResponse.turn.state).toBe("WITHHELD");
    expect(forbiddenResponse.turn.decision?.kind).toBe("WITHHELD");
  });

  it("keeps a planner outage qualified instead of answering from Today", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: new AssistantError("unavailable", "The planner is down"),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Book me a flight",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(response.turn.decision?.reason).toBe("plan.unavailable");
    expect(response.turn.decision?.kind).toBe("UNAVAILABLE");
    expect(response.turn.action_refs).toEqual([]);
    expect(context.upstream.calls.email).toHaveLength(0);
    expect(response.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "UNAVAILABLE",
        text: "The assistant could not look that up right now. Nothing was changed.",
      },
    ]);
    expect(
      response.turn.presentation?.blocks.some(
        (block) => block.kind === "SUGGESTIONS",
      ),
    ).toBe(false);
  });

  it("keeps a search outage or malformed result qualified with a search reason", async () => {
    const outage = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      emailError: new AssistantError("unavailable", "Connected search is down"),
    });
    const outageResponse = await outage.runtime.submitTurn({
      cookie: COOKIE,
      session_id: outage.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    expect(outageResponse.turn.state).toBe("UNAVAILABLE");
    expect(outageResponse.turn.decision?.reason).toBe("search.unavailable");
    expect(outageResponse.turn.decision?.action_state).toBe("NONE");
    expect(outageResponse.turn.action_refs).toEqual([]);
    expect(outageResponse.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "UNAVAILABLE",
        text: "The assistant could not look that up right now. Nothing was changed.",
      },
    ]);
    // The validated plan is kept; SPEC-002's answer never replaces it.
    expect(outageResponse.turn.plan?.intents[0]?.kind).toBe("email.search");

    const malformed = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: { results: "not-a-list", coverage: { truncated: false } },
    });
    const malformedResponse = await malformed.runtime.submitTurn({
      cookie: COOKIE,
      session_id: malformed.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    expect(malformedResponse.turn.state).toBe("UNAVAILABLE");
    expect(malformedResponse.turn.decision?.reason).toBe("search.unavailable");
    expect(malformedResponse.turn.presentation?.blocks).toEqual([
      {
        kind: "NOTICE",
        state: "UNAVAILABLE",
        text: "The assistant could not look that up right now. Nothing was changed.",
      },
    ]);
  });

  it("cannot confirm a unique email from incomplete coverage", async () => {
    const context = await withSession({
      today: UNSUPPORTED_TODAY,
      planError: null,
      plan: intentEnvelope(emailPlan()),
      email: emailSearchPayload([{ resource_id: EMAIL_RESULT }], {
        truncated: true,
      }),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me?",
        modality: "TEXT",
      },
    });
    expect(context.upstream.calls.email).toHaveLength(1);
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.kind).toBe("CLARIFY");
    expect(response.turn.decision?.reason).toBe("email.search.incomplete");
    expect(response.turn.decision?.action_state).toBe("NONE");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.action_refs).toEqual([]);
    expect(
      response.turn.presentation?.blocks.some((block) => block.kind === "ITEM"),
    ).toBe(true);
    expect(
      response.turn.presentation?.blocks.some(
        (block) =>
          block.kind === "DETAILS" &&
          block.lines.includes(
            "Search coverage was incomplete, so other matches may exist.",
          ),
      ),
    ).toBe(true);
  });

  it("rejects an envelope that claims an action or another session", async () => {
    for (const envelope of [
      intentEnvelope(emailPlan(), { actions_executed: true }),
      intentEnvelope(emailPlan(), {
        session_id: "66666666-6666-4666-8666-666666666666",
      }),
      intentEnvelope(emailPlan(), { workspace_id: OTHER_SCOPE.workspace_id }),
    ]) {
      const context = await withSession({
        today: UNSUPPORTED_TODAY,
        planError: null,
        plan: envelope,
      });
      const response = await context.runtime.submitTurn({
        cookie: COOKIE,
        session_id: context.sessionId,
        body: {
          request_id: REQUEST_ID,
          text: "What did Sarah email me?",
          modality: "TEXT",
        },
      });
      expect(response.turn.state).toBe("UNAVAILABLE");
      expect(response.turn.decision?.kind).toBe("REFUSED");
      expect(response.turn.action_refs).toEqual([]);
      expect(context.upstream.calls.email).toHaveLength(0);
    }
  });
});

describe("real SPEC-005 and SPEC-007 wire shapes", () => {
  function jsonResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "content-type": "application/json" },
    });
  }

  it("runs the full bridge from the real API envelopes", async () => {
    const seen: { path: string; body: unknown }[] = [];
    const upstream = createNavoxUpstream({
      baseUrl: "https://navox.example/api/v1",
      fetchImpl: async (input, init) => {
        const path = new URL(input).pathname;
        const body = init?.body ? JSON.parse(String(init.body)) : null;
        seen.push({ path, body });
        if (path.endsWith("/auth/me")) {
          return jsonResponse({
            id: SCOPE.user_id,
            email: "operator@example.com",
            workspace: { id: SCOPE.workspace_id },
          });
        }
        if (path.endsWith("/today/query"))
          return jsonResponse(UNSUPPORTED_TODAY);
        if (path.endsWith("/ai/sessions")) {
          return jsonResponse({ id: NAVOX_SESSION_ID }, 201);
        }
        if (path.endsWith("/ai/assistant/intents")) {
          // The exact SPEC-005 response envelope, echoing the request session.
          return jsonResponse({
            task_id: "aaaaaaaa-0000-4000-8000-000000000001",
            trace_id: "aaaaaaaa-0000-4000-8000-000000000002",
            plan: emailPlan(),
            session_id: (body as { session_id: string }).session_id,
            turn_sequence: 1,
            actions_executed: false,
          });
        }
        if (path.endsWith("/search/query")) {
          expect(body).toMatchObject({
            mode: "SEARCH",
            types: ["EMAIL", "EMAIL_THREAD"],
          });
          return jsonResponse(
            emailSearchPayload([
              {
                resource_id: EMAIL_RESULT,
                title: "Renewal confirmation",
                external_resource_id: "message-1",
              },
            ]),
          );
        }
        throw new Error(`unexpected path ${path}`);
      },
    });
    const store = createMemoryStore();
    const runtime = createAssistantRuntime({
      store,
      upstream,
      now: () => NOW,
      newId: ids(),
    });
    const session = await runtime.createSession({ cookie: COOKIE });
    const response = await runtime.submitTurn({
      cookie: COOKIE,
      session_id: session.id,
      body: {
        request_id: REQUEST_ID,
        text: "What did Sarah email me about the renewal?",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("READY");
    expect(response.turn.decision?.capability_id).toBe("email.search");
    const citations = response.turn.presentation?.blocks.find(
      (block) => block.kind === "CITATIONS",
    );
    expect(citations).toMatchObject({
      kind: "CITATIONS",
      citations: [{ evidence_id: EMAIL_RESULT }],
    });
    expect(
      seen
        .filter((call) => call.path.endsWith("/ai/assistant/intents"))
        .map((call) => call.body),
    ).toEqual([
      {
        utterance: "What did Sarah email me about the renewal?",
        recent_references: [],
        session_id: NAVOX_SESSION_ID,
      },
    ]);
  });
});

describe("adaptive response modality", () => {
  it("saves a wake greeting in the same session without planning or action authority", async () => {
    const context = await withSession();
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "Hey NavoX",
        modality: "VOICE",
      },
    });
    expect(context.upstream.calls.plan).toHaveLength(0);
    expect(context.upstream.calls.today).toHaveLength(0);
    expect(response.turn.plan?.intents[0]?.kind).toBe("assistant.delivery");
    expect(response.turn.decision?.kind).toBe("PRESENT");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.action_refs).toEqual([]);
    expect(response.turn.presentation?.speech_text).toBe("Hi, I'm listening.");
    expect(response.turn.presentation?.speak).toBe(true);
  });

  it("answers a spoken turn with the same visual blocks plus a bounded summary", async () => {
    const context = await withSession();
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "VOICE",
      },
    });
    expect(response.turn.presentation?.presentation).toBe("BOTH");
    expect(response.turn.presentation?.speak).toBe(true);
    // A spoken question follows the client's Voice Mode preference.
    expect(response.turn.presentation?.delivery).toBe("AUTOMATIC");
    expect(response.turn.presentation?.speech_text).toBe(
      "1 item needs attention now.",
    );
    // One coherent turn: the detailed visual blocks survive alongside speech.
    expect(response.turn.presentation?.blocks).toEqual([
      { kind: "ANSWER", text: "1 item needs attention now." },
    ]);
  });

  it("speaks the most recent eligible answer in the same session on request", async () => {
    const context = await withSession();
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    const plannerCalls = context.upstream.calls.plan.length;
    const todayCalls = context.upstream.calls.today.length;
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "read it to me",
        modality: "TEXT",
      },
    });
    // The delivery turn is resolved from saved state; it makes no provider call.
    expect(context.upstream.calls.plan).toHaveLength(plannerCalls);
    expect(context.upstream.calls.today).toHaveLength(todayCalls);
    expect(response.turn.plan?.intents[0]?.kind).toBe("assistant.delivery");
    expect(response.turn.decision?.kind).toBe("PRESENT");
    expect(response.turn.decision?.capability_id).toBeNull();
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.action_refs).toEqual([]);
    expect(response.turn.presentation?.presentation).toBe("BOTH");
    expect(response.turn.presentation?.speak).toBe(true);
    // The canonical signal the client reads: this answer was asked for in words.
    expect(response.turn.presentation?.delivery).toBe("SPEAK");
    expect(response.turn.presentation?.speech_text).toBe(
      "1 item needs attention now.",
    );
    expect(response.turn.presentation?.blocks).toEqual([
      { kind: "ANSWER", text: "1 item needs attention now." },
    ]);
  });

  it("asks for clarification when no saved answer can be read aloud", async () => {
    const context = await withSession();
    const plannerCalls = context.upstream.calls.plan.length;
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "read it to me",
        modality: "VOICE",
      },
    });
    expect(context.upstream.calls.plan).toHaveLength(plannerCalls);
    expect(context.upstream.calls.today).toHaveLength(0);
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.reason).toBe("delivery.no_eligible_answer");
    // The operator asked in words, so the clarification is read back too.
    expect(response.turn.presentation?.presentation).toBe("BOTH");
    expect(response.turn.presentation?.speak).toBe(true);
  });

  it("clarifies an out-of-range referent without a provider call", async () => {
    const context = await withSession();
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    const plannerCalls = context.upstream.calls.plan.length;
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "read the third one to me",
        modality: "VOICE",
      },
    });
    expect(context.upstream.calls.plan).toHaveLength(plannerCalls);
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.reason).toBe("delivery.unknown_referent");
  });

  it("refuses a referent that has no speakable answer", async () => {
    const context = await withSession({
      planError: new AssistantError("unavailable", "Planner is offline."),
      todayError: new AssistantError("unavailable", "Today is offline."),
    });
    await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "A question that cannot be answered",
        modality: "TEXT",
      },
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: "77777777-7777-4777-8777-777777777778",
        text: "read the first one to me",
        modality: "TEXT",
      },
    });
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.reason).toBe("delivery.ineligible_referent");
  });

  it("suppresses automatic speech for the answer it belongs to", async () => {
    const context = await withSession({
      planError: null,
      plan: intentEnvelope({
        version: 1,
        intents: [
          {
            route: "today.read",
            entity: { kind: "NONE", value: null, confidence: 0.5 },
            time: { kind: "NONE", expression: null, confidence: 1 },
            reference: { kind: "NONE", ordinal: null },
            confidence: 0.5,
            requires_clarification: false,
            clarification: null,
          },
        ],
      }),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today? Don't read it aloud.",
        modality: "VOICE",
      },
    });
    // The question still reached the owning service; only playback is dropped.
    expect(context.upstream.calls.today[0]?.query).toContain(
      "Don't read it aloud",
    );
    expect(response.turn.state).toBe("READY");
    expect(response.turn.presentation?.presentation).toBe("TEXT");
    expect(response.turn.presentation?.speak).toBe(false);
    expect(response.turn.presentation?.delivery).toBe("SUPPRESS");
    expect(response.turn.presentation?.speech_text).toBeNull();
  });

  it("records an explicit cue inside a spoken question and still routes it", async () => {
    const context = await withSession({
      planError: null,
      plan: intentEnvelope({
        version: 1,
        intents: [
          {
            route: "today.read",
            question: "what am I missing today",
            entity: { kind: "NONE", value: null, confidence: 0.5 },
            time: { kind: "NONE", expression: null, confidence: 1 },
            reference: { kind: "NONE", ordinal: null },
            confidence: 0.5,
            requires_clarification: false,
            clarification: null,
          },
        ],
      }),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "what am I missing today, and read it to me",
        modality: "VOICE",
      },
    });
    // The question reached its owning service with the operator's own words.
    expect(context.upstream.calls.today[0]?.query).toBe(
      "what am I missing today",
    );
    expect(response.turn.state).toBe("READY");
    expect(response.turn.presentation?.presentation).toBe("BOTH");
    expect(response.turn.presentation?.speak).toBe(true);
    // Voice Mode is a client default; an explicit request outranks it.
    expect(response.turn.presentation?.delivery).toBe("SPEAK");
  });

  it("prefers suppression over the read-aloud phrase it contains", async () => {
    const context = await withSession({
      planError: null,
      plan: intentEnvelope({
        version: 1,
        intents: [
          {
            route: "today.read",
            question: "what am I missing today",
            entity: { kind: "NONE", value: null, confidence: 0.5 },
            time: { kind: "NONE", expression: null, confidence: 1 },
            reference: { kind: "NONE", ordinal: null },
            confidence: 0.5,
            requires_clarification: false,
            clarification: null,
          },
        ],
      }),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "what am I missing today, but don't read it to me",
        modality: "VOICE",
      },
    });
    expect(response.turn.presentation?.delivery).toBe("SUPPRESS");
    expect(response.turn.presentation?.speak).toBe(false);
    expect(response.turn.presentation?.speech_text).toBeNull();
    // The visual answer is untouched.
    expect(
      response.turn.presentation?.blocks.some(
        (block) => block.kind === "ANSWER",
      ),
    ).toBe(true);
  });

  it("speaks a silent long answer as whole sentences on explicit request", async () => {
    const context = await withSession({
      today: {
        intent: "today",
        answer:
          "First task is due at nine. Second task is due at noon. ".repeat(20),
        items: [],
        supported_queries: ["What am I missing today?"],
        details: [],
      },
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    expect(response.turn.presentation?.speak).toBe(false);
    const spoken = spokenTextForTurn(response.turn);
    expect(spoken).not.toBeNull();
    expect(spoken?.length).toBeLessThanOrEqual(LIMITS.maxSpeechLength);
    // The summary keeps whole sentences instead of slicing the answer.
    expect(spoken?.startsWith("First task is due at nine.")).toBe(true);
    expect(spoken?.endsWith(".")).toBe(true);
  });

  it("acknowledges a standalone suppression cue without a provider call", async () => {
    const context = await withSession();
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "don't read it aloud",
        modality: "VOICE",
      },
    });
    expect(context.upstream.calls.plan).toHaveLength(0);
    expect(context.upstream.calls.today).toHaveLength(0);
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.reason).toBe("delivery.suppressed");
    expect(response.turn.presentation?.presentation).toBe("TEXT");
    expect(response.turn.presentation?.speak).toBe(false);
  });

  it("resolves its claim when a delivery turn cannot read the session", async () => {
    const context = await withSession();
    const failure = new AssistantError(
      "unavailable",
      "The conversation store is unavailable.",
    );
    const listTurns = vi.spyOn(context.store, "listTurns");
    listTurns.mockRejectedValueOnce(failure);

    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "read it to me",
        modality: "VOICE",
      },
    });
    expect(listTurns).toHaveBeenCalledTimes(1);
    // A store failure is a qualified turn, never a stranded durable claim.
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(response.turn.plan?.intents[0]?.kind).toBe("assistant.delivery");
    expect(context.upstream.calls.plan).toHaveLength(0);
    expect(context.upstream.calls.today).toHaveLength(0);

    // The same request replays the saved turn instead of waiting on the claim.
    const replay = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "read it to me",
        modality: "VOICE",
      },
    });
    expect(replay.replay).toBe(true);
    expect(replay.turn.state).toBe("UNAVAILABLE");
  });
});
