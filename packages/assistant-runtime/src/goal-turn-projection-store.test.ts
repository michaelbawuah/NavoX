import { describe, expect, it } from "vitest";
import { verifyProjectedTurnGoal } from "./goal-turn-projection";
import { GOAL_DETAIL } from "./goals";
import { createPostgresStore, type SqlExecutor } from "./store";

const scope = {
  user_id: "11111111-1111-4111-8111-111111111111",
  workspace_id: "22222222-2222-4222-8222-222222222222",
};
const sessionId = "66666666-6666-4666-8666-666666666666";
const turnId = "88888888-8888-4888-8888-888888888888";
const goalId = "99999999-9999-4999-8999-999999999999";
const instant = "2026-10-22T12:00:00.000Z";
const privateText = "PRIVATE-SOURCE-CONTENT-NEVER-PROJECTED";

function savedTurn() {
  return {
    id: turnId,
    session_id: sessionId,
    sequence: 1,
    modality: "TEXT" as const,
    state: "READY" as const,
    request_id: "77777777-7777-4777-8777-777777777777",
    request_fingerprint: "a".repeat(64),
    question: privateText,
    response_text: privateText,
    plan: {
      version: 1 as const,
      intents: [
        {
          kind: "today.read" as const,
          capability_id: "today.read" as const,
          question: privateText,
          confidence: 1,
          entity: { kind: "NONE" as const, value: null, confidence: 1 },
          time: { kind: "NONE" as const, expression: null, confidence: 1 },
          reference: { kind: "NONE" as const, ordinal: null, turn_id: null },
          requires_clarification: false,
          clarification: null,
        },
      ],
    },
    decision: {
      kind: "DELEGATE" as const,
      capability_id: "today.read" as const,
      target: "today.query" as const,
      reason: "today.today",
      requires_approval: false,
      action_state: "NONE" as const,
      action_id: null,
      response_state: "READY" as const,
    },
    presentation: {
      presentation: "TEXT" as const,
      speak: false,
      speech_text: null,
      delivery: "AUTOMATIC" as const,
      blocks: [{ kind: "ANSWER" as const, text: privateText }],
    },
    action_refs: [],
    created_at: instant,
  };
}

function goalRow(extra: Record<string, unknown> = {}) {
  return {
    id: goalId,
    session_id: sessionId,
    ...scope,
    kind: "BRIEFING",
    status: "PENDING",
    dispatch_state: "NOT_DISPATCHED",
    source_turn_id: turnId,
    action_id: null,
    detail: GOAL_DETAIL.pendingDispatch,
    attempts: 0,
    verify_attempts: 0,
    created_at: instant,
    updated_at: instant,
    completed_at: null,
    turn_state: "READY",
    projection_version: 1,
    projection_classification: "BRIEFING",
    action_status: null,
    action_executed_at: null,
    action_verified_at: null,
    source_owned: true,
    ...extra,
  };
}

function executor(rows: unknown[]) {
  const calls: { sql: string; values: readonly unknown[] }[] = [];
  const sql: SqlExecutor = {
    async query<R>(statement: string, values: readonly unknown[] = []) {
      calls.push({ sql: statement, values });
      return { rows: rows as R[] };
    },
  };
  return { sql, calls };
}

describe("additive goal projection store", () => {
  it("captures the strict projection with its turn in one atomic statement", async () => {
    const input = savedTurn();
    const { sql, calls } = executor([input]);
    const store = createPostgresStore(sql);
    await store.insertTurn({ ...input, scope });
    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call.sql).toContain("WITH saved_turn AS");
    expect(call.sql).toContain(
      "INSERT INTO assistant_runtime_turn_goal_projections",
    );
    expect(call.sql).toContain("RETURNING id, session_id, sequence");
    expect(call.sql).toContain("created_at, workspace_id, user_id");
    expect(call.sql).toContain(
      "JOIN saved_projection ON saved_projection.turn_id = saved_turn.id",
    );
    expect(call.values.slice(-2)).toEqual([1, "BRIEFING"]);
    expect(call.values).toHaveLength(18);
    expect(JSON.stringify(call.values.slice(-2))).not.toContain(privateText);
  });

  it("reads only bound IDs/status/projection metadata without a plaintext fallback", async () => {
    const row = goalRow();
    for (const field of [
      "question",
      "response_text",
      "turn_plan",
      "turn_decision",
      "turn_presentation",
    ]) {
      Object.defineProperty(row, field, {
        get() {
          throw new Error("Private legacy source was read");
        },
      });
    }
    const { sql, calls } = executor([row]);
    const check = await createPostgresStore(sql).readGoalVerificationProjection(
      { goal_id: goalId },
    );
    expect(check?.source_owned).toBe(true);
    expect(check?.turn?.projection).toEqual({
      contract_version: 1,
      classification: "BRIEFING",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].values).toEqual([goalId]);
    expect(calls[0].sql).not.toMatch(
      /\bt\.(question|response_text|plan|decision|presentation)\b/,
    );
    for (const field of ["session_id", "workspace_id", "user_id"]) {
      expect(calls[0].sql).toContain(`t.${field} = g.${field}`);
      expect(calls[0].sql).toContain(`p.${field} = t.${field}`);
    }
    expect(JSON.stringify(check)).not.toContain(privateText);
  });

  it.each([
    { projection_version: null, projection_classification: null },
    { projection_version: 2 },
    { projection_classification: privateText },
    { turn_state: "UNAVAILABLE" },
  ])(
    "never certifies missing/invalid evidence from a completed goal: %j",
    async (invalid) => {
      const { sql, calls } = executor([
        goalRow({ ...invalid, status: "COMPLETED", completed_at: instant }),
      ]);
      const check = await createPostgresStore(
        sql,
      ).readGoalVerificationProjection({ goal_id: goalId });
      expect(check?.source_owned).toBe(true);
      expect(check?.turn?.projection).toBeNull();
      expect(
        verifyProjectedTurnGoal("BRIEFING", check?.turn?.projection),
      ).toEqual({ available: false, outcome: null });
      expect(calls).toHaveLength(1);
      expect(calls[0].sql).not.toMatch(/\bt\.(plan|decision|presentation)\b/);
    },
  );

  it("surfaces foreign source as unowned and never synthesizes a turn proof", async () => {
    const { sql } = executor([
      goalRow({
        turn_state: null,
        projection_version: null,
        projection_classification: null,
        source_owned: false,
      }),
    ]);
    const check = await createPostgresStore(sql).readGoalVerificationProjection(
      { goal_id: goalId },
    );
    expect(check?.source_owned).toBe(false);
    expect(check?.turn).toBeNull();
  });

  it("preserves the live consequential-action ledger branch without reading its payload", async () => {
    const { sql, calls } = executor([
      goalRow({
        kind: "COMMUNICATION_ACTION",
        source_turn_id: null,
        action_id: turnId,
        turn_state: null,
        projection_version: null,
        projection_classification: null,
        action_status: "uncertain",
        action_executed_at: instant,
        action_verified_at: null,
      }),
    ]);
    const check = await createPostgresStore(sql).readGoalVerificationProjection(
      { goal_id: goalId },
    );
    expect(check?.action).toEqual({
      status: "uncertain",
      executed_at: instant,
      verified_at: null,
    });
    expect(check?.turn).toBeNull();
    expect(calls[0].sql).not.toMatch(/\ba\.(payload|result)\b/);
  });

  it("reports a missing goal as missing and performs no mutation", async () => {
    const { sql, calls } = executor([]);
    expect(
      await createPostgresStore(sql).readGoalVerificationProjection({
        goal_id: goalId,
      }),
    ).toBeNull();
    expect(calls).toHaveLength(1);
    expect(calls[0].sql.trim()).toMatch(/^SELECT /);
  });
});
