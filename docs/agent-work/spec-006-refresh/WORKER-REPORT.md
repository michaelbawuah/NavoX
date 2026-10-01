# SPEC-006 S006-REFRESH-1 worker report

STATUS: ready_for_review

TASK: S006-REFRESH-1 - register the stale-source refresh orchestration for news
conversations so a conversation answer is not produced from stale evidence.
WORKSPACE: `/Users/mba_steins/NavoX`, branch `spec-006-news-intelligence`, HEAD
`5573ba4`. Nothing was committed, staged, pushed, merged or deployed.

## Changed paths

| path | kind |
| --- | --- |
| `services/api/navox/workflows/news.py` | modified |
| `services/api/tests/test_news_conversation_refresh_workflow.py` | new |
| `docs/agent-work/spec-006-refresh/WORKER-REPORT.md` | new (this report) |
| `docs/agent-work/spec-006-refresh/validation/` | new (raw logs) |

`services/api/navox/news/activities.py` was **not** modified.
`prepare_news_conversation_activity` was already callable exactly as written, so
the "only if it genuinely needs a correction" escape hatch in the brief was not
used. No other path was touched, including the accepted S006-SURF-1, S006-EVAL-1
and S006-ADV-1 work, `CODEX-HANDOFF-20260929-1304.md` and `docs/agent-work/`
directories owned by other bundles.

## What changed

`NewsConversationRefreshWorkflow.run` now runs, behind the patch marker:

1. `workflow.execute_activity(prepare_news_conversation_activity, payload)` with
   `start_to_close_timeout=30s` and `RetryPolicy(maximum_attempts=3)`. The
   activity returns identifier-only `NewsSourceWork` items (capped at four by
   `retrieval.py`: `refresh_source_ids` has `max_length=4`), so no question,
   headline or evidence text reaches workflow history.
2. `refresh_stale_sources(...)`, which hands those items to the existing
   `reconcile_page(...)` helper. That is the same child-workflow entry point used
   by source reconciliation: child workflow `NewsSourceIngestionWorkflow.run`,
   id `news-source:{source_id}:{request_id}`, and `asyncio.Semaphore(4)` bounded
   concurrency. Childs are awaited by `asyncio.gather` before the answer runs;
   there is no sleeping, polling or unbounded fan-out.
3. The unchanged answer step: `execute_activity(news_conversation_activity,
   payload, start_to_close_timeout=2min, RetryPolicy(maximum_attempts=1))`.

Failure semantics mirror `NewsSourceIngestionWorkflow`'s tolerance of an
unavailable intelligence step. `prepare_news_conversation_activity` failing with
`ActivityError` is logged as a warning and skipped; each child workflow failure
is already logged per source inside `reconcile_page`; cancellation
(`is_cancelled_exception`) is re-raised in both places and stops the run before
the answer. The only signature change elsewhere is a keyword-only, defaulted
`deferred: str` log message on `reconcile_page`, so reconciliation keeps its
original message and behaviour while the refresh path logs its own.

## Patch marker

`news-conversation-refresh-v1`, used as
`workflow.patched("news-conversation-refresh-v1")` at the top of
`NewsConversationRefreshWorkflow.run`. It follows the existing
`news-intelligence-pipeline-v1` convention in the same module. When it returns
`False` (previously started histories) the workflow skips prepare and refresh
entirely and executes only the answer activity, preserving old replay paths. No
`deprecate_patch`/`patched_before` change was made, so both paths stay
addressable.

## Verification

All commands ran from the repository root unless stated; logs are in
`docs/agent-work/spec-006-refresh/validation/`.

| # | command | exit | salient result |
| --- | --- | --- | --- |
| 1 | `cd services/api && ./.venv/bin/python -m pytest tests/test_news_conversation_refresh_workflow.py -q` (pre-change, red-first) | 1 | `7 failed in 0.21s` - ordering assertion saw only `['activity']` (the answer) so refresh-before-answer was provably absent. Log `01-red-first-pytest.txt`. |
| 2 | same file, post-change | 0 | `7 passed in 0.12s`. Log `02-refresh-green-pytest.txt`. |
| 3 | `./.venv/bin/python -m pytest tests/test_news_conversation_refresh_workflow.py tests/test_news_jobs.py tests/test_news_intelligence.py tests/test_news_conversation_api.py tests/test_news_conversations.py tests/test_news_retrieval.py tests/test_news_adversarial.py tests/test_intelligence_workflow_hardening.py tests/test_connector_recovery_workflows.py -q` | 0 | `125 passed in 15.26s` - focused news workflow/activity set plus the two named harness-style modules. Log `03-focused-news-pytest.txt`. |
| 4 | `UV_CACHE_DIR=$(mktemp -d /tmp/navox-uv-cache.XXXXXX) bash scripts/check-api.sh` | 0 | `API preflight PASSED`: Ruff lint, `ruff format --check .`, strict mypy, Alembic revision width, `2074 passed, 3 warnings in 182.21s` (2067 prior + 7 new), SPEC-003/SPEC-004 thresholds, Alembic SQL and required schema, deterministic release evaluation and patch whitespace all PASS. Log `04-check-api.txt`. |
| 5 | `cd services/api && ./.venv/bin/python -m ruff check navox/workflows/news.py tests/test_news_conversation_refresh_workflow.py` + `ruff format --check` + `mypy navox/workflows/news.py` | 0 / 0 / 0 | Targeted lint/format/type confirmation of the two touched source paths. Log `05-targeted-lint.txt`. |

`UV_CACHE_DIR` was pointed at a fresh temp directory for the full gate so the
locked sync and gates did not depend on a pre-existing cache; no dependency or
lockfile change was made.

## Coverage in the new test module

`services/api/tests/test_news_conversation_refresh_workflow.py` uses the existing
harness style (monkeypatching `news.workflow.execute_activity`,
`execute_child_workflow`, `patched`, `logger`), no new harness and no new
dependency:

- two stale sources refresh before the answer, with the
  `news-source:{source_id}:{request_id}` child ids, the payload passed through
  unchanged, answer timeout `2min` / `maximum_attempts=1`, and field sets
  asserted to be identifier-only;
- empty source list runs prepare then the answer and schedules no children;
- a failing ingestion child still produces the answer and logs a warning;
- an unavailable prepare (`ActivityError`) still produces the answer;
- a pre-patch history (`patched` returns `False`) runs only the answer and reads
  exactly the marker string `news-conversation-refresh-v1`;
- child cancellation propagates and the answer never runs;
- six sources peak at four concurrent children and drop to zero in flight.

## Retained failures and warnings

- The red-first run is a retained, intentional failure (log `01-red-first-pytest.txt`);
  it is the evidence that the ordering test was meaningful before the fix.
- The full gate reports three pre-existing warnings unrelated to this change: a
  FastAPI/Starlette `httpx` deprecation, an `anyio` `BlockingPortal` deprecation,
  and `navox/api/today.py`'s `HTTP_422_UNPROCESSABLE_ENTITY` Starlette
  deprecation. No new warnings were introduced.

## Limits and notes

- The harness expresses replayed histories by forcing `workflow.patched` to
  `False`/`True`; this repository has no Temporal test-server replay fixture, so
  a genuine pre-patch event history is not exercised here. The existing
  `tests/test_news_conversation_api.py::test_news_workflows_load_in_temporal_sandbox`
  still proves the workflow definitions load in the Temporal sandbox and passed
  in the focused and full runs.
- Hosted CI, Compose and a real Temporal server are not available in this
  sandbox, so the gate result is local only, as `scripts/check-api.sh` itself
  states.
- An unrelated, concurrent edit to `docs/evidence/spec-006/README.md` (outside
  this bundle's allowed paths) appeared in the shared working tree during this
  run. It was left untouched; see `validation/07-git-status.txt`.
- No commit, stage, push, merge, deploy, dependency, lockfile, feature-flag,
  schema, migration or CI change was performed.
