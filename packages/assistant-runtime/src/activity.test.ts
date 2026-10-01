import type { PlannedIntent } from "@navox/contracts";
import { describe, expect, it } from "vitest";
import {
  ACTION_HISTORY_AGE_NOTE_MS,
  ACTION_HISTORY_LIMIT,
  type ActionHistoryRecord,
  actionEventAt,
  actionHistoryDayWindow,
  actionHistorySelector,
  answerActionHistory,
  classifyAction,
  parseActionHistory,
} from "./activity";
import { AssistantError } from "./errors";
import { createAssistantRuntime } from "./runtime";
import {
  createFakeUpstream,
  createMemoryStore,
  intentEnvelope,
  REQUEST_ID,
  SCOPE,
} from "./testing/fakes";

const NOW = new Date("2026-10-01T02:00:00.000Z");
const COOKIE = "navox_session=abc123";

function ids() {
  let counter = 0;
  return () => {
    counter += 1;
    return `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`;
  };
}

function record(
  changes: Partial<ActionHistoryRecord> = {},
): ActionHistoryRecord {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    action_type: "gmail.send",
    provider: "google",
    status: "completed",
    created_at: "2026-10-01T01:00:00.000Z",
    executed_at: "2026-10-01T01:00:05.000Z",
    verified_at: "2026-10-01T01:00:05.000Z",
    ...changes,
  };
}

/** One row in the shape the existing authenticated `GET /actions` returns. */
function apiRow(changes: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    commitment_id: null,
    plan_id: "22222222-2222-4222-8222-222222222222",
    provider: "google",
    action_type: "gmail.send",
    risk_level: "R3",
    requires_approval: true,
    status: "completed",
    payload: {
      body_text: "SECRET-PAYLOAD-TOKEN",
      to: ["stranger@example.com"],
    },
    payload_hash: "a".repeat(64),
    result: { message_id: "message-1" },
    policy_reason: "approved_and_provider_verified",
    created_at: "2026-10-01T01:00:00.000Z",
    started_at: "2026-10-01T01:00:01.000Z",
    executed_at: "2026-10-01T01:00:05.000Z",
    verified_at: "2026-10-01T01:00:05.000Z",
    approval: null,
    workflow_status: "completed",
    ...changes,
  };
}

function plan(intent: Record<string, unknown> = {}) {
  return {
    version: 1,
    intents: [
      {
        route: "action.history",
        entity: { kind: "NONE", value: null, confidence: 1 },
        time: { kind: "RELATIVE", expression: "today", confidence: 0.9 },
        reference: { kind: "NONE", ordinal: null },
        confidence: 0.9,
        requires_clarification: false,
        clarification: null,
        ...intent,
      },
    ],
  };
}

function setup(overrides: { plan?: unknown; actions?: unknown } = {}) {
  const store = createMemoryStore();
  const upstream = createFakeUpstream({
    planError: null,
    plan: intentEnvelope(overrides.plan ?? plan()),
    actions: overrides.actions ?? [],
  });
  const runtime = createAssistantRuntime({
    store,
    upstream,
    now: () => NOW,
    newId: ids(),
  });
  return { store, upstream, runtime };
}

async function withSession(
  overrides: { plan?: unknown; actions?: unknown } = {},
) {
  const context = setup(overrides);
  const session = await context.runtime.createSession({ cookie: COOKIE });
  return { ...context, sessionId: session.id };
}

function blocksText(blocks: unknown): string {
  return JSON.stringify(blocks);
}

describe("action history source parsing", () => {
  it("binds only the minimal facts and never the payload", () => {
    const [bound] = parseActionHistory([apiRow()], {
      scope: SCOPE,
      now: NOW,
    });
    expect(bound).toEqual({
      id: "11111111-1111-4111-8111-111111111111",
      action_type: "gmail.send",
      provider: "google",
      status: "completed",
      created_at: "2026-10-01T01:00:00.000Z",
      executed_at: "2026-10-01T01:00:05.000Z",
      verified_at: "2026-10-01T01:00:05.000Z",
    });
    expect(JSON.stringify(bound)).not.toContain("SECRET-PAYLOAD-TOKEN");
  });

  it("refuses a row that names another workspace or user", () => {
    expect(() =>
      parseActionHistory(
        [apiRow({ workspace_id: "99999999-9999-4999-8999-999999999999" })],
        { scope: SCOPE, now: NOW },
      ),
    ).toThrow(/could not verify/i);
    expect(() =>
      parseActionHistory([apiRow({ user_id: SCOPE.user_id })], {
        scope: SCOPE,
        now: NOW,
      }),
    ).not.toThrow();
  });

  it("refuses malformed, duplicated, oversized and over-claimed rows", () => {
    expect(() =>
      parseActionHistory([apiRow({ status: 7 })], { scope: SCOPE, now: NOW }),
    ).toThrow(/could not verify/i);
    expect(() =>
      parseActionHistory([apiRow(), apiRow()], { scope: SCOPE, now: NOW }),
    ).toThrow(/could not verify/i);
    expect(() =>
      parseActionHistory([apiRow({ verified_at: null })], {
        scope: SCOPE,
        now: NOW,
      }),
    ).not.toThrow();
    // An independent verification timestamp cannot exist on a non-completed row.
    expect(() =>
      parseActionHistory([apiRow({ status: "executing" })], {
        scope: SCOPE,
        now: NOW,
      }),
    ).toThrow(/could not verify/i);
    // Nor can it exist without the execution it verifies, or before it.
    expect(() =>
      parseActionHistory([apiRow({ executed_at: null })], {
        scope: SCOPE,
        now: NOW,
      }),
    ).toThrow(/could not verify/i);
    expect(() =>
      parseActionHistory([apiRow({ executed_at: null, verified_at: null })], {
        scope: SCOPE,
        now: NOW,
      }),
    ).toThrow(/could not verify/i);
    expect(() =>
      parseActionHistory(
        [
          apiRow({
            executed_at: "2026-10-01T01:00:09.000Z",
            verified_at: "2026-10-01T01:00:05.000Z",
          }),
        ],
        { scope: SCOPE, now: NOW },
      ),
    ).toThrow(/could not verify/i);
    expect(() =>
      parseActionHistory(
        Array.from({ length: ACTION_HISTORY_LIMIT + 1 }, (_, index) =>
          apiRow({
            id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
          }),
        ),
        { scope: SCOPE, now: NOW },
      ),
    ).toThrow(/could not verify/i);
  });
});

describe("action history scope", () => {
  const baseIntent: PlannedIntent = {
    kind: "action.history",
    capability_id: "action.history",
    question: "What did you do today?",
    confidence: 1,
    entity: { kind: "NONE", value: null, confidence: 1 },
    time: { kind: "NONE", expression: null, confidence: 1 },
    reference: { kind: "NONE", ordinal: null, turn_id: null },
    requires_clarification: false,
    clarification: null,
  };
  const intent = (changes: Partial<PlannedIntent> = {}): PlannedIntent => ({
    ...baseIntent,
    ...changes,
  });

  it("reads a literal local day and the unbounded recent list", () => {
    expect(
      actionHistorySelector(
        intent({
          time: { kind: "RELATIVE", expression: "today", confidence: 1 },
        }),
      ),
    ).toEqual({ kind: "DAY" });
    expect(actionHistorySelector(intent())).toEqual({ kind: "RECENT" });
  });

  it("clarifies rather than guessing a name or another date", () => {
    expect(
      actionHistorySelector(
        intent({ entity: { kind: "PERSON", value: "Sarah", confidence: 1 } }),
      ),
    ).toBeNull();
    expect(
      actionHistorySelector(
        intent({
          time: { kind: "RELATIVE", expression: "yesterday", confidence: 1 },
        }),
      ),
    ).toBeNull();
    expect(
      actionHistorySelector({
        ...intent(),
        kind: "news.read",
      }),
    ).toBeUndefined();
  });
});

describe("action history classification", () => {
  it("claims done only for an independently verified completion", () => {
    expect(classifyAction(record())).toBe("VERIFIED");
    expect(classifyAction(record({ verified_at: null }))).toBe(
      "EXECUTED_UNVERIFIED",
    );
  });

  it("labels the remaining ledger states honestly", () => {
    const cases: [string, string][] = [
      ["pending", "PENDING_APPROVAL"],
      ["awaiting_approval", "PENDING_APPROVAL"],
      ["approved", "IN_PROGRESS"],
      ["executing", "IN_PROGRESS"],
      ["failed", "FAILED"],
      ["rejected", "DECLINED"],
      ["cancelled", "CANCELLED"],
      ["expired", "EXPIRED"],
      ["uncertain", "UNCERTAIN"],
      ["blocked", "BLOCKED"],
      ["teleported", "UNRECOGNIZED"],
    ];
    for (const [status, expected] of cases) {
      expect(
        classifyAction(record({ status, verified_at: null })),
        status,
      ).toBe(expected);
    }
  });

  it("keeps the live ledger status for an old unresolved row", () => {
    const old = new Date(NOW.getTime() - ACTION_HISTORY_AGE_NOTE_MS - 1);
    const pending = record({
      status: "awaiting_approval",
      created_at: old.toISOString(),
      executed_at: null,
      verified_at: null,
    });
    expect(classifyAction(pending)).toBe("PENDING_APPROVAL");
    const result = answerActionHistory({
      records: [pending],
      scope: { kind: "RECENT" },
      timezone: null,
      now: NOW,
    });
    const text = (result.blocks[0] as { text: string }).text;
    expect(text).toContain("1 waiting on approval");
    expect(text).toContain(
      "that age note is informational and does not change the ledger status",
    );
    expect(text).not.toContain("stale");
  });
});

describe("action history day resolution", () => {
  it("resolves today from the request's IANA zone, not UTC", () => {
    expect(actionHistoryDayWindow(NOW, "America/New_York")).toEqual({
      start: Date.parse("2026-09-30T04:00:00.000Z"),
      end: Date.parse("2026-10-01T04:00:00.000Z"),
    });
    expect(actionHistoryDayWindow(NOW, "UTC")).toEqual({
      start: Date.parse("2026-10-01T00:00:00.000Z"),
      end: Date.parse("2026-10-02T00:00:00.000Z"),
    });
  });

  it("keeps a late local evening action inside the operator's day", () => {
    // 22:00Z is still 30 September in New York, but already 30 September UTC.
    const evening = record({
      created_at: "2026-09-30T22:00:00.000Z",
      executed_at: "2026-09-30T22:00:05.000Z",
      verified_at: "2026-09-30T22:00:05.000Z",
    });
    const answer = (records: ActionHistoryRecord[]) =>
      answerActionHistory({
        records,
        scope: { kind: "DAY" },
        timezone: "America/New_York",
        now: NOW,
      });
    expect(blocksText(answer([evening]).blocks)).toContain(
      "1 verified as done",
    );
    // The same instant is the previous UTC day, so UTC reports nothing today.
    expect(
      blocksText(
        answerActionHistory({
          records: [evening],
          scope: { kind: "DAY" },
          timezone: "UTC",
          now: NOW,
        }).blocks,
      ),
    ).toContain("No actions were recorded");
  });

  it("reports a verified action on its verification day, not its creation day", () => {
    // Prepared the previous local day (30 September), verified today.
    const carriedOver = record({
      // 29 September 22:00 in New York - outside the reporting day window.
      created_at: "2026-09-30T02:00:00.000Z",
      executed_at: "2026-10-01T01:00:00.000Z",
      verified_at: "2026-10-01T01:30:00.000Z",
    });
    expect(actionEventAt(carriedOver)).toBe("2026-10-01T01:30:00.000Z");
    const today = answerActionHistory({
      records: [carriedOver],
      scope: { kind: "DAY" },
      timezone: "America/New_York",
      now: NOW,
    });
    expect(today.state).toBe("READY");
    const carriedText = blocksText(today.blocks);
    expect(carriedText).toContain("1 verified as done");
    expect(carriedText).toContain("2026-10-01T01:30:00.000Z");

    // Created inside the reporting day but verified on the following local
    // day, so the creation day's answer must not claim it.
    const createdTodayVerifiedTomorrow = record({
      id: "33333333-3333-4333-8333-333333333333",
      created_at: "2026-10-01T01:00:00.000Z",
      executed_at: "2026-10-01T01:05:00.000Z",
      verified_at: "2026-10-01T05:00:00.000Z",
    });
    expect(actionEventAt(createdTodayVerifiedTomorrow)).toBe(
      "2026-10-01T05:00:00.000Z",
    );
    expect(
      blocksText(
        answerActionHistory({
          records: [createdTodayVerifiedTomorrow],
          scope: { kind: "DAY" },
          timezone: "America/New_York",
          now: NOW,
        }).blocks,
      ),
    ).toContain("No actions were recorded");
  });

  it("uses the event time for an unverified completion and for other states", () => {
    expect(
      actionEventAt(
        record({ verified_at: null, executed_at: "2026-10-01T01:05:00.000Z" }),
      ),
    ).toBe("2026-10-01T01:05:00.000Z");
    expect(
      actionEventAt(
        record({
          status: "failed",
          executed_at: "2026-09-30T23:00:00.000Z",
          verified_at: null,
        }),
      ),
    ).toBe("2026-09-30T23:00:00.000Z");
    expect(
      actionEventAt(
        record({ status: "pending", executed_at: null, verified_at: null }),
      ),
    ).toBe("2026-10-01T01:00:00.000Z");
  });

  it("refuses a day-scoped request without a usable timezone", () => {
    for (const timezone of [null, "Mars/Olympus"]) {
      const result = answerActionHistory({
        records: [record()],
        scope: { kind: "DAY" },
        timezone,
        now: NOW,
      });
      expect(result.state).toBe("CLARIFY");
      expect(result.decision.kind).toBe("CLARIFY");
      expect(result.decision.reason).toBe("action.history.timezone_required");
      expect(result.blocks[0]).toMatchObject({
        kind: "NOTICE",
        state: "CLARIFY",
      });
    }
  });
});

describe("action history answer bounds", () => {
  it("shows the latest verified activity first even when it was created earlier", () => {
    const result = answerActionHistory({
      records: [
        record({
          id: "33333333-3333-4333-8333-333333333333",
          created_at: "2026-10-01T01:30:00.000Z",
          executed_at: "2026-10-01T01:30:05.000Z",
          verified_at: "2026-10-01T01:30:05.000Z",
        }),
        record({
          created_at: "2026-09-30T01:00:00.000Z",
          executed_at: "2026-10-01T01:40:00.000Z",
          verified_at: "2026-10-01T01:40:05.000Z",
        }),
      ],
      scope: { kind: "RECENT" },
      timezone: null,
      now: NOW,
    });
    expect(result.blocks[1]).toMatchObject({
      kind: "ITEM",
      item: { id: "11111111-1111-4111-8111-111111111111" },
    });
  });

  it("never claims a merely executed action was done", () => {
    const result = answerActionHistory({
      records: [
        record(),
        record({
          id: "33333333-3333-4333-8333-333333333333",
          verified_at: null,
        }),
      ],
      scope: { kind: "RECENT" },
      timezone: null,
      now: NOW,
    });
    const text = blocksText(result.blocks);
    expect(text).toContain("1 verified as done");
    expect(text).toContain("1 executed but not independently verified");
    expect(result.blocks[0]).toMatchObject({ kind: "ANSWER" });
    expect((result.blocks[0] as { text: string }).text).not.toContain(
      "Gmail was sent",
    );
  });

  it("returns an honest empty answer", () => {
    const result = answerActionHistory({
      records: [],
      scope: { kind: "DAY" },
      timezone: "America/New_York",
      now: NOW,
    });
    expect(result.state).toBe("READY");
    expect(blocksText(result.blocks)).toContain("No actions were recorded");
    expect(result.blocks).toHaveLength(1);
  });

  it("qualifies a listing that reached the fetch bound and bounds items", () => {
    const records = Array.from({ length: ACTION_HISTORY_LIMIT }, (_, index) =>
      record({
        id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      }),
    );
    const result = answerActionHistory({
      records,
      scope: { kind: "RECENT" },
      timezone: null,
      now: NOW,
    });
    const text = (result.blocks[0] as { text: string }).text;
    expect(text).toContain("may be partial");
    expect(text).toContain(
      `Showing the 12 most recent of ${ACTION_HISTORY_LIMIT} here.`,
    );
    expect(result.blocks.filter((block) => block.kind === "ITEM")).toHaveLength(
      12,
    );
  });
});

describe("action history routing", () => {
  it("routes a natural question through the structured plan and same session", async () => {
    const context = await withSession({
      actions: [apiRow({ created_at: "2026-10-01T01:00:00.000Z" })],
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did you do today?",
        modality: "TEXT",
        timezone: "America/New_York",
      },
    });
    expect(response.session_id).toBe(context.sessionId);
    expect(response.turn.decision?.capability_id).toBe("action.history");
    expect(response.turn.decision?.target).toBe("actions.query");
    expect(response.turn.decision?.reason).toBe("action.history.day");
    expect(response.turn.decision?.requires_approval).toBe(false);
    expect(response.turn.decision?.action_state).toBe("NONE");
    expect(response.turn.action_refs).toEqual([]);
    expect(context.upstream.calls.actions).toEqual([
      { cookie: COOKIE, limit: ACTION_HISTORY_LIMIT },
    ]);
    expect(context.upstream.calls.today).toEqual([]);
    expect(context.upstream.calls.email).toEqual([]);
    const text = blocksText(response.turn.presentation?.blocks);
    expect(text).toContain("1 verified as done");
    expect(text).toContain("ACTION");
    expect(text).not.toContain("SECRET-PAYLOAD-TOKEN");
    expect(text).not.toContain("stranger@example.com");
  });

  it("clarifies a name filter without touching the ledger", async () => {
    const context = await withSession({
      plan: plan({
        entity: { kind: "PERSON", value: "Sarah", confidence: 0.9 },
      }),
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did you do for Sarah today?",
        modality: "TEXT",
        timezone: "America/New_York",
      },
    });
    expect(response.turn.state).toBe("CLARIFY");
    expect(response.turn.decision?.reason).toBe(
      "action.history.unsupported_selector",
    );
    expect(context.upstream.calls.actions).toEqual([]);
  });

  it("qualifies an upstream failure as UNAVAILABLE", async () => {
    const context = await withSession();
    context.upstream.actionsError = new AssistantError(
      "unavailable",
      "The action ledger is down.",
    );
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did you do today?",
        modality: "TEXT",
        timezone: "America/New_York",
      },
    });
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(response.turn.decision?.kind).toBe("UNAVAILABLE");
    expect(response.turn.decision?.reason).toBe("activity.unavailable");
  });

  it("never renders a malformed ledger row as an answer", async () => {
    const context = await withSession({
      actions: [
        apiRow({ verified_at: "2026-10-01T01:00:05.000Z", status: "failed" }),
      ],
    });
    const response = await context.runtime.submitTurn({
      cookie: COOKIE,
      session_id: context.sessionId,
      body: {
        request_id: REQUEST_ID,
        text: "What did you do today?",
        modality: "TEXT",
        timezone: "America/New_York",
      },
    });
    expect(response.turn.state).toBe("UNAVAILABLE");
    expect(response.turn.decision?.reason).toBe("activity.unavailable");
  });
});
