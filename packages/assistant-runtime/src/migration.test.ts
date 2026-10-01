import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const sql = readFileSync(
  new URL("../migrations/0001_assistant_runtime.sql", import.meta.url),
  "utf8",
);
const normalized = sql.replace(/\s+/g, " ");
const goalsSql = readFileSync(
  new URL("../migrations/0002_assistant_goals.sql", import.meta.url),
  "utf8",
);
const goalsNormalized = goalsSql.replace(/\s+/g, " ");

describe("TypeScript-owned NavoXbot migration", () => {
  it("adds only NavoXbot tables and leaves the Alembic head alone", () => {
    expect(sql).not.toMatch(/ALTER TABLE assistant_sessions/i);
    expect(sql).not.toMatch(/\bDROP\s+(TABLE|COLUMN)\b/i);
    expect(sql).not.toMatch(/alembic_version/i);
    expect(sql).not.toMatch(/assistant_turns\b/i);
  });

  it("references the SPEC-005 session row and the account scope", () => {
    expect(normalized).toMatch(
      /FOREIGN KEY \(navox_session_id\) REFERENCES assistant_sessions \(id\) ON DELETE CASCADE/,
    );
    expect(normalized).toMatch(
      /FOREIGN KEY \(workspace_id\) REFERENCES workspaces \(id\)/,
    );
    expect(normalized).toMatch(
      /FOREIGN KEY \(user_id\) REFERENCES users \(id\)/,
    );
  });

  it("fences turns by session, workspace and user", () => {
    expect(normalized).toMatch(
      /FOREIGN KEY \(session_id, workspace_id, user_id\) REFERENCES assistant_runtime_sessions \(id, workspace_id, user_id\)/,
    );
    expect(normalized).toMatch(
      /ON assistant_runtime_turns \(session_id, workspace_id, user_id, sequence\)/,
    );
  });

  it("enforces idempotency and ordering in the database", () => {
    expect(normalized).toMatch(/UNIQUE \(session_id, request_id\)/);
    expect(normalized).toMatch(/UNIQUE \(session_id, sequence\)/);
    expect(normalized).toMatch(/sequence BETWEEN 1 AND 2000/);
  });

  it("adds the durable per-request claim table", () => {
    expect(normalized).toMatch(
      /CREATE TABLE IF NOT EXISTS assistant_runtime_requests/,
    );
    expect(normalized).toMatch(/PRIMARY KEY \(session_id, request_id\)/);
    expect(normalized).toMatch(/status IN \('PENDING', 'RESOLVED'\)/);
    expect(normalized).toMatch(/\(status = 'PENDING'\) = \(turn_id IS NULL\)/);
    expect(normalized).toMatch(
      /FOREIGN KEY \(turn_id\) REFERENCES assistant_runtime_turns \(id\)/,
    );
    expect(normalized).toMatch(/ON assistant_runtime_requests \(updated_at\)/);
  });

  it("bounds retention and retained text", () => {
    expect(normalized).toMatch(
      /expires_at <= created_at \+ interval '30 days'/,
    );
    expect(normalized).toMatch(/char_length\(question\) BETWEEN 1 AND 500/);
    expect(normalized).toMatch(/char_length\(response_text\) <= 4000/);
    expect(normalized).toMatch(/char_length\(request_fingerprint\) = 64/);
  });

  it("keeps the vocabulary in step with the shared contracts", () => {
    expect(normalized).toMatch(/modality IN \('TEXT', 'VOICE'\)/);
    expect(normalized).toMatch(
      /state IN \('READY', 'CLARIFY', 'UNAVAILABLE', 'WITHHELD'\)/,
    );
  });
});

describe("TypeScript-owned M14B goal migration", () => {
  it("adds only its own table and leaves the Alembic head alone", () => {
    expect(goalsSql).toMatch(
      /CREATE TABLE IF NOT EXISTS assistant_runtime_goals/,
    );
    expect(goalsSql).not.toMatch(/ALTER TABLE assistant_sessions/i);
    expect(goalsSql).not.toMatch(/\bDROP\s+(TABLE|COLUMN)\b/i);
    expect(goalsSql).not.toMatch(/alembic_version/i);
  });

  it("fences a goal by its owning session, workspace and user", () => {
    expect(goalsNormalized).toMatch(
      /FOREIGN KEY \(session_id, workspace_id, user_id\) REFERENCES assistant_runtime_sessions \(id, workspace_id, user_id\)/,
    );
    expect(goalsNormalized).toMatch(
      /FOREIGN KEY \(source_turn_id\) REFERENCES assistant_runtime_turns \(id\)/,
    );
    expect(goalsNormalized).toMatch(
      /FOREIGN KEY \(action_id\) REFERENCES actions \(id\)/,
    );
  });

  it("names exactly one bounded source per goal", () => {
    expect(goalsNormalized).toMatch(
      /\(source_turn_id IS NULL\) <> \(action_id IS NULL\)/,
    );
    expect(goalsNormalized).toMatch(
      /\(kind = 'COMMUNICATION_ACTION'\) = \(action_id IS NOT NULL\)/,
    );
  });

  it("keeps the status vocabulary in step with the shared contracts", () => {
    expect(goalsNormalized).toMatch(
      /kind IN \('BRIEFING', 'MEETING_PREP', 'COMMUNICATION_ACTION'\)/,
    );
    expect(goalsNormalized).toMatch(
      /status IN \( 'PENDING', 'RUNNING', 'WAITING_FOR_USER', 'WAITING_FOR_EXTERNAL', 'COMPLETED', 'FAILED' \)/,
    );
    expect(goalsNormalized).toMatch(
      /dispatch_state IN \('NOT_DISPATCHED', 'DISPATCHED', 'DISPATCH_FAILED'\)/,
    );
  });

  it("bounds dispatch, verification and completion", () => {
    expect(goalsNormalized).toMatch(/attempts BETWEEN 0 AND 8/);
    expect(goalsNormalized).toMatch(/verify_attempts BETWEEN 0 AND 64/);
    expect(goalsNormalized).toMatch(
      /\(status = 'COMPLETED'\) = \(completed_at IS NOT NULL\)/,
    );
    expect(goalsNormalized).toMatch(
      /detail IS NULL OR char_length\(detail\) <= 500/,
    );
  });

  it("makes a replayed turn or action exactly one goal", () => {
    expect(goalsSql).toMatch(
      /CREATE UNIQUE INDEX IF NOT EXISTS assistant_runtime_goals_turn_unique/,
    );
    expect(goalsSql).toMatch(
      /CREATE UNIQUE INDEX IF NOT EXISTS assistant_runtime_goals_action_unique/,
    );
    expect(goalsNormalized).toMatch(
      /ON assistant_runtime_goals \(source_turn_id, kind\) WHERE source_turn_id IS NOT NULL/,
    );
    expect(goalsNormalized).toMatch(
      /ON assistant_runtime_goals \(action_id\) WHERE action_id IS NOT NULL/,
    );
  });
});
