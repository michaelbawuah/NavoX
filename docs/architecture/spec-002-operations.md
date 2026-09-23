# SPEC-002 operation and verification

## Enable locally

1. Pull the delivery branch, preserve your existing `.env`, then rebuild with
   `docker compose up --build -d`. Migrations run before API/worker startup,
   including 0012 for bounded source-processing receipts.
2. Configure `AI_PROVIDER=openai`, `OPENAI_API_KEY`, and `OPENAI_MODEL` in the
   private environment. Use a model your account supports for strict Responses
   JSON schema. Restart/recreate both API and worker after changes. Keys are never
   entered in the web app or committed to Git.
3. Sign in and use **Enable Gmail & Calendar understanding** on the existing Google
   connection. Consent grants only Gmail readonly and Calendar events readonly.
   Enable both APIs and add the read scopes to the Google OAuth consent screen.
   Identity-only connections do not read messages or events.
4. Use **Sync Gmail** and **Sync Calendar**. Initial Gmail processing covers the
   last 30 days; Calendar covers primary-calendar events from 30 days ago through
   90 days ahead. Source changes subsequently use provider history/sync cursors.
5. Today shows evidence references, reasons, uncertainty and feedback controls.
   A sent request can become waiting; only evidence of the intended outcome
   completes it. An expired deadline alone never completes a task.

## Provider delivery and recovery

Gmail push requires `GOOGLE_GMAIL_WATCH_TOPIC` plus existing Pub/Sub subscription,
OIDC audience/service-account and verification-token settings. Calendar push uses
`GOOGLE_CALENDAR_PUSH_URL` ending in `/api/v1/events/calendar` on public HTTPS.
The worker creates/renews provider watches and stores lease expiry. Notification
handlers authenticate before creating a durable inbox row. A failed immediate
Temporal dispatch leaves that inbox row available to reconciliation.

Without public push delivery, the worker performs delta reconciliation after
30 minutes of cursor inactivity. It never performs repeated full-mailbox LLM
rescans. Expired provider cursors trigger a bounded initial scan plus revalidation
of previously referenced resources. A batch limit or transient model/provider
failure leaves the previous cursor unchanged. Each accepted source revision commits
its minimal receipt together with its resolved facts; retries skip unchanged
revisions that already completed. An invalid model proposal is quarantined with a
receipt and an `invalid_model_proposal` audit reason, so a poison document cannot
permanently prevent later valid changes from processing. Receipts store bounded
source identifiers and hashes, never full source content. Check audit events for
failure class and latency; raw source text and provider error payloads are excluded.

The intelligence workflow contains identifiers only. PostgreSQL holds current
facts; Temporal retries work and refreshes time-dependent state and attention.
Pause and revoked scopes are checked again inside processing activities.

## Display context

Workspace settings persist IANA timezone, 12/24-hour clock, temperature unit and
optional weather city. Weather is opt-in, uses city-level Open-Meteo data, and has
no device geolocation or intelligence/notification side effects. Disabling weather
forgets the saved city. The UI displays attribution and gracefully handles outages.

## Evaluations and limits

`uv run pytest` runs application and security regressions. CI additionally runs
`tests/test_intelligence_*.py` into JUnit and produces the SPEC-002 regression
report. These fixtures exercise real application code with synthetic source/model
responses. They establish deterministic behavior, not live model accuracy.
The spec's precision/recall targets remain unmeasured until a representative,
independently labeled corpus is run through the configured live model.

Temporal/PostgreSQL deployment is validated by the repository Compose CI gate.
Real Google delivery, live extraction quality/cost, and mailbox demos require the
owner's private provider configuration. No private credentials are used by CI.

The Compose job also runs `integration/intelligence_temporal_smoke.py` in an
isolated task queue and database workspace. It uses the real source-processing
activities, Temporal child workflows, PostgreSQL schema, Google response
normalization, extraction validation, Today projection, and feedback learning.
Only token retrieval and external Google/model I/O are fixture substitutes; an
additional HTTP guard forbids unmocked requests. The gate checks two source types,
replay receipts, evidence, feedback, pause/revocation, and zero outbound actions,
then deletes only its own seeded workspace/user. This new gate is provisional
until its GitHub Compose run passes; it is not a live Google/model claim.

## Repeatable model smoke check

The smoke command uses nine built-in synthetic examples covering requests,
promises, deadlines, meetings, follow-ups, waiting, completion, informational mail,
and instruction-bearing content. It runs the actual AI gateway and extraction
validator. It never reads a mailbox, writes operational state, or sends email.
Output contains only fixed case IDs, outcome codes, timings, and counts; source
text, model output, API keys, and provider error bodies are excluded.

From the repository root, rehearse without network or private configuration:

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.intelligence_smoke --offline
```

After configuring the API container's `AI_PROVIDER`, `OPENAI_API_KEY`, and
`OPENAI_MODEL`, run the real provider smoke check:

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.intelligence_smoke --live
```

The live run makes nine model requests and API charges can apply. It returns exit
code 0 only when every smoke case passes, 1 for failed cases, or 2 when private
configuration/report output is unavailable. An omitted mode is a dry run that
lists the planned case IDs and makes no requests. Add `--output /tmp/navox-smoke.json`
to retain the sanitized report in the API container. For local development,
run the same module with `uv run python -m ...` from `services/api`.

A successful live smoke verifies model connectivity and these bounded extraction
checks. It does **not** establish the SPEC-002 precision/recall targets or verify
Google sync, Temporal execution, or the full user workflow.

## Completion checklist

- [x] M1–M7 implementation and synthetic application/security regression demos.
- [x] Explicit, offline-rehearsable model smoke command and sanitized results.
- [ ] Run the live model smoke using the intended deployment's configuration and
  record its report and code revision.
- [ ] In the owner's authorized workspace, verify Gmail and Calendar sync produce
  evidence-backed Today items, incremental replay creates no duplicate, feedback
  persists, and completion/waiting transitions match the source evidence.
- [ ] Complete the three required live user demonstrations, using only explicitly
  approved outbound actions, and record sanitized outcomes.
- [ ] Evaluate an independently labeled representative corpus against the
  spec's quality targets. Synthetic regression counts are not accuracy metrics.

Unrecorded live checks remain open; successful builds or fixture runs alone do not
close these acceptance items. Do not include credentials or mailbox contents in
the completion record.

## Verified upstream contracts

- [Gmail incremental synchronization](https://developers.google.com/workspace/gmail/api/guides/sync)
- [Calendar incremental synchronization](https://developers.google.com/workspace/calendar/api/guides/sync)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Temporal retries](https://docs.temporal.io/develop/python/best-practices/error-handling)
- [Temporal history rollover](https://docs.temporal.io/develop/python/workflows/continue-as-new)
- [Open-Meteo forecast](https://open-meteo.com/en/docs) and [city search](https://open-meteo.com/en/docs/geocoding-api)

No dependency version changes were needed for this implementation.
