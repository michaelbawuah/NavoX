"""Exercise the pre-push gate without installing packages or accessing providers."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check-api.sh"


@pytest.mark.parametrize(
    "failed_stage",
    [
        "",
        "sync",
        "lint",
        "format",
        "mypy",
        "revision",
        "pytest",
        "measurements",
        "subscription_measurements",
        "alembic",
        "evaluation",
        "schema",
        "watch_schema",
        "ai_schema",
        "draft_schema",
    ],
)
def test_api_preflight_blocks_failures_and_runs_remaining_gates(
    tmp_path: Path, failed_stage: str
) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    trace = tmp_path / "commands.txt"
    fake_uv = binary_dir / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$PREFLIGHT_TEST_TRACE"
stage=''
case "$*" in
  'sync '*) stage=sync ;;
  *'ruff check '*) stage=lint ;;
  *'ruff format '*) stage=format ;;
  *'mypy '*) stage=mypy ;;
  *'ScriptDirectory.from_config'*) stage=revision ;;
  *'pytest'*) stage=pytest ;;
  *'navox.evaluation.connector_metrics '*) stage=measurements ;;
  *'navox.evaluation.subscription_metrics '*) stage=subscription_measurements ;;
  *'alembic '*) stage=alembic ;;
  *'navox.evaluation '*) stage=evaluation ;;
esac
if [ -n "$PREFLIGHT_TEST_FAILURE" ] && [ "$stage" = "$PREFLIGHT_TEST_FAILURE" ]; then
  exit 7
fi
if [ "$stage" = alembic ]; then
  tables=(
    users workspaces workspace_memberships plans
    plan_steps plan_sources actions approvals workflow_refs
    audit_events proactive_preferences proactive_signals briefing_snapshots
    people person_identities person_identity_sources operational_observations observation_evidence
    intelligence_feedback intelligence_source_receipts gmail_sync_plans connector_definitions
    connector_connections connector_resources connector_subscriptions
    connector_event_receipts connector_sync_runs
    connector_sync_receipts connector_import_snapshots
    merchants merchant_aliases recurring_obligations recurring_obligation_evidence
    obligation_price_history cancellation_attempts cancellation_evidence subscription_events
    ai_registry_revisions ai_registry_state ai_providers ai_models ai_model_capabilities
    ai_profiles ai_profile_assignments ai_prompts ai_schemas ai_routing_policies
    ai_provider_health ai_evaluation_runs ai_task_runs communication_drafts
    communication_draft_versions assistant_sessions assistant_turns
  )
  for table in "${tables[@]}"; do
    if [ "$PREFLIGHT_TEST_FAILURE" = schema ]; then
      [ "$table" = connector_import_snapshots ] && continue
    fi
    if [ "$PREFLIGHT_TEST_FAILURE" = ai_schema ]; then
      [ "$table" = ai_task_runs ] && continue
    fi
    if [ "$PREFLIGHT_TEST_FAILURE" = draft_schema ]; then
      [ "$table" = communication_draft_versions ] && continue
    fi
    printf 'CREATE TABLE %s (\\n' "$table"
  done
  printf '%s\\n' client_type
  if [ "$PREFLIGHT_TEST_FAILURE" != watch_schema ]; then
    printf '%s\\n' 'ADD COLUMN expiration_confirmed_at TIMESTAMP WITH TIME ZONE'
  fi
  printf '%s\\n' uq_person_identities_workspace_type_value
  printf '%s\\n' uq_observation_evidence_observation_source_hash
fi
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    fake_git = binary_dir / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_git.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{binary_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "PREFLIGHT_TEST_TRACE": str(trace),
        "PREFLIGHT_TEST_FAILURE": failed_stage,
    }
    completed = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == (1 if failed_stage else 0), completed.stderr
    commands = trace.read_text(encoding="utf-8")
    if failed_stage == "sync":
        assert commands.splitlines() == ["sync --locked --all-groups"]
    else:
        assert len(commands.splitlines()) == 10
        assert "ScriptDirectory.from_config" in commands
        assert "python -m pytest" in commands
        assert "python -m navox.evaluation.connector_metrics" in commands
        assert "python -m navox.evaluation.subscription_metrics" in commands
        assert "python -m navox.evaluation --fail-on-gate" in commands
        assert "python -m mypy navox" in commands
    assert ("API preflight PASSED" in completed.stdout) is (not failed_stage)
