import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createAssistantRuntime } from "./runtime";
import { createPostgresStore, type SqlExecutor } from "./store";
import { createFakeUpstream } from "./testing/fakes";

/**
 * Persistence integration coverage.
 *
 * Runs only when ASSISTANT_TEST_DATABASE_URL points at a disposable local
 * PostgreSQL; it seeds its own user, workspace and SPEC-005 session row and
 * removes them again. Without that variable the suite reports a skip instead
 * of pretending the migration was exercised.
 */
const connectionString = process.env.ASSISTANT_TEST_DATABASE_URL;
const migrationSql = readFileSync(
  new URL("../migrations/0001_assistant_runtime.sql", import.meta.url),
  "utf8",
);

const suite = connectionString ? describe : describe.skip;

suite("assistant persistence against disposable PostgreSQL", () => {
  const client = new Client({ connectionString });
  const userId = randomUUID();
  const workspaceId = randomUUID();
  const navoxSessionId = randomUUID();
  const store = createPostgresStore({
    query: async (text, values) => {
      const result = await client.query(text, values ? [...values] : undefined);
      return { rows: result.rows };
    },
  } satisfies SqlExecutor);

  beforeAll(async () => {
    await client.connect();
    await client.query(migrationSql);
    await client.query("INSERT INTO users (id, email) VALUES ($1, $2)", [
      userId,
      `m1-${userId}@example.test`,
    ]);
    await client.query("INSERT INTO workspaces (id, name) VALUES ($1, $2)", [
      workspaceId,
      "M1 disposable workspace",
    ]);
    await client.query(
      `INSERT INTO assistant_sessions (id, workspace_id, user_id, next_sequence, mode, status)
       VALUES ($1, $2, $3, 1, 'PERSONAL', 'active')`,
      [navoxSessionId, workspaceId, userId],
    );
  });

  afterAll(async () => {
    await client.query(
      "DELETE FROM assistant_runtime_sessions WHERE user_id = $1",
      [userId],
    );
    await client.query("DELETE FROM assistant_sessions WHERE user_id = $1", [
      userId,
    ]);
    await client.query("DELETE FROM workspaces WHERE id = $1", [workspaceId]);
    await client.query("DELETE FROM users WHERE id = $1", [userId]);
    await client.end();
  });

  it("persists a bounded session and fences reads by scope", async () => {
    const scope = { user_id: userId, workspace_id: workspaceId };
    const created = await store.createSession({
      id: randomUUID(),
      navox_session_id: navoxSessionId,
      scope,
      created_at: "2026-09-30T12:00:00.000Z",
      expires_at: "2026-10-30T12:00:00.000Z",
    });
    expect(created.next_sequence).toBe(1);
    await expect(
      store.readSession({ session_id: created.id, scope }),
    ).resolves.toMatchObject({
      id: created.id,
    });
    const intruder = { user_id: userId, workspace_id: randomUUID() };
    await expect(
      store.readSession({ session_id: created.id, scope: intruder }),
    ).resolves.toBeNull();
    await expect(
      store.deleteSession({ session_id: created.id, scope: intruder }),
    ).resolves.toBe(false);
  });

  it("replays one turn per request id and refuses a changed payload", async () => {
    const scope = { user_id: userId, workspace_id: workspaceId };
    const session = await store.readSession({
      session_id: (
        await client.query(
          "SELECT id FROM assistant_runtime_sessions WHERE user_id = $1",
          [userId],
        )
      ).rows[0].id as string,
      scope,
    });
    expect(session).not.toBeNull();
    if (!session) return;

    const requestId = randomUUID();
    const sequence = await store.reserveSequence({
      session_id: session.id,
      scope,
      now: "2026-09-30T12:01:00.000Z",
    });
    await store.insertTurn({
      id: randomUUID(),
      session_id: session.id,
      scope,
      sequence,
      modality: "TEXT",
      state: "READY",
      request_id: requestId,
      request_fingerprint: "a".repeat(64),
      question: "What am I missing today?",
      response_text: "1 item needs attention now.",
      plan: {
        version: 1,
        intents: [
          {
            kind: "today.read",
            capability_id: "today.read",
            question: "What am I missing today?",
            confidence: 1,
            entity: { kind: "NONE", value: null, confidence: 1 },
            time: { kind: "NONE", expression: null, confidence: 1 },
            reference: { kind: "NONE", ordinal: null, turn_id: null },
            requires_clarification: false,
            clarification: null,
          },
        ],
      },
      decision: {
        kind: "DELEGATE",
        capability_id: "today.read",
        target: "today.query",
        reason: "today.today",
        requires_approval: false,
        action_state: "NONE",
        action_id: null,
        response_state: "READY",
      },
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
      },
      action_refs: [],
      created_at: "2026-09-30T12:01:00.000Z",
    });

    const saved = await store.findTurnByRequest({
      session_id: session.id,
      scope,
      request_id: requestId,
    });
    expect(saved?.request_fingerprint).toBe("a".repeat(64));
    expect(saved?.plan.intents[0]?.capability_id).toBe("today.read");

    await expect(
      store.insertTurn({
        id: randomUUID(),
        session_id: session.id,
        scope,
        sequence: sequence + 1,
        modality: "TEXT",
        state: "READY",
        request_id: requestId,
        request_fingerprint: "b".repeat(64),
        question: "A different question",
        response_text: null,
        plan: saved?.plan as never,
        decision: saved?.decision as never,
        presentation: saved?.presentation as never,
        action_refs: [],
        created_at: "2026-09-30T12:02:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "conflict" });

    await expect(
      client.query(
        `INSERT INTO assistant_runtime_turns
           (id, session_id, workspace_id, user_id, sequence, modality, state, request_id,
            request_fingerprint, question, response_text, plan, decision, presentation,
            action_refs, created_at)
         VALUES ($1,$2,$3,$4,$5,'TEXT','READY',$6,$7,$8,NULL,'{}','{}','{}','[]',now())`,
        [
          randomUUID(),
          session.id,
          workspaceId,
          userId,
          99,
          randomUUID(),
          "c".repeat(64),
          "x".repeat(501),
        ],
      ),
    ).rejects.toThrow(/check constraint/i);

    await store.deleteSession({ session_id: session.id, scope });
    const remaining = await client.query(
      "SELECT count(*)::int AS count FROM assistant_runtime_turns WHERE session_id = $1",
      [session.id],
    );
    expect(remaining.rows[0].count).toBe(0);
  });

  it("persists a typed turn through the runtime and replays it once", async () => {
    const endToEndSessionId = randomUUID();
    await client.query(
      `INSERT INTO assistant_sessions (id, workspace_id, user_id, next_sequence, mode, status)
       VALUES ($1, $2, $3, 1, 'PERSONAL', 'active')`,
      [endToEndSessionId, workspaceId, userId],
    );
    const upstream = createFakeUpstream({
      account: { user_id: userId, workspace_id: workspaceId, email: null },
      today: {
        intent: "today",
        answer: "1 item needs attention now.",
        items: [],
        supported_queries: ["What am I missing today?"],
        details: [],
      },
    });
    upstream.createAssistantSession = async () => endToEndSessionId;
    const runtime = createAssistantRuntime({ store, upstream });
    const session = await runtime.createSession({
      cookie: "navox_session=abc",
    });
    const body = {
      request_id: randomUUID(),
      text: "What am I missing today?",
      modality: "TEXT",
    };

    const turn = await runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body,
    });
    expect(turn.replay).toBe(false);
    expect(turn.turn.state).toBe("READY");
    expect(turn.turn.sequence).toBe(1);
    expect(turn.turn.presentation?.speak).toBe(false);

    const stored = await client.query(
      `SELECT question, response_text, request_fingerprint, state
         FROM assistant_runtime_turns WHERE session_id = $1`,
      [session.id],
    );
    expect(stored.rows).toHaveLength(1);
    expect(stored.rows[0].question).toBe("What am I missing today?");
    expect(stored.rows[0].request_fingerprint).toHaveLength(64);

    const replay = await runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body,
    });
    expect(replay.replay).toBe(true);
    expect(replay.turn.id).toBe(turn.turn.id);
    const afterReplay = await client.query(
      "SELECT count(*)::int AS count FROM assistant_runtime_turns WHERE session_id = $1",
      [session.id],
    );
    expect(afterReplay.rows[0].count).toBe(1);

    await runtime.deleteSession({
      cookie: "navox_session=abc",
      session_id: session.id,
    });
    const afterDelete = await client.query(
      "SELECT count(*)::int AS count FROM assistant_runtime_turns WHERE session_id = $1",
      [session.id],
    );
    expect(afterDelete.rows[0].count).toBe(0);
  });

  it("calls Today once for concurrent duplicates of a delayed request", async () => {
    const sessionId = randomUUID();
    await client.query(
      `INSERT INTO assistant_sessions (id, workspace_id, user_id, next_sequence, mode, status)
       VALUES ($1, $2, $3, 1, 'PERSONAL', 'active')`,
      [sessionId, workspaceId, userId],
    );
    let release!: (value: {
      intent: "today";
      answer: string;
      items: never[];
      supported_queries: never[];
      details: never[];
    }) => void;
    const gate = new Promise<Parameters<typeof release>[0]>((resolve) => {
      release = resolve;
    });
    let todayCalls = 0;
    const upstream = createFakeUpstream({
      account: { user_id: userId, workspace_id: workspaceId, email: null },
      today: async () => {
        todayCalls += 1;
        return gate;
      },
    });
    upstream.createAssistantSession = async () => sessionId;
    const runtime = createAssistantRuntime({ store, upstream });
    const session = await runtime.createSession({
      cookie: "navox_session=abc",
    });
    const body = {
      request_id: randomUUID(),
      text: "What am I missing today?",
      modality: "TEXT" as const,
    };

    const owner = runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body,
    });
    // Wait until the owner holds the durable claim and is inside Today.
    const deadline = Date.now() + 2000;
    while (todayCalls === 0) {
      if (Date.now() > deadline) throw new Error("Today was never called");
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    const duplicate = runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body,
    });
    release({
      intent: "today",
      answer: "1 item needs attention now.",
      items: [],
      supported_queries: [],
      details: [],
    });
    const [first, second] = await Promise.all([owner, duplicate]);

    expect(todayCalls).toBe(1);
    expect(first.turn.id).toBe(second.turn.id);
    expect([first.replay, second.replay]).toEqual([false, true]);
    const stored = await client.query(
      "SELECT count(*)::int AS count FROM assistant_runtime_turns WHERE session_id = $1",
      [session.id],
    );
    expect(stored.rows[0].count).toBe(1);
    const claims = await client.query(
      "SELECT status, turn_id FROM assistant_runtime_requests WHERE session_id = $1",
      [session.id],
    );
    expect(claims.rows).toHaveLength(1);
    expect(claims.rows[0].status).toBe("RESOLVED");
    expect(claims.rows[0].turn_id).toBe(first.turn.id);
  });

  it("enforces the turn limit during sequence allocation", async () => {
    const scope = { user_id: userId, workspace_id: workspaceId };
    const upstreamId = randomUUID();
    await client.query(
      `INSERT INTO assistant_sessions (id, workspace_id, user_id, next_sequence, mode, status)
       VALUES ($1, $2, $3, 1, 'PERSONAL', 'active')`,
      [upstreamId, workspaceId, userId],
    );
    const session = await store.createSession({
      id: randomUUID(),
      navox_session_id: upstreamId,
      scope,
      created_at: "2026-09-30T12:00:00.000Z",
      expires_at: "2026-10-30T12:00:00.000Z",
    });
    await client.query(
      "UPDATE assistant_runtime_sessions SET next_sequence = 200 WHERE id = $1",
      [session.id],
    );
    const input = {
      session_id: session.id,
      scope,
      now: "2026-09-30T12:01:00.000Z",
    };
    await expect(store.reserveSequence(input)).resolves.toBe(200);
    await expect(store.reserveSequence(input)).rejects.toMatchObject({
      code: "unsupported",
    });
    const result = await client.query(
      "SELECT next_sequence FROM assistant_runtime_sessions WHERE id = $1",
      [session.id],
    );
    expect(result.rows[0].next_sequence).toBe(201);
  });
});
