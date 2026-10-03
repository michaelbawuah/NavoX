-- SPEC-008 M1 NavoXbot runtime schema.
--
-- This migration is TypeScript-owned: it is deliberately outside the Python
-- Alembic migration head and does not modify any SPEC-005 model. Apply it with
--   npm run migrate --workspace=@navox/assistant-runtime
-- and verify the resulting schema with
--   npm run migration:check --workspace=@navox/assistant-runtime
--
-- The SPEC-005 `assistant_sessions` row stays the session identity. These
-- tables only add NavoXbot-owned rows that reference it, and they are the
-- reason the repository keeps a separate schema check for this boundary.

CREATE TABLE IF NOT EXISTS assistant_runtime_sessions (
    id uuid PRIMARY KEY,
    navox_session_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    next_sequence integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    CONSTRAINT assistant_runtime_sessions_navox_fk
        FOREIGN KEY (navox_session_id) REFERENCES assistant_sessions (id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_sessions_workspace_fk
        FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_sessions_user_fk
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_sessions_navox_unique UNIQUE (navox_session_id),
    CONSTRAINT assistant_runtime_sessions_scope_unique UNIQUE (id, workspace_id, user_id),
    CONSTRAINT assistant_runtime_sessions_sequence_positive CHECK (next_sequence >= 1),
    CONSTRAINT assistant_runtime_sessions_expiry_ordered CHECK (expires_at > created_at),
    CONSTRAINT assistant_runtime_sessions_retention_bound
        CHECK (expires_at <= created_at + interval '30 days')
);

CREATE TABLE IF NOT EXISTS assistant_runtime_turns (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    sequence integer NOT NULL,
    modality text NOT NULL,
    state text NOT NULL,
    request_id uuid NOT NULL,
    request_fingerprint text NOT NULL,
    question text NOT NULL,
    response_text text,
    plan jsonb NOT NULL,
    decision jsonb NOT NULL,
    presentation jsonb NOT NULL,
    action_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL,
    CONSTRAINT assistant_runtime_turns_session_fk
        FOREIGN KEY (session_id, workspace_id, user_id)
        REFERENCES assistant_runtime_sessions (id, workspace_id, user_id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_turns_sequence_unique UNIQUE (session_id, sequence),
    CONSTRAINT assistant_runtime_turns_request_unique UNIQUE (session_id, request_id),
    CONSTRAINT assistant_runtime_turns_sequence_bound CHECK (sequence BETWEEN 1 AND 2000),
    CONSTRAINT assistant_runtime_turns_modality_valid CHECK (modality IN ('TEXT', 'VOICE')),
    CONSTRAINT assistant_runtime_turns_state_valid
        CHECK (state IN ('READY', 'CLARIFY', 'UNAVAILABLE', 'WITHHELD')),
    CONSTRAINT assistant_runtime_turns_fingerprint_length
        CHECK (char_length(request_fingerprint) = 64),
    CONSTRAINT assistant_runtime_turns_question_bound
        CHECK (char_length(question) BETWEEN 1 AND 500),
    CONSTRAINT assistant_runtime_turns_answer_bound
        CHECK (response_text IS NULL OR char_length(response_text) <= 4000)
);

CREATE INDEX IF NOT EXISTS assistant_runtime_sessions_expiry_idx
    ON assistant_runtime_sessions (expires_at);

CREATE INDEX IF NOT EXISTS assistant_runtime_turns_scope_idx
    ON assistant_runtime_turns (session_id, workspace_id, user_id, sequence);

-- Durable per-request claim. The row is written before any upstream call, so a
-- concurrent duplicate replays one result and a changed payload behind the same
-- request ID is refused without spending an upstream call.
CREATE TABLE IF NOT EXISTS assistant_runtime_requests (
    session_id uuid NOT NULL,
    request_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    request_fingerprint text NOT NULL,
    status text NOT NULL,
    turn_id uuid,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (session_id, request_id),
    CONSTRAINT assistant_runtime_requests_session_fk
        FOREIGN KEY (session_id, workspace_id, user_id)
        REFERENCES assistant_runtime_sessions (id, workspace_id, user_id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_requests_turn_fk
        FOREIGN KEY (turn_id) REFERENCES assistant_runtime_turns (id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_requests_status_valid
        CHECK (status IN ('PENDING', 'RESOLVED')),
    CONSTRAINT assistant_runtime_requests_fingerprint_length
        CHECK (char_length(request_fingerprint) = 64),
    CONSTRAINT assistant_runtime_requests_resolution
        CHECK ((status = 'PENDING') = (turn_id IS NULL))
);

CREATE INDEX IF NOT EXISTS assistant_runtime_requests_stale_idx
    ON assistant_runtime_requests (updated_at);
