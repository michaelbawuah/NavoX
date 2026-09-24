#!/usr/bin/env bash
# Reproducible, fixture-backed SPEC-003 contract/security/portability gate.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
report="${1:-/tmp/navox-connector-certification.xml}"
cd "$repo_dir/services/api"

uv run --no-sync pytest -q --junitxml="$report" \
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
  tests/test_connector_portability.py \
  tests/test_connector_google_bridge.py \
  tests/test_google_calendar_connector.py \
  tests/test_google_gmail_connector.py \
  tests/test_events.py \
  tests/test_mcp_management.py \
  tests/test_connection_management.py

printf 'Fixture-backed connector evidence: %s\n' "$report"
