import { describe, expect, it } from "vitest";
import { AssistantError } from "./errors";
import { createPostgresStore, type SqlExecutor } from "./store";

interface Call {
  text: string;
  values: readonly unknown[];
}

function executor(
  handler: (sql: string, values: readonly unknown[]) => { rows: unknown[] },
): { executor: SqlExecutor; calls: Call[] } {
  const calls: Call[] = [];
  return {
    calls,
    executor: {
      async query<R>(text: string, values: readonly unknown[] = []) {
        calls.push({ text, values });
        return handler(text, values) as { rows: R[] };
      },
    },
  };
}

const scope = {
  user_id: "11111111-1111-4111-8111-111111111111",
  workspace_id: "22222222-2222-4222-8222-222222222222",
};
const sessionId = "66666666-6666-4666-8666-666666666666";
const requestId = "77777777-7777-4777-8777-777777777777";

const validPlan = {
  version: 1,
  intents: [
    {
      kind: "today.read",
      capability_id: "today.read",
      question: "What is today?",
      confidence: 1,
    },
  ],
};
const validDecision = {
  kind: "DELEGATE",
  capability_id: "today.read",
  target: "today.query",
  reason: "today.today",
  requires_approval: false,
  action_state: "NONE",
  action_id: null,
  response_state: "READY",
};
const validPresentation = {
  presentation: "TEXT",
  speak: false,
  speech_text: null,
  blocks: [{ kind: "ANSWER", text: "Nothing needs your attention." }],
};

function turnRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "88888888-8888-4888-8888-888888888888",
    session_id: sessionId,
    sequence: 1,
    modality: "TEXT",
    state: "READY",
    request_id: requestId,
    request_fingerprint: "a".repeat(64),
    question: "What is today?",
    response_text: "Nothing needs your attention.",
    plan: validPlan,
    decision: validDecision,
    presentation: validPresentation,
    action_refs: [],
    created_at: new Date("2026-09-30T12:00:00.000Z"),
    ...overrides,
  };
}

function claimRow(overrides: Record<string, unknown> = {}) {
  return {
    request_id: requestId,
    request_fingerprint: "a".repeat(64),
    status: "PENDING",
    turn_id: null,
    updated_at: new Date("2026-09-30T12:00:00.000Z"),
    ...overrides,
  };
}

describe("PostgreSQL assistant store", () => {
  it("fences every read by session, workspace and user", async () => {
    const { executor: sql, calls } = executor(() => ({ rows: [] }));
    const store = createPostgresStore(sql);
    await store.readSession({ session_id: sessionId, scope });
    await store.listTurns({ session_id: sessionId, scope });
    await store.findTurnByRequest({
      session_id: sessionId,
      scope,
      request_id: requestId,
    });
    await store.deleteSession({ session_id: sessionId, scope });
    for (const call of calls) {
      expect(call.text).toMatch(/workspace_id = \$\d+ AND user_id = \$\d+/);
      expect(call.values).toContain(scope.workspace_id);
      expect(call.values).toContain(scope.user_id);
    }
  });

  it("re-validates a stored turn instead of trusting the row", async () => {
    const { executor: sql } = executor(() => ({ rows: [turnRow()] }));
    const store = createPostgresStore(sql);
    const turn = await store.findTurnByRequest({
      session_id: sessionId,
      scope,
      request_id: requestId,
    });
    expect(turn?.plan.intents[0]?.question).toBe("What is today?");
    expect(turn?.created_at).toBe("2026-09-30T12:00:00.000Z");
  });

  it("keeps a runtime-bound turn selector readable and rejects a foreign row", async () => {
    const bound = {
      ...validPlan,
      intents: [
        {
          ...validPlan.intents[0],
          kind: "email.search",
          capability_id: "email.search",
          reference: {
            kind: "RECENT_TURN",
            ordinal: 1,
            turn_id: "99999999-9999-4999-8999-999999999999",
          },
        },
      ],
    };
    const { executor: sql } = executor(() => ({
      rows: [turnRow({ plan: bound })],
    }));
    const store = createPostgresStore(sql);
    const turn = await store.findTurnByRequest({
      session_id: sessionId,
      scope,
      request_id: requestId,
    });
    expect(turn?.plan.intents[0]?.reference).toEqual({
      kind: "RECENT_TURN",
      ordinal: 1,
      turn_id: "99999999-9999-4999-8999-999999999999",
    });

    const corrupt = {
      ...validPlan,
      intents: [
        {
          ...validPlan.intents[0],
          kind: "files.delete",
        },
      ],
    };
    const failing = createPostgresStore(
      executor(() => ({ rows: [turnRow({ plan: corrupt })] })).executor,
    );
    await expect(
      failing.findTurnByRequest({
        session_id: sessionId,
        scope,
        request_id: requestId,
      }),
    ).rejects.toMatchObject({ code: "unavailable" });
  });

  it("fails closed when a stored row no longer matches the contracts", async () => {
    const { executor: sql } = executor(() => ({
      rows: [turnRow({ presentation: { presentation: "SHOUT", blocks: [] } })],
    }));
    const store = createPostgresStore(sql);
    await expect(
      store.findTurnByRequest({
        session_id: sessionId,
        scope,
        request_id: requestId,
      }),
    ).rejects.toMatchObject({ code: "unavailable" });
  });

  it("reports a concurrent insert race as a conflict", async () => {
    const { executor: sql } = executor(() => {
      throw Object.assign(new Error("duplicate key"), { code: "23505" });
    });
    const store = createPostgresStore(sql);
    await expect(
      store.insertTurn({
        id: "99999999-9999-4999-8999-999999999999",
        session_id: sessionId,
        scope,
        sequence: 1,
        modality: "TEXT",
        state: "READY",
        request_id: requestId,
        request_fingerprint: "b".repeat(64),
        question: "What is today?",
        response_text: null,
        plan: validPlan as never,
        decision: validDecision as never,
        presentation: validPresentation as never,
        action_refs: [],
        created_at: "2026-09-30T12:00:00.000Z",
      }),
    ).rejects.toBeInstanceOf(AssistantError);
  });

  it("refuses to reserve a sequence for a missing or expired session", async () => {
    const { executor: sql, calls } = executor(() => ({ rows: [] }));
    const store = createPostgresStore(sql);
    await expect(
      store.reserveSequence({
        session_id: sessionId,
        scope,
        now: "2026-09-30T12:00:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "not_found" });
    expect(calls[0]?.text).toMatch(/expires_at > \$\d+/);
    expect(calls[0]?.text).toMatch(/next_sequence <= \$\d+/);
  });

  it("bounds retention purges in the database, not in memory", async () => {
    const { executor: sql, calls } = executor(() => ({
      rows: [{ id: sessionId }, { id: "another" }],
    }));
    const store = createPostgresStore(sql);
    await expect(
      store.purgeExpired("2026-09-30T12:00:00.000Z", 500),
    ).resolves.toBe(2);
    expect(calls[0]?.text).toMatch(/WHERE expires_at <= \$1/);
    expect(calls[0]?.text).toMatch(/LIMIT \$2/);
    expect(calls[0]?.values).toEqual(["2026-09-30T12:00:00.000Z", 500]);
  });
});

describe("PostgreSQL request claims", () => {
  const claim = {
    session_id: sessionId,
    scope,
    request_id: requestId,
    request_fingerprint: "a".repeat(64),
    now: "2026-09-30T12:00:00.000Z",
    stale_before: "2026-09-30T11:59:30.000Z",
  };

  it("clears a stale claim before inserting, and reports ownership", async () => {
    const { executor: sql, calls } = executor((text) =>
      text.startsWith("INSERT INTO assistant_runtime_requests")
        ? { rows: [claimRow()] }
        : { rows: [] },
    );
    const store = createPostgresStore(sql);
    const result = await store.claimRequest(claim);
    expect(result.created).toBe(true);
    expect(result.claim?.status).toBe("PENDING");
    expect(result.claim?.turn_id).toBeNull();
    expect(calls[0]?.text).toMatch(/DELETE FROM assistant_runtime_requests/);
    expect(calls[0]?.text).toMatch(/status = 'PENDING' AND updated_at <= \$5/);
    expect(calls[1]?.text).toMatch(
      /ON CONFLICT \(session_id, request_id\) DO NOTHING/,
    );
    for (const call of calls) {
      expect(call.values).toContain(scope.workspace_id);
      expect(call.values).toContain(scope.user_id);
    }
  });

  it("returns the existing claim when another worker owns the request", async () => {
    const { executor: sql } = executor((text) =>
      text.startsWith("INSERT INTO assistant_runtime_requests")
        ? { rows: [] }
        : text.startsWith("SELECT")
          ? { rows: [claimRow({ status: "PENDING" })] }
          : { rows: [] },
    );
    const store = createPostgresStore(sql);
    const result = await store.claimRequest(claim);
    expect(result.created).toBe(false);
    expect(result.claim?.request_fingerprint).toBe("a".repeat(64));
  });

  it("reports a claim that vanished between insert and read as unowned", async () => {
    const { executor: sql } = executor(() => ({ rows: [] }));
    const store = createPostgresStore(sql);
    const result = await store.claimRequest(claim);
    expect(result).toEqual({ created: false, claim: null });
  });

  it("resolves a claim with its turn and refuses an unknown status", async () => {
    const { executor: sql, calls } = executor(() => ({ rows: [] }));
    const store = createPostgresStore(sql);
    await store.resolveClaimRequest({
      session_id: sessionId,
      scope,
      request_id: requestId,
      turn_id: "88888888-8888-4888-8888-888888888888",
      now: "2026-09-30T12:00:01.000Z",
    });
    expect(calls[0]?.text).toMatch(/SET status = 'RESOLVED', turn_id = \$5/);
    expect(calls[0]?.text).toMatch(/AND status = 'PENDING'/);

    const corrupted = createPostgresStore(
      executor(() => ({ rows: [claimRow({ status: "ORPHANED" })] })).executor,
    );
    await expect(
      corrupted.readClaimRequest({
        session_id: sessionId,
        scope,
        request_id: requestId,
      }),
    ).rejects.toMatchObject({ code: "unavailable" });
  });

  it("releases only a pending claim", async () => {
    const { executor: sql, calls } = executor(() => ({ rows: [] }));
    const store = createPostgresStore(sql);
    await store.releaseClaimRequest({
      session_id: sessionId,
      scope,
      request_id: requestId,
    });
    expect(calls[0]?.text).toMatch(/DELETE FROM assistant_runtime_requests/);
    expect(calls[0]?.text).toMatch(/AND status = 'PENDING'/);
  });
});
