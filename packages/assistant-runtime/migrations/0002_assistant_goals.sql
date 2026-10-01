-- SPEC-008 M14B bounded personal goals.
--
-- This migration is TypeScript-owned and stays outside the Python Alembic
-- head. It adds only NavoXbot-owned goal rows that reference an existing
-- SPEC-005 assistant session and, at most, one of:
--   * a saved assistant turn (BRIEFING, MEETING_PREP), or
--   * a SPEC-001/003 action (COMMUNICATION_ACTION).
--
-- A goal row carries identifiers, a bounded status vocabulary and a bounded
-- operator-facing detail string only. No cookie, source content, provider
-- token, approval secret or action payload is stored here or sent to Temporal.

CREATE TABLE IF NOT EXISTS assistant_runtime_goals (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    dispatch_state text NOT NULL,
    source_turn_id uuid,
    action_id uuid,
    detail text,
    attempts integer NOT NULL DEFAULT 0,
    verify_attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz,
    CONSTRAINT assistant_runtime_goals_session_fk
        FOREIGN KEY (session_id, workspace_id, user_id)
        REFERENCES assistant_runtime_sessions (id, workspace_id, user_id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_goals_turn_fk
        FOREIGN KEY (source_turn_id) REFERENCES assistant_runtime_turns (id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_goals_action_fk
        FOREIGN KEY (action_id) REFERENCES actions (id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_goals_kind_valid
        CHECK (kind IN ('BRIEFING', 'MEETING_PREP', 'COMMUNICATION_ACTION')),
    CONSTRAINT assistant_runtime_goals_status_valid
        CHECK (
            status IN (
                'PENDING',
                'RUNNING',
                'WAITING_FOR_USER',
                'WAITING_FOR_EXTERNAL',
                'COMPLETED',
                'FAILED'
            )
        ),
    CONSTRAINT assistant_runtime_goals_dispatch_valid
        CHECK (dispatch_state IN ('NOT_DISPATCHED', 'DISPATCHED', 'DISPATCH_FAILED')),
    -- Exactly one source: a saved turn, or one SPEC-001/003 action.
    CONSTRAINT assistant_runtime_goals_exactly_one_source
        CHECK ((source_turn_id IS NULL) <> (action_id IS NULL)),
    -- Only a consequential-action goal may name an action.
    CONSTRAINT assistant_runtime_goals_source_matches_kind
        CHECK ((kind = 'COMMUNICATION_ACTION') = (action_id IS NOT NULL)),
    CONSTRAINT assistant_runtime_goals_detail_bound
        CHECK (detail IS NULL OR char_length(detail) <= 500),
    -- Bounded dispatch and verification budgets; the worker stops at the cap.
    CONSTRAINT assistant_runtime_goals_attempts_bounded
        CHECK (attempts BETWEEN 0 AND 8),
    CONSTRAINT assistant_runtime_goals_verify_attempts_bounded
        CHECK (verify_attempts BETWEEN 0 AND 64),
    -- A goal is completed exactly when it records the instant it verified.
    CONSTRAINT assistant_runtime_goals_completion
        CHECK ((status = 'COMPLETED') = (completed_at IS NOT NULL)),
    CONSTRAINT assistant_runtime_goals_time_ordered
        CHECK (updated_at >= created_at)
);

-- Replaying a request must not create a second goal for the same saved turn.
CREATE UNIQUE INDEX IF NOT EXISTS assistant_runtime_goals_turn_unique
    ON assistant_runtime_goals (source_turn_id, kind)
    WHERE source_turn_id IS NOT NULL;

-- One goal per SPEC-001/003 action, so a redispatch never duplicates a goal.
CREATE UNIQUE INDEX IF NOT EXISTS assistant_runtime_goals_action_unique
    ON assistant_runtime_goals (action_id)
    WHERE action_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS assistant_runtime_goals_scope_idx
    ON assistant_runtime_goals (session_id, workspace_id, user_id, created_at);

CREATE INDEX IF NOT EXISTS assistant_runtime_goals_dispatch_idx
    ON assistant_runtime_goals (dispatch_state, status, updated_at);
