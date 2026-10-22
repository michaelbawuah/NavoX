-- Content-free goal provenance captured by the turn writer, not inferred from a
-- goal's kind/status. Existing plaintext turns and old workers remain untouched.
-- No legacy proof is backfilled or certified by this additive schema.
BEGIN;
-- Multiple isolated verification clients may apply this idempotent DDL at once.
-- Serialize this schema increment without changing any application authority.
SELECT pg_advisory_xact_lock(697228231, 3);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_runtime_turns_projection_scope_unique
    ON assistant_runtime_turns (id, session_id, workspace_id, user_id);

CREATE TABLE IF NOT EXISTS assistant_runtime_turn_goal_projections (
    turn_id uuid PRIMARY KEY,
    session_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    contract_version integer NOT NULL,
    classification text NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT assistant_runtime_turn_goal_projections_scope_fk
        FOREIGN KEY (turn_id, session_id, workspace_id, user_id)
        REFERENCES assistant_runtime_turns (id, session_id, workspace_id, user_id) ON DELETE CASCADE,
    CONSTRAINT assistant_runtime_turn_goal_projections_version
        CHECK (contract_version = 1),
    CONSTRAINT assistant_runtime_turn_goal_projections_classification
        CHECK (classification IN ('BRIEFING', 'MEETING_PREP', 'NOT_GROUNDED', 'UNREADABLE'))
);

-- A projection is a proof about exact source semantics and ownership. A legacy
-- semantic update invalidates it rather than silently retaining old authority.
-- Question/answer-only edits are not verification inputs. A future migration
-- that changes semantic storage still needs its own explicitly qualified cutover.
CREATE OR REPLACE FUNCTION assistant_runtime_invalidate_goal_projection()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.state IS DISTINCT FROM OLD.state
       OR NEW.plan IS DISTINCT FROM OLD.plan
       OR NEW.decision IS DISTINCT FROM OLD.decision
       OR NEW.presentation IS DISTINCT FROM OLD.presentation
    THEN
        DELETE FROM assistant_runtime_turn_goal_projections WHERE turn_id = OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assistant_runtime_turn_goal_projection_invalidate ON assistant_runtime_turns;
CREATE TRIGGER assistant_runtime_turn_goal_projection_invalidate
    BEFORE UPDATE OF id, session_id, workspace_id, user_id, state, plan, decision, presentation
    ON assistant_runtime_turns
    FOR EACH ROW EXECUTE FUNCTION assistant_runtime_invalidate_goal_projection();

COMMIT;
