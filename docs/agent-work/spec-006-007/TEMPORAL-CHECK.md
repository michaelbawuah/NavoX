# Real Knowledge workflow execution, 30 September 2026 02:24UTC

`tests/test_knowledge_temporal.py`: **1 passed in1.95s** against real local Temporal
(auto-setup1.28.0) and isolated schemas in PostgreSQL17. The single integrated scenario
executes all eight registered Knowledge workflow types, checks indexed content,
stale-event refusal, disabled embedding availability, disabled-feature deletion and
no-resurrection, completes one reconciliation page, cancels its schedule, and replays
all captured histories through Temporal's Replayer. Histories exclude fixture bodies.

Activities use real database services with authored connected resources; the session
factory is scoped to an isolated fixture schema. No source/provider network calls or
production configuration. This is separate from the original-spec query/quality,
live source and human usability gates; hosted Compose CI on a new head remains open.

Command: NAVOX_TEMPORAL_TEST_TARGET=127.0.0.1:54723 and
NAVOX_CONNECTOR_TEST_DSN=postgresql+asyncpg://navox_test:navox_test_only@127.0.0.1:54705/navox_spec_test
then `uv run --no-sync pytest tests/test_knowledge_temporal.py -q` fromservices/api.
Log /tmp/navox-knowledge-temporal-final.log.

Disposable stack file /tmp/navox-spec006007-compose-20260929.yml, project
navox-spec006007-check-20260929, unique check-data volume, localhost ports54706/54723.
No existing NavoX containers/volumes altered. Remove this exact project with --volumes
after final integration; retain no running test services at task end.
