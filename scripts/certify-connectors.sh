#!/usr/bin/env bash
# Reproducible, fixture-backed SPEC-003 contract/security/portability gate.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
report="${1:-/tmp/navox-connector-certification.xml}"
measurements="${2:-${report%.xml}-measurements.json}"
cd "$repo_dir/services/api"

test_status=0
uv run --no-sync pytest -q --junitxml="$report" \
  tests/test_connector_measurements.py \
  tests/test_connector_certification.py \
  tests/test_connector_contracts.py \
  tests/test_connector_capabilities.py \
  tests/test_connector_runtime.py \
  tests/test_connector_sync_recovery.py \
  tests/test_connector_recovery_workflows.py \
  tests/test_connector_secret_broker.py \
  tests/test_connector_secret_lifecycle.py \
  tests/test_connector_canvas.py \
  tests/test_canvas_managed.py \
  tests/test_connector_imports.py \
  tests/test_managed_imports.py \
  tests/test_connector_generic_api.py \
  tests/test_connector_mcp.py \
  tests/test_connector_subscriptions.py \
  tests/test_connector_events.py \
  tests/test_connector_portability.py \
  tests/test_connector_google_bridge.py \
  tests/test_google_calendar_connector.py \
  tests/test_google_gmail_connector.py \
  tests/test_events.py \
  tests/test_mcp_management.py \
  tests/test_connection_management.py \
  tests/test_intelligence_*.py || test_status=$?

measurement_status=0
uv run --no-sync python -m navox.evaluation.connector_metrics \
  --junit "$report" --output "$measurements" || measurement_status=$?

printf 'Fixture-backed connector evidence: %s\n' "$report"
printf 'Metric-specific measurements: %s\n' "$measurements"
if [ "$test_status" -ne 0 ] || [ "$measurement_status" -ne 0 ]; then
  exit 1
fi
