#!/usr/bin/env bash
# Run the API quality gates before pushing. Collect failures; never hide them.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/services/api" || exit 1

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'ERROR: uv is required for the locked API preflight.' >&2
  exit 1
fi
if ! uv sync --locked --all-groups; then
  printf '%s\n' 'FAIL: locked dependency installation; no quality result is available.' >&2
  exit 1
fi

reports="$(mktemp -d "${TMPDIR:-/tmp}/navox-api-preflight.XXXXXX")" || exit 1
trap 'rm -rf "$reports"' EXIT
failures=0
check() {
  local label="$1"
  shift
  printf '\n=== %s ===\n' "$label"
  if "$@"; then
    printf 'PASS: %s\n' "$label"
  else
    local result=$?
    printf 'FAIL: %s (exit %s)\n' "$label" "$result" >&2
    failures=$((failures + 1))
  fi
}

check 'Ruff lint' uv run --no-sync python -m ruff check .
check 'Ruff formatting' uv run --no-sync python -m ruff format --check .
check 'Strict mypy' uv run --no-sync python -m mypy navox
check 'PostgreSQL Alembic revision width' uv run --no-sync python -c 'from alembic.config import Config; from alembic.script import ScriptDirectory; revisions = ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions(); assert all(len(item.revision) <= 32 for item in revisions), "Alembic revision exceeds PostgreSQL version_num width"'
check 'Full API test suite' uv run --no-sync python -m pytest

render_schema() {
  uv run --no-sync python -m alembic upgrade head --sql > "$reports/schema.sql" || return $?
  # Keep this list aligned with the required-schema check in .github/workflows/ci.yml.
  local table
  tables=(
    users workspaces workspace_memberships plans
    plan_steps actions approvals workflow_refs
    audit_events proactive_preferences proactive_signals briefing_snapshots
    people person_identities operational_observations observation_evidence
    intelligence_feedback intelligence_source_receipts gmail_sync_plans connector_definitions
    connector_connections connector_resources connector_subscriptions connector_sync_runs connector_sync_receipts connector_import_snapshots
  )
  for table in "${tables[@]}"; do
    grep -F "CREATE TABLE $table (" "$reports/schema.sql" >/dev/null || {
      printf 'Missing migration table: %s\n' "$table" >&2
      return 1
    }
  done
  grep -F 'client_type' "$reports/schema.sql" >/dev/null || return 1
  grep -F 'uq_person_identities_workspace_type_value' "$reports/schema.sql" >/dev/null || return 1
  grep -F 'uq_observation_evidence_observation_source_hash' "$reports/schema.sql" >/dev/null || return 1
}
check 'Alembic SQL and required schema' render_schema
check 'Deterministic release evaluation' uv run --no-sync python -m navox.evaluation \
  --fail-on-gate --output "$reports/evaluation.json" --markdown "$reports/evaluation.md"
check 'Patch whitespace' git -C "$root" diff --check HEAD

if [ "$failures" -ne 0 ]; then
  printf '\nAPI preflight FAILED: %s gate(s). Do not push this change.\n' "$failures" >&2
  exit 1
fi
printf '\nAPI preflight PASSED. Hosted CI and Compose still require separate verification.\n'
