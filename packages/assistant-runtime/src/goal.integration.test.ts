import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { Client, Pool } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { GOAL_DETAIL } from "./goals";
import { createPostgresStore, type SqlExecutor } from "./store";
import { createGoalActivities } from "./temporal/activities";

/**
 * Goal persistence integration coverage.
 *
 * Runs only when ASSISTANT_TEST_DATABASE_URL points at a disposable
 * PostgreSQL. It seeds its own users, workspace and SPEC-005 session rows and
 * removes them again, so it never touches another operator's data.
 */
const connectionString = process.env.ASSISTANT_TEST_DATABASE_URL;
const migrations = [
  "../migrations/0001_assistant_runtime.sql",
  "../migrations/0002_assistant_goals.sql",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));

const suite = connectionString ? describe : describe.skip;

suite("assistant goals against disposable PostgreSQL", () => {
  const client = new Client({ connectionString });
  const store = createPostgresStore({
    query: async (text, values) => {
      const result = await client.query(text, values ? [...values] : undefined);
      return { rows: result.rows };
    },
  } satisfies SqlExecutor);
  /**
   * A real pool, so the budget regression runs the claims on several
   * connections at once instead of being serialised by one client.
   */
  const pool = new Pool({ connectionString, max: 4 });
  const concurrentStore = createPostgresStore({
    query: async (text, values) => {
      const result = await pool.query(text, values ? [...values] : undefined);
      return { rows: result.rows };
    },
  } satisfies SqlExecutor);
  const userId = randomUUID();
  const workspaceId = randomUUID();
  const otherUserId = randomUUID();
  const otherWorkspaceId = randomUUID();
  const activities = createGoalActivities(store);
  const scope = { user_id: userId, workspace_id: workspaceId };

  async function seedSession(
    sessionUserId: string,
    sessionWorkspaceId: string,
  ): Promise<string> {
    const upstream = randomUUID();
    await client.query(
      `INSERT INTO assistant_sessions (id, workspace_id, user_id, next_sequence, mode, status)
       VALUES ($1, $2, $3, 1, 'PERSONAL', 'active')`,
      [upstream, sessionWorkspaceId, sessionUserId],
    );
    const session = await store.createSession({
      id: randomUUID(),
      navox_session_id: upstream,
      scope: { user_id: sessionUserId, workspace_id: sessionWorkspaceId },
      created_at: "2026-10-01T12:00:00.000Z",
      expires_at: "2026-10-31T12:00:00.000Z",
    });
    return session.id;
  }

  async function seedTurn(sessionId: string, sessionScope = scope) {
    const sequence = await store.reserveSequence({
      session_id: sessionId,
      scope: sessionScope,
      now: "2026-10-01T12:01:00.000Z",
    });
    return store.insertTurn({
      id: randomUUID(),
      session_id: sessionId,
      scope: sessionScope,
      sequence,
      modality: "TEXT",
      state: "READY",
      request_id: randomUUID(),
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
        delivery: "AUTOMATIC",
        blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
      },
      action_refs: [],
      created_at: "2026-10-01T12:01:00.000Z",
    });
  }

  beforeAll(async () => {
    await client.connect();
    for (const sql of migrations) await client.query(sql);
    for (const [id, workspace, label] of [
      [userId, workspaceId, "m14b"],
      [otherUserId, otherWorkspaceId, "m14b-other"],
    ] as const) {
      await client.query("INSERT INTO users (id, email) VALUES ($1, $2)", [
        id,
        `${label}-${id}@example.test`,
      ]);
      await client.query("INSERT INTO workspaces (id, name) VALUES ($1, $2)", [
        workspace,
        `M14B disposable workspace (${label})`,
      ]);
    }
  });

  afterAll(async () => {
    await client.query(
      "DELETE FROM assistant_runtime_sessions WHERE user_id = ANY($1::uuid[])",
      [[userId, otherUserId]],
    );
    await client.query(
      "DELETE FROM assistant_sessions WHERE user_id = ANY($1::uuid[])",
      [[userId, otherUserId]],
    );
    await client.query("DELETE FROM workspaces WHERE id = ANY($1::uuid[])", [
      [workspaceId, otherWorkspaceId],
    ]);
    await client.query("DELETE FROM users WHERE id = ANY($1::uuid[])", [
      [userId, otherUserId],
    ]);
    await pool.end().catch(() => {});
    await client.end();
  });

  it("stores one goal per saved turn and fences every read", async () => {
    const sessionId = await seedSession(userId, workspaceId);
    const turn = await seedTurn(sessionId);
    const created = await store.createGoal({
      id: randomUUID(),
      session_id: sessionId,
      scope,
      kind: "BRIEFING",
      source_turn_id: turn.id,
      action_id: null,
      created_at: "2026-10-01T12:02:00.000Z",
    });
    expect(created.created).toBe(true);
    expect(created.goal.dispatch_state).toBe("NOT_DISPATCHED");
    expect(created.goal.detail).toBe(GOAL_DETAIL.pendingDispatch);

    // A replay of the same turn returns the same row instead of a duplicate.
    const replay = await store.createGoal({
      id: randomUUID(),
      session_id: sessionId,
      scope,
      kind: "BRIEFING",
      source_turn_id: turn.id,
      action_id: null,
      created_at: "2026-10-01T12:03:00.000Z",
    });
    expect(replay.created).toBe(false);
    expect(replay.goal.id).toBe(created.goal.id);
    const rows = await client.query(
      "SELECT count(*)::int AS count FROM assistant_runtime_goals WHERE source_turn_id = $1",
      [turn.id],
    );
    expect(rows.rows[0].count).toBe(1);

    await expect(
      store.readGoal({ goal_id: created.goal.id, scope }),
    ).resolves.toMatchObject({ id: created.goal.id, kind: "BRIEFING" });
    const intruder = { user_id: userId, workspace_id: randomUUID() };
    await expect(
      store.readGoal({ goal_id: created.goal.id, scope: intruder }),
    ).resolves.toBeNull();
    await expect(
      store.listGoals({ session_id: sessionId, scope: intruder }),
    ).resolves.toEqual([]);
  });

  it("refuses to change a goal whose saved turn another account owns", async () => {
    const ownSession = await seedSession(userId, workspaceId);
    const foreignSession = await seedSession(otherUserId, otherWorkspaceId);
    const foreignTurn = await seedTurn(foreignSession, {
      user_id: otherUserId,
      workspace_id: otherWorkspaceId,
    });
    // The goal row belongs to this account but names the other account's turn.
    await client.query(
      `INSERT INTO assistant_runtime_goals
         (id, session_id, workspace_id, user_id, kind, status, dispatch_state,
          source_turn_id, action_id, detail, attempts, verify_attempts,
          created_at, updated_at, completed_at)
       VALUES ($1, $2, $3, $4, 'BRIEFING', 'PENDING', 'DISPATCHED', $5, NULL, $6, 0, 0, $7, $7, NULL)`,
      [
        randomUUID(),
        ownSession,
        workspaceId,
        userId,
        foreignTurn.id,
        GOAL_DETAIL.dispatched,
        "2026-10-01T12:04:00.000Z",
      ],
    );
    const check = await store.readGoalSourceCheck({
      goal_id: (
        await client.query(
          "SELECT id FROM assistant_runtime_goals WHERE source_turn_id = $1",
          [foreignTurn.id],
        )
      ).rows[0].id as string,
    });
    expect(check?.source_owned).toBe(false);

    const result = await activities.verifyGoal({
      goal_id: check?.goal.id ?? "",
    });
    expect(result.outcome).toBe("SOURCE_NOT_OWNED");
    const after = await client.query(
      "SELECT status, completed_at, verify_attempts FROM assistant_runtime_goals WHERE id = $1",
      [check?.goal.id],
    );
    expect(after.rows[0].status).toBe("PENDING");
    expect(after.rows[0].completed_at).toBeNull();
    expect(after.rows[0].verify_attempts).toBe(0);
  });

  it("refuses a source from another session or account at creation", async () => {
    const ownSession = await seedSession(userId, workspaceId);
    const otherOwnSession = await seedSession(userId, workspaceId);
    const ownTurn = await seedTurn(otherOwnSession);
    // Same account, a session that does not own the saved turn.
    await expect(
      store.createGoal({
        id: randomUUID(),
        session_id: ownSession,
        scope,
        kind: "BRIEFING",
        source_turn_id: ownTurn.id,
        action_id: null,
        created_at: "2026-10-01T12:20:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "forbidden" });

    // A different account owns the saved turn.
    const foreignSession = await seedSession(otherUserId, otherWorkspaceId);
    const foreignTurn = await seedTurn(foreignSession, {
      user_id: otherUserId,
      workspace_id: otherWorkspaceId,
    });
    await expect(
      store.createGoal({
        id: randomUUID(),
        session_id: ownSession,
        scope,
        kind: "BRIEFING",
        source_turn_id: foreignTurn.id,
        action_id: null,
        created_at: "2026-10-01T12:21:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "forbidden" });
    const rows = await client.query(
      "SELECT count(*)::int AS count FROM assistant_runtime_goals WHERE source_turn_id = ANY($1::uuid[])",
      [[ownTurn.id, foreignTurn.id]],
    );
    expect(rows.rows[0].count).toBe(0);
  });

  it("claims the dispatch budget atomically and stops at the cap", async () => {
    const sessionId = await seedSession(userId, workspaceId);
    const turn = await seedTurn(sessionId);
    const created = await store.createGoal({
      id: randomUUID(),
      session_id: sessionId,
      scope,
      kind: "BRIEFING",
      source_turn_id: turn.id,
      action_id: null,
      created_at: "2026-10-01T12:22:00.000Z",
    });
    const claim = () =>
      concurrentStore.claimGoalDispatchAttempt({
        goal_id: created.goal.id,
        scope,
        now: "2026-10-01T12:23:00.000Z",
        max_attempts: 3,
      });
    const claimed = await Promise.all(Array.from({ length: 8 }, claim));
    expect(claimed.filter((row) => row !== null)).toHaveLength(3);
    await expect(claim()).resolves.toBeNull();
    const stored = await store.readGoal({ goal_id: created.goal.id, scope });
    expect(stored?.attempts).toBe(3);
    // The budget is fenced by scope like every other goal write.
    await expect(
      concurrentStore.claimGoalDispatchAttempt({
        goal_id: created.goal.id,
        scope: { user_id: userId, workspace_id: randomUUID() },
        now: "2026-10-01T12:24:00.000Z",
        max_attempts: 8,
      }),
    ).resolves.toBeNull();
  });

  it("does not replay an existing goal into another session of the same account", async () => {
    const sourceSession = await seedSession(userId, workspaceId);
    const otherSession = await seedSession(userId, workspaceId);
    const turn = await seedTurn(sourceSession);
    const created = await store.createGoal({
      id: randomUUID(),
      session_id: sourceSession,
      scope,
      kind: "BRIEFING",
      source_turn_id: turn.id,
      action_id: null,
      created_at: "2026-10-01T12:24:00.000Z",
    });
    expect(created.created).toBe(true);
    await expect(
      store.createGoal({
        id: randomUUID(),
        session_id: otherSession,
        scope,
        kind: "BRIEFING",
        source_turn_id: turn.id,
        action_id: null,
        created_at: "2026-10-01T12:25:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "forbidden" });
  });

  it("verifies a grounded turn once and freezes the completed goal", async () => {
    const sessionId = await seedSession(userId, workspaceId);
    const turn = await seedTurn(sessionId);
    const created = await store.createGoal({
      id: randomUUID(),
      session_id: sessionId,
      scope,
      kind: "BRIEFING",
      source_turn_id: turn.id,
      action_id: null,
      created_at: "2026-10-01T12:05:00.000Z",
    });
    await store.markGoalDispatched({
      goal_id: created.goal.id,
      scope,
      now: "2026-10-01T12:05:30.000Z",
    });

    const verified = await activities.verifyGoal({ goal_id: created.goal.id });
    expect(verified.outcome).toBe("COMPLETED");
    const completed = await store.readGoal({ goal_id: created.goal.id, scope });
    expect(completed?.status).toBe("COMPLETED");
    expect(completed?.completed_at).not.toBeNull();

    // A replayed run can neither downgrade nor re-complete the frozen goal.
    await activities.markGoalDeadline({ goal_id: created.goal.id });
    const after = await store.readGoal({ goal_id: created.goal.id, scope });
    expect(after?.status).toBe("COMPLETED");
    expect(after?.completed_at).toBe(completed?.completed_at);
  });

  it("keeps an unverifiable goal out of completion within its bound", async () => {
    const sessionId = await seedSession(userId, workspaceId);
    const turn = await seedTurn(sessionId);
    const created = await store.createGoal({
      id: randomUUID(),
      session_id: sessionId,
      scope,
      kind: "BRIEFING",
      source_turn_id: turn.id,
      action_id: null,
      created_at: "2026-10-01T12:06:00.000Z",
    });
    await client.query(
      "UPDATE assistant_runtime_turns SET state = 'UNAVAILABLE' WHERE id = $1",
      [turn.id],
    );
    const result = await activities.verifyGoal({ goal_id: created.goal.id });
    expect(result.outcome).toBe("FAILED");
    const stored = await store.readGoal({ goal_id: created.goal.id, scope });
    expect(stored?.status).toBe("FAILED");
    expect(stored?.completed_at).toBeNull();
    expect(stored?.detail).toBe(GOAL_DETAIL.turnNotReady);
  });
});
