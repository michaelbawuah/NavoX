# SPEC-002 regression evidence

The acceptance suite runs the actual extraction validator, evidence persistence,
entity and temporal resolution, commitment resolution, lifecycle inference,
attention calculation, Today projection, and bounded feedback adjustment. SQLite
foreign-key enforcement is enabled in the end-to-end demonstrations.

The three required demonstrations are executable tests in
`services/api/tests/test_intelligence_demos.py`:

1. Calendar meeting plus a budget request resolves one budget commitment, appears
   in Today with evidence and ranking factors, accepts idempotent feedback, and
   completes only after sent evidence.
2. An approval request enters waiting after it is sent. An unrelated sender's
   response cannot finish it; the expected counterparty's approval can.
3. Promotional content, replayed source versions, injected instructions, and
   forged permission fields produce no extra commitment or external action.

A fourth demonstration tests cross-workspace source, Today, and feedback
isolation. All model responses and input documents are synthetic test fixtures;
these are application regression results, **not measured live model accuracy**.
The SPEC-002 precision/recall targets remain unmeasured until a separately labeled
live-provider evaluation has been run. No production credentials are required or
used by this regression suite.

From `services/api`:

```bash
uv run pytest tests/test_intelligence_*.py --junitxml=/tmp/navox-spec002-tests.xml
uv run python -m navox.evaluation.intelligence_report \
  --junit /tmp/navox-spec002-tests.xml \
  --output /tmp/navox-spec002-regression.json
```

`evals/intelligence/gates.json` requires every demonstration, zero failures and
zero skips. The report records executed counts from JUnit instead of hardcoding
success counts. CI retains the test evidence and report as artifacts. Mocked
connector protocol tests also cover read-scope checks, delta handling, replay and
error boundaries; successful synthetic tests do not prove live watch delivery,
OAuth configuration, or real model quality.

## Separate model smoke evidence

`python -m navox.evaluation.intelligence_smoke` is a request-free dry run.
`--offline` validates authored responses through the actual gateway/extractor;
`--live` sends nine fixed synthetic cases to the configured provider. The optional
`--suite email-triage` instead evaluates 24 synthetic Gmail cases through the v2
schema, owner-context extraction and relevance filter (ten positive, fourteen
negative). Offline fixtures cover the wiring. The owner supplied a complete live
email-triage report on 2026-09-24: 24 executed, 24 passed, zero failed or skipped.
It is retained in
[`evals/intelligence/reports/2026-09-24-email-triage-live-smoke.json`](../../evals/intelligence/reports/2026-09-24-email-triage-live-smoke.json).
This verifies those synthetic examples through the configured live model; it
does not establish classification accuracy on a representative mailbox or clean
up existing saved cards. The earlier nine-case live report predates this policy.
The command does not accept mailbox input. Reports distinguish `offline_fixture` from
`live_model_smoke`, redact exception details, and retain only case IDs, outcome
codes, durations, and counts. Invalid proposals on the adversarial case can count
as safely blocked; transport/runtime errors always fail, and empty extractions do
not pass positive obligation cases.

This deliberately small smoke suite checks integration behavior. Its pass count
must not be reported as precision, recall, or general model accuracy. It cannot
prove source synchronization or durable workflow execution. The owner-facing
commands and remaining live acceptance checklist are in
`spec-002-operations.md`. The regression report and smoke report remain separate
so one cannot silently substitute for the other.

## Older Gmail cleanup regressions

`test_intelligence_email_focus.py` covers read-time exclusion of legacy and old
undated Gmail cards, preservation of dated obligations/manual work/recorded user
choices, recoverable set-aside records, owner-scoped idempotent keep, and stale
proactive/meeting-prep paths. The relevance regressions include deliberately wrong
high-confidence labels for non-task shapes plus real assigned return/application
work. Subject-only Gmail no longer creates review cards; bounded evidence reading
and citation deduplication are still exercised on set-aside suggestions.

Web regressions cover six-row pagination and nonduplicated filter membership.
The production build, types and static rendering tests are not a visual browser
acceptance test. Local Chromium preview was unavailable because its download
returned an unavailable page; desktop/mobile visual acceptance remains pending.

`test_intelligence_gmail_recheck.py` verifies that listing/previewing does not
change cards or ingestion state, ignored mail yields only a selectable suggestion,
only a high-confidence matching action is reported as supported, and unrelated
work, paraphrases, uncertain proposals and completion/waiting updates require
review. Multiple-source tests verify that a later state update or provider failure
is not hidden by an earlier matching request. Malformed/changed/missing evidence
and previews from an older policy cannot authorize removal. It exercises the
three-message limit, deduplicated reads, paused/revoked/narrowed access, cache and timeout recovery,
Google cooldowns, expired claims, obsolete preview IDs, state changes during model
I/O, ownership, batch bounds, keep decisions, and idempotent selected removal.
The frontend tests cover explicit selection, sequential ten-item batches, stopping
on failure or pause, escaped titles, and no automatic source reads/removal.

The Compose gate also runs `integration/gmail_recheck.py` against PostgreSQL.
It checks competing preview claims, four simultaneous applications of the same
selection with one dismissal audit, and an owner state change during model I/O.
Its `gmail_recheck_concurrency_verified` flag uses synthetic provider responses;
the real owner cleanup flow remains a separate acceptance check.

## Deployed workflow integration gate

The Compose CI job mounts `services/api/integration` read-only into a temporary
API container and runs `python -m integration.intelligence_temporal_smoke` after
the existing lifecycle checks. It registers real workflows and activities on a
unique Temporal queue and seeds an isolated PostgreSQL workspace. Synthetic
Google HTTP responses pass through the real adapter and extraction pipeline.
The harness verifies source-event completion, receipts, replay deduplication,
Today provenance, idempotent feedback and reranking, pause and revocation, and
absence of external actions. No fixture mode is added to production settings.
It allows no real HTTP request; Temporal/PostgreSQL remain real services.

Status: implemented; successful execution must be established by the GitHub
Compose integration result for the delivered revision. A local unit/static check
does not substitute for this gate or for the separate live owner demonstrations.
