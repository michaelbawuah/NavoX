import type {
  AssistantActionRef,
  AssistantGoalDispatchState,
  AssistantGoalKind,
  AssistantGoalStatus,
  AssistantModality,
  AssistantPresentationPlan,
  AssistantResponseState,
  AssistantTurnView,
  CapabilityDecision,
  IntentPlan,
} from "@navox/contracts";
import { AssistantError, isAssistantError } from "./errors";
import {
  GOAL_DETAIL,
  GOAL_LIMITS,
  parseGoalDetail,
  parseGoalDispatchState,
  parseGoalKind,
  parseGoalStatus,
} from "./goals";
import { LIMITS } from "./limits";
import {
  clampText,
  parseCapabilityDecision,
  parseIntentPlan,
  parsePresentationPlan,
} from "./validate";

export interface SqlResult<R> {
  rows: R[];
}

/** Minimal executor seam so persistence is testable without a live database. */
export interface SqlExecutor {
  query<R = Record<string, unknown>>(
    text: string,
    values?: readonly unknown[],
  ): Promise<SqlResult<R>>;
}

export interface AssistantScope {
  user_id: string;
  workspace_id: string;
}

export interface AssistantSessionRecord {
  id: string;
  navox_session_id: string;
  workspace_id: string;
  user_id: string;
  next_sequence: number;
  created_at: string;
  updated_at: string;
  expires_at: string;
}

export interface AssistantTurnRecord {
  id: string;
  session_id: string;
  sequence: number;
  modality: AssistantModality;
  state: AssistantResponseState;
  request_id: string;
  request_fingerprint: string;
  question: string;
  response_text: string | null;
  plan: IntentPlan;
  decision: CapabilityDecision;
  presentation: AssistantPresentationPlan;
  action_refs: AssistantActionRef[];
  created_at: string;
}

/**
 * Durable per-request claim. It is written before any upstream call so that a
 * concurrent duplicate replays one result, and a changed payload behind the same
 * request ID is refused without spending an upstream call.
 */
export interface AssistantRequestClaim {
  request_id: string;
  request_fingerprint: string;
  status: "PENDING" | "RESOLVED";
  turn_id: string | null;
  updated_at: string;
}

/** One durable, opaque verification goal. Identifiers and a status only. */
export interface AssistantGoalRecord {
  id: string;
  session_id: string;
  workspace_id: string;
  user_id: string;
  kind: AssistantGoalKind;
  status: AssistantGoalStatus;
  dispatch_state: AssistantGoalDispatchState;
  source_turn_id: string | null;
  action_id: string | null;
  detail: string | null;
  attempts: number;
  verify_attempts: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

/**
 * The facts one worker run may read for a goal, plus its own ownership
 * recheck. `source_owned` is true only when the referenced turn or action is
 * owned by the goal's stored user and workspace; a mismatch must leave the goal
 * untouched rather than mutate a row it cannot justify.
 */
export interface GoalSourceCheck {
  goal: AssistantGoalRecord;
  source_owned: boolean;
  turn: {
    state: string;
    plan: unknown;
    decision: unknown;
    presentation: unknown;
  } | null;
  action: {
    status: string;
    executed_at: string | null;
    verified_at: string | null;
  } | null;
}

export interface AssistantStore {
  createSession(input: {
    id: string;
    navox_session_id: string;
    scope: AssistantScope;
    created_at: string;
    expires_at: string;
  }): Promise<AssistantSessionRecord>;
  readSession(input: {
    session_id: string;
    scope: AssistantScope;
  }): Promise<AssistantSessionRecord | null>;
  listTurns(input: {
    session_id: string;
    scope: AssistantScope;
  }): Promise<AssistantTurnRecord[]>;
  findTurnByRequest(input: {
    session_id: string;
    scope: AssistantScope;
    request_id: string;
  }): Promise<AssistantTurnRecord | null>;
  claimRequest(input: {
    session_id: string;
    scope: AssistantScope;
    request_id: string;
    request_fingerprint: string;
    now: string;
    stale_before: string;
  }): Promise<{ created: boolean; claim: AssistantRequestClaim | null }>;
  readClaimRequest(input: {
    session_id: string;
    scope: AssistantScope;
    request_id: string;
  }): Promise<AssistantRequestClaim | null>;
  resolveClaimRequest(input: {
    session_id: string;
    scope: AssistantScope;
    request_id: string;
    turn_id: string;
    now: string;
  }): Promise<void>;
  releaseClaimRequest(input: {
    session_id: string;
    scope: AssistantScope;
    request_id: string;
  }): Promise<void>;
  reserveSequence(input: {
    session_id: string;
    scope: AssistantScope;
    now: string;
  }): Promise<number>;
  insertTurn(input: {
    id: string;
    session_id: string;
    scope: AssistantScope;
    sequence: number;
    modality: AssistantModality;
    state: AssistantResponseState;
    request_id: string;
    request_fingerprint: string;
    question: string;
    response_text: string | null;
    plan: IntentPlan;
    decision: CapabilityDecision;
    presentation: AssistantPresentationPlan;
    action_refs: AssistantActionRef[];
    created_at: string;
  }): Promise<AssistantTurnRecord>;
  deleteSession(input: {
    session_id: string;
    scope: AssistantScope;
  }): Promise<boolean>;
  /** Deletes at most `limit` expired sessions so a purge pass stays bounded. */
  purgeExpired(now: string, limit: number): Promise<number>;
  /**
   * Creates one bounded goal for a saved turn or action. A replay returns the
   * existing row instead of creating a duplicate.
   */
  createGoal(input: {
    id: string;
    session_id: string;
    scope: AssistantScope;
    kind: AssistantGoalKind;
    source_turn_id: string | null;
    action_id: string | null;
    created_at: string;
  }): Promise<{ created: boolean; goal: AssistantGoalRecord }>;
  /** Reads one goal, fenced by the owning session, workspace and user. */
  readGoal(input: {
    goal_id: string;
    scope: AssistantScope;
  }): Promise<AssistantGoalRecord | null>;
  /** Lists a bounded number of goals for one owned session. */
  listGoals(input: {
    session_id: string;
    scope: AssistantScope;
  }): Promise<AssistantGoalRecord[]>;
  /**
   * Worker-facing read by opaque ID only. The caller must check `source_owned`
   * before it changes any status.
   */
  readGoalSourceCheck(input: {
    goal_id: string;
  }): Promise<GoalSourceCheck | null>;
  /** Records that the durable workflow was accepted for this goal. */
  markGoalDispatched(input: {
    goal_id: string;
    scope: AssistantScope;
    now: string;
  }): Promise<AssistantGoalRecord | null>;
  /**
   * Atomically claims one attempt from the goal's bounded total dispatch
   * budget. It returns null when the budget is exhausted, the goal is already
   * completed, or the goal is outside this scope. The condition and the
   * increment are one statement, so concurrent callers can never exceed it.
   */
  claimGoalDispatchAttempt(input: {
    goal_id: string;
    scope: AssistantScope;
    now: string;
    max_attempts: number;
  }): Promise<AssistantGoalRecord | null>;
  /**
   * Records one dispatch failure; the goal stays truthfully pending. The
   * attempt was claimed separately, so this never changes the budget.
   */
  markGoalDispatchFailed(input: {
    goal_id: string;
    scope: AssistantScope;
    now: string;
    detail: string;
  }): Promise<AssistantGoalRecord | null>;
  /**
   * Writes one workflow's verification outcome. A completed goal is frozen, so
   * a late or replayed run can never downgrade or re-complete it.
   */
  applyGoalVerification(input: {
    goal_id: string;
    status: AssistantGoalStatus;
    detail: string;
    completed_at: string | null;
    now: string;
  }): Promise<AssistantGoalRecord | null>;
}

const SESSION_COLUMNS =
  "id, navox_session_id, workspace_id, user_id, next_sequence, created_at, updated_at, expires_at";
const TURN_COLUMNS =
  "id, session_id, sequence, modality, state, request_id, request_fingerprint, question, response_text, plan, decision, presentation, action_refs, created_at";

interface SessionRow {
  id: string;
  navox_session_id: string;
  workspace_id: string;
  user_id: string;
  next_sequence: number | string;
  created_at: Date | string;
  updated_at: Date | string;
  expires_at: Date | string;
}

interface TurnRow {
  id: string;
  session_id: string;
  sequence: number | string;
  modality: string;
  state: string;
  request_id: string;
  request_fingerprint: string;
  question: string;
  response_text: string | null;
  plan: unknown;
  decision: unknown;
  presentation: unknown;
  action_refs: unknown;
  created_at: Date | string;
}

interface ClaimRow {
  request_id: string;
  request_fingerprint: string;
  status: string;
  turn_id: string | null;
  updated_at: Date | string;
}

const CLAIM_COLUMNS =
  "request_id, request_fingerprint, status, turn_id, updated_at";

const GOAL_COLUMNS =
  "id, session_id, workspace_id, user_id, kind, status, dispatch_state, source_turn_id, action_id, detail, attempts, verify_attempts, created_at, updated_at, completed_at";
/** The same columns, qualified for the ownership join against turns/actions. */
const GOAL_COLUMNS_QUALIFIED = GOAL_COLUMNS.split(", ")
  .map((column) => `g.${column}`)
  .join(", ");

interface GoalRow {
  id: string;
  session_id: string;
  workspace_id: string;
  user_id: string;
  kind: string;
  status: string;
  dispatch_state: string;
  source_turn_id: string | null;
  action_id: string | null;
  detail: string | null;
  attempts: number | string;
  verify_attempts: number | string;
  created_at: Date | string;
  updated_at: Date | string;
  completed_at: Date | string | null;
}

function iso(value: Date | string): string {
  return value instanceof Date
    ? value.toISOString()
    : new Date(value).toISOString();
}

function isoOrNull(value: Date | string | null): string | null {
  return value === null ? null : iso(value);
}

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "A saved assistant turn could not be verified.",
  );
}

function sessionFromRow(row: SessionRow): AssistantSessionRecord {
  return {
    id: row.id,
    navox_session_id: row.navox_session_id,
    workspace_id: row.workspace_id,
    user_id: row.user_id,
    next_sequence: Number(row.next_sequence),
    created_at: iso(row.created_at),
    updated_at: iso(row.updated_at),
    expires_at: iso(row.expires_at),
  };
}

function actionRefsFrom(value: unknown): AssistantActionRef[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (typeof entry !== "object" || entry === null) return [];
    const ref = entry as Record<string, unknown>;
    if (typeof ref.action_id !== "string" || typeof ref.state !== "string")
      return [];
    return [
      {
        action_id: ref.action_id,
        state: ref.state as AssistantActionRef["state"],
      },
    ];
  });
}

/** Stored rows are re-validated on read; corruption is a failure, not an answer. */
function turnFromRow(row: TurnRow): AssistantTurnRecord {
  try {
    return {
      id: row.id,
      session_id: row.session_id,
      sequence: Number(row.sequence),
      modality: row.modality as AssistantModality,
      state: row.state as AssistantResponseState,
      request_id: row.request_id,
      request_fingerprint: row.request_fingerprint,
      question: row.question,
      response_text: row.response_text,
      plan: parseIntentPlan(row.plan, { allowBoundTurn: true }),
      decision: parseCapabilityDecision(row.decision),
      presentation: parsePresentationPlan(row.presentation),
      action_refs: actionRefsFrom(row.action_refs),
      created_at: iso(row.created_at),
    };
  } catch (error) {
    if (isAssistantError(error) && error.code === "unavailable") throw error;
    unreadable();
  }
}

function claimFromRow(row: ClaimRow): AssistantRequestClaim {
  if (row.status !== "PENDING" && row.status !== "RESOLVED") unreadable();
  return {
    request_id: row.request_id,
    request_fingerprint: row.request_fingerprint,
    status: row.status,
    turn_id: row.turn_id,
    updated_at: iso(row.updated_at),
  };
}

/** Stored goal rows are re-validated on read; corruption is a failure. */
function goalFromRow(row: GoalRow): AssistantGoalRecord {
  try {
    return {
      id: row.id,
      session_id: row.session_id,
      workspace_id: row.workspace_id,
      user_id: row.user_id,
      kind: parseGoalKind(row.kind),
      status: parseGoalStatus(row.status),
      dispatch_state: parseGoalDispatchState(row.dispatch_state),
      source_turn_id: row.source_turn_id,
      action_id: row.action_id,
      detail: parseGoalDetail(row.detail),
      attempts: Number(row.attempts),
      verify_attempts: Number(row.verify_attempts),
      created_at: iso(row.created_at),
      updated_at: iso(row.updated_at),
      completed_at: isoOrNull(row.completed_at),
    };
  } catch (error) {
    if (isAssistantError(error) && error.code === "unavailable") throw error;
    unreadable();
  }
}

export function toTurnView(record: AssistantTurnRecord): AssistantTurnView {
  return {
    id: record.id,
    sequence: record.sequence,
    modality: record.modality,
    state: record.state,
    question: record.question,
    plan: record.plan,
    decision: record.decision,
    presentation: record.presentation,
    action_refs: record.action_refs,
    created_at: record.created_at,
  };
}

export function createPostgresStore(executor: SqlExecutor): AssistantStore {
  return {
    async createSession(input) {
      const { rows } = await executor.query<SessionRow>(
        `INSERT INTO assistant_runtime_sessions
           (id, navox_session_id, workspace_id, user_id, created_at, updated_at, expires_at)
         VALUES ($1, $2, $3, $4, $5, $5, $6)
         RETURNING ${SESSION_COLUMNS}`,
        [
          input.id,
          input.navox_session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.created_at,
          input.expires_at,
        ],
      );
      const row = rows[0];
      if (!row)
        throw new AssistantError(
          "unavailable",
          "The assistant session was not saved.",
        );
      return sessionFromRow(row);
    },

    async readSession(input) {
      const { rows } = await executor.query<SessionRow>(
        `SELECT ${SESSION_COLUMNS} FROM assistant_runtime_sessions
          WHERE id = $1 AND workspace_id = $2 AND user_id = $3`,
        [input.session_id, input.scope.workspace_id, input.scope.user_id],
      );
      const row = rows[0];
      return row ? sessionFromRow(row) : null;
    },

    async listTurns(input) {
      const { rows } = await executor.query<TurnRow>(
        `SELECT ${TURN_COLUMNS} FROM assistant_runtime_turns
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3
          ORDER BY sequence ASC
          LIMIT $4`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          LIMITS.maxSequence,
        ],
      );
      return rows.map(turnFromRow);
    },

    async findTurnByRequest(input) {
      const { rows } = await executor.query<TurnRow>(
        `SELECT ${TURN_COLUMNS} FROM assistant_runtime_turns
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3 AND request_id = $4`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.request_id,
        ],
      );
      const row = rows[0];
      return row ? turnFromRow(row) : null;
    },

    async claimRequest(input) {
      // A claim older than the stale window belongs to a crashed worker and may
      // be taken over; the window is longer than the upstream timeout.
      await executor.query(
        `DELETE FROM assistant_runtime_requests
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3 AND request_id = $4
            AND status = 'PENDING' AND updated_at <= $5`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.request_id,
          input.stale_before,
        ],
      );
      const inserted = await executor.query<ClaimRow>(
        `INSERT INTO assistant_runtime_requests
           (session_id, request_id, workspace_id, user_id, request_fingerprint,
            status, turn_id, created_at, updated_at)
         VALUES ($1, $2, $3, $4, $5, 'PENDING', NULL, $6, $6)
         ON CONFLICT (session_id, request_id) DO NOTHING
         RETURNING ${CLAIM_COLUMNS}`,
        [
          input.session_id,
          input.request_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.request_fingerprint,
          input.now,
        ],
      );
      const created = inserted.rows[0];
      if (created) return { created: true, claim: claimFromRow(created) };
      const { rows } = await executor.query<ClaimRow>(
        `SELECT ${CLAIM_COLUMNS} FROM assistant_runtime_requests
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3 AND request_id = $4`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.request_id,
        ],
      );
      const row = rows[0];
      return { created: false, claim: row ? claimFromRow(row) : null };
    },

    async readClaimRequest(input) {
      const { rows } = await executor.query<ClaimRow>(
        `SELECT ${CLAIM_COLUMNS} FROM assistant_runtime_requests
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3 AND request_id = $4`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.request_id,
        ],
      );
      const row = rows[0];
      return row ? claimFromRow(row) : null;
    },

    async resolveClaimRequest(input) {
      await executor.query(
        `UPDATE assistant_runtime_requests
            SET status = 'RESOLVED', turn_id = $5, updated_at = $6
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3 AND request_id = $4
            AND status = 'PENDING'`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.request_id,
          input.turn_id,
          input.now,
        ],
      );
    },

    async releaseClaimRequest(input) {
      await executor.query(
        `DELETE FROM assistant_runtime_requests
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3 AND request_id = $4
            AND status = 'PENDING'`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.request_id,
        ],
      );
    },

    async reserveSequence(input) {
      const { rows } = await executor.query<{ sequence: number | string }>(
        `UPDATE assistant_runtime_sessions
            SET next_sequence = next_sequence + 1, updated_at = $4
          WHERE id = $1 AND workspace_id = $2 AND user_id = $3 AND expires_at > $4
            AND next_sequence <= $5
          RETURNING next_sequence - 1 AS sequence`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.now,
          LIMITS.maxTurnsPerSession,
        ],
      );
      const row = rows[0];
      if (!row) {
        const current = await executor.query<{
          next_sequence: number | string;
        }>(
          `SELECT next_sequence FROM assistant_runtime_sessions
            WHERE id = $1 AND workspace_id = $2 AND user_id = $3 AND expires_at > $4`,
          [
            input.session_id,
            input.scope.workspace_id,
            input.scope.user_id,
            input.now,
          ],
        );
        if (
          Number(current.rows[0]?.next_sequence) > LIMITS.maxTurnsPerSession
        ) {
          throw new AssistantError(
            "unsupported",
            "This conversation reached its turn limit. Start a new one.",
          );
        }
        throw new AssistantError(
          "not_found",
          "That assistant session is no longer available.",
        );
      }
      return Number(row.sequence);
    },

    async insertTurn(input) {
      let rows: TurnRow[];
      try {
        const result = await executor.query<TurnRow>(
          `INSERT INTO assistant_runtime_turns
             (id, session_id, workspace_id, user_id, sequence, modality, state, request_id,
              request_fingerprint, question, response_text, plan, decision, presentation,
              action_refs, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
           RETURNING ${TURN_COLUMNS}`,
          [
            input.id,
            input.session_id,
            input.scope.workspace_id,
            input.scope.user_id,
            input.sequence,
            input.modality,
            input.state,
            input.request_id,
            input.request_fingerprint,
            clampText(input.question, LIMITS.maxQuestionLength),
            input.response_text === null
              ? null
              : clampText(input.response_text, LIMITS.maxAnswerLength),
            JSON.stringify(input.plan),
            JSON.stringify(input.decision),
            JSON.stringify(input.presentation),
            JSON.stringify(input.action_refs),
            input.created_at,
          ],
        );
        rows = result.rows;
      } catch (error) {
        if (isUniqueViolation(error)) {
          throw new AssistantError(
            "conflict",
            "That request ID is already saved for this conversation.",
          );
        }
        throw error;
      }
      const row = rows[0];
      if (!row)
        throw new AssistantError(
          "unavailable",
          "The assistant turn was not saved.",
        );
      return turnFromRow(row);
    },

    async deleteSession(input) {
      const { rows } = await executor.query<{ id: string }>(
        `DELETE FROM assistant_runtime_sessions
          WHERE id = $1 AND workspace_id = $2 AND user_id = $3
          RETURNING id`,
        [input.session_id, input.scope.workspace_id, input.scope.user_id],
      );
      return rows.length > 0;
    },

    async purgeExpired(now, limit) {
      const { rows } = await executor.query<{ id: string }>(
        `DELETE FROM assistant_runtime_sessions
          WHERE id IN (
            SELECT id FROM assistant_runtime_sessions
             WHERE expires_at <= $1
             ORDER BY expires_at
             LIMIT $2
          )
          RETURNING id`,
        [now, limit],
      );
      return rows.length;
    },

    async createGoal(input) {
      // The source scope is enforced in the same statement as the insert, so a
      // turn from another session, user or workspace - or an action from
      // another account - can never become this goal's source, even for a
      // moment. The single-column foreign keys alone cannot express that.
      const inserted = await executor.query<GoalRow>(
        `INSERT INTO assistant_runtime_goals
           (id, session_id, workspace_id, user_id, kind, status, dispatch_state,
            source_turn_id, action_id, detail, attempts, verify_attempts,
            created_at, updated_at, completed_at)
         SELECT $1, $2, $3, $4, $5, 'PENDING', 'NOT_DISPATCHED', $6, $7, $8, 0, 0, $9, $9, NULL
          WHERE (
                  $6::uuid IS NOT NULL AND EXISTS (
                    SELECT 1 FROM assistant_runtime_turns t
                     WHERE t.id = $6::uuid
                       AND t.session_id = $2
                       AND t.user_id = $4
                       AND t.workspace_id = $3
                  )
                )
             OR (
                  $7::uuid IS NOT NULL AND EXISTS (
                    SELECT 1 FROM actions a
                     WHERE a.id = $7::uuid
                       AND a.user_id = $4
                       AND a.workspace_id = $3
                  )
                )
         ON CONFLICT DO NOTHING
         RETURNING ${GOAL_COLUMNS}`,
        [
          input.id,
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.kind,
          input.source_turn_id,
          input.action_id,
          GOAL_DETAIL.pendingDispatch,
          input.created_at,
        ],
      );
      const created = inserted.rows[0];
      if (created) return { created: true, goal: goalFromRow(created) };
      // A replay of the same saved turn or action returns the existing goal.
      const existing = await executor.query<GoalRow>(
        input.source_turn_id !== null
          ? `SELECT ${GOAL_COLUMNS} FROM assistant_runtime_goals
              WHERE source_turn_id = $1 AND kind = $2`
          : `SELECT ${GOAL_COLUMNS} FROM assistant_runtime_goals
              WHERE action_id = $1`,
        input.source_turn_id !== null
          ? [input.source_turn_id, input.kind]
          : [input.action_id],
      );
      const found = existing.rows[0];
      // No row inserted and no existing row means the source is not owned by
      // this session, user and workspace.
      if (!found)
        throw new AssistantError(
          "forbidden",
          "That goal source is not available for this account.",
        );
      const goal = goalFromRow(found);
      if (
        goal.session_id !== input.session_id ||
        goal.workspace_id !== input.scope.workspace_id ||
        goal.user_id !== input.scope.user_id
      )
        throw new AssistantError(
          "forbidden",
          "This account cannot use that goal.",
        );
      return { created: false, goal };
    },

    async readGoal(input) {
      const { rows } = await executor.query<GoalRow>(
        `SELECT ${GOAL_COLUMNS} FROM assistant_runtime_goals
          WHERE id = $1 AND workspace_id = $2 AND user_id = $3`,
        [input.goal_id, input.scope.workspace_id, input.scope.user_id],
      );
      const row = rows[0];
      return row ? goalFromRow(row) : null;
    },

    async listGoals(input) {
      const { rows } = await executor.query<GoalRow>(
        `SELECT ${GOAL_COLUMNS} FROM assistant_runtime_goals
          WHERE session_id = $1 AND workspace_id = $2 AND user_id = $3
          ORDER BY created_at DESC, id DESC
          LIMIT $4`,
        [
          input.session_id,
          input.scope.workspace_id,
          input.scope.user_id,
          GOAL_LIMITS.maxGoalsPerSession,
        ],
      );
      return rows.map(goalFromRow);
    },

    async readGoalSourceCheck(input) {
      const { rows } = await executor.query<
        GoalRow & {
          turn_state: string | null;
          turn_plan: unknown;
          turn_decision: unknown;
          turn_presentation: unknown;
          action_status: string | null;
          action_executed_at: Date | string | null;
          action_verified_at: Date | string | null;
          source_owned: boolean;
        }
      >(
        `SELECT ${GOAL_COLUMNS_QUALIFIED},
                t.state AS turn_state,
                t.plan AS turn_plan,
                t.decision AS turn_decision,
                t.presentation AS turn_presentation,
                a.status AS action_status,
                a.executed_at AS action_executed_at,
                a.verified_at AS action_verified_at,
                ((g.source_turn_id IS NOT NULL AND t.id IS NOT NULL)
                  OR (g.action_id IS NOT NULL AND a.id IS NOT NULL)) AS source_owned
           FROM assistant_runtime_goals g
           LEFT JOIN assistant_runtime_turns t
             ON t.id = g.source_turn_id
            AND t.session_id = g.session_id
            AND t.user_id = g.user_id
            AND t.workspace_id = g.workspace_id
           LEFT JOIN actions a
             ON a.id = g.action_id
            AND a.user_id = g.user_id
            AND a.workspace_id = g.workspace_id
          WHERE g.id = $1`,
        [input.goal_id],
      );
      const row = rows[0];
      if (!row) return null;
      return {
        goal: goalFromRow(row),
        source_owned: row.source_owned === true,
        turn:
          row.turn_state === null
            ? null
            : {
                state: row.turn_state,
                plan: row.turn_plan,
                decision: row.turn_decision,
                presentation: row.turn_presentation,
              },
        action:
          row.action_status === null
            ? null
            : {
                status: row.action_status,
                executed_at: isoOrNull(row.action_executed_at),
                verified_at: isoOrNull(row.action_verified_at),
              },
      };
    },

    async markGoalDispatched(input) {
      const { rows } = await executor.query<GoalRow>(
        `UPDATE assistant_runtime_goals
            SET dispatch_state = 'DISPATCHED',
                detail = $4,
                updated_at = $5
          WHERE id = $1 AND workspace_id = $2 AND user_id = $3
            AND status <> 'COMPLETED'
          RETURNING ${GOAL_COLUMNS}`,
        [
          input.goal_id,
          input.scope.workspace_id,
          input.scope.user_id,
          GOAL_DETAIL.dispatched,
          input.now,
        ],
      );
      const row = rows[0];
      return row ? goalFromRow(row) : null;
    },

    async markGoalDispatchFailed(input) {
      const { rows } = await executor.query<GoalRow>(
        `UPDATE assistant_runtime_goals
            SET dispatch_state = 'DISPATCH_FAILED',
                detail = $4,
                updated_at = $5
          WHERE id = $1 AND workspace_id = $2 AND user_id = $3
            AND status <> 'COMPLETED'
          RETURNING ${GOAL_COLUMNS}`,
        [
          input.goal_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.detail,
          input.now,
        ],
      );
      const row = rows[0];
      return row ? goalFromRow(row) : null;
    },

    async claimGoalDispatchAttempt(input) {
      const { rows } = await executor.query<GoalRow>(
        `UPDATE assistant_runtime_goals
            SET attempts = attempts + 1,
                updated_at = $4
          WHERE id = $1 AND workspace_id = $2 AND user_id = $3
            AND status <> 'COMPLETED'
            AND attempts < $5
          RETURNING ${GOAL_COLUMNS}`,
        [
          input.goal_id,
          input.scope.workspace_id,
          input.scope.user_id,
          input.now,
          input.max_attempts,
        ],
      );
      const row = rows[0];
      return row ? goalFromRow(row) : null;
    },

    async applyGoalVerification(input) {
      const { rows } = await executor.query<GoalRow>(
        `UPDATE assistant_runtime_goals
            SET status = $2,
                detail = $3,
                completed_at = $4,
                updated_at = $5,
                verify_attempts = LEAST(verify_attempts + 1, 64)
          WHERE id = $1 AND status <> 'COMPLETED'
          RETURNING ${GOAL_COLUMNS}`,
        [
          input.goal_id,
          input.status,
          input.detail,
          input.completed_at,
          input.now,
        ],
      );
      const row = rows[0];
      return row ? goalFromRow(row) : null;
    },
  };
}

export function isUniqueViolation(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { code?: unknown }).code === "23505"
  );
}
