# SPEC-002 operation and verification

## Enable locally

1. Pull the delivery branch, preserve your existing `.env`, then rebuild with
   `docker compose up --build -d`. Migrations run before API/worker startup,
   including 0012 for source-processing receipts and 0013 for resumable Gmail plans.
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
   Each source reports queued, running/retrying, completed, or failed independently.
   Today and the evidence counts refresh after completion. A completed sync with
   no new commitments is valid; it does not establish that extraction quality is sufficient.
5. Today shows evidence references, reasons, uncertainty and feedback controls.
   A sent request can become waiting; only evidence of the intended outcome
   completes it. An expired deadline alone never completes a task.

## Email relevance in Today

Gmail extraction now makes a separate, evidence-backed relevance assessment for
each proposed item before it can enter Today. A high extraction confidence score
alone does not establish a personal obligation. The same model request receives
the connected mailbox address, sender/recipients, and bounded sent/bulk-mail hints;
this adds no second model call or raw connector metadata.

| Email content | Result |
| --- | --- |
| A genuine question or decision the owner needs to answer | Reply-required item |
| Assigned work, a required course/team deadline, or the owner's explicit promise | Action-required item |
| A specific security issue, failed payment, service outage, or change to an existing booking | Important alert in Needs attention |
| An explicit outcome or pending response for existing work | Existing commitment state check |
| Promotions, newsletters, optional offers/RSVPs, receipts, routine updates, FYI, or unclear relevance | No Today item or review card |

Relevance must apply to the owner, use a compatible intent/basis, and have at least
0.90 model confidence. This is a conservative decision threshold, not a calibrated
probability or a measured precision claim. Exact evidence validation still applies.
Spam, trash, drafts, outgoing questions misclassified as replies the owner owes,
and quoted old requests cannot become new tasks. Subject-only proposals that
otherwise qualify still require confirmation. Ordinary messages that need no
action do not accumulate incidental people, date, or relationship records.

An unsubscribe header or Promotions label increases caution, while body-backed
assigned obligations and consequential account/service alerts can still qualify.
Alerts are labeled separately in Today and need no invented reply or deadline.
No classification grants execution permission, sends a message, follows a source
link, or bypasses an existing approval requirement.

The model output contract is `operational-extraction.v2`. Previously processed or
quarantined v1 receipts are honored for unchanged source revisions, so this upgrade
does not trigger a mailbox replay or retry already completed work. Ignored new
revisions receive normal completion receipts and are skipped on retry. Existing
saved cards are not bulk-deleted or silently reclassified by a rebuild; the owner
can use **Not a task** on unwanted active AI-created items. Model-backed relevance
on fresh Gmail processing and cleanup of legacy cards remain owner-side checks.

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

Gmail additionally checkpoints a bounded read plan in `gmail_sync_plans`. It saves
the initial history cursor, a fixed bootstrap boundary, list-page progress, message
IDs/deletion flags, chronology timestamps, and completed positions. First it reads
only IDs and timestamps to establish chronological order; then each full message
is downloaded, validated, resolved, and acknowledged with its receipt and plan
position in one transaction. A late provider or model failure resumes at the
unfinished message. Successfully completed message bodies and prior list/chronology
pages are not downloaded again for that plan. Bodies, subjects, addresses, and
credentials are never stored in the plan. It is removed atomically with successful
cursor publication; an interrupted plan retains the original cursor.

Chronology adds one body-free GET per live message on a new plan. This trades extra
initial requests for causal ordering and durable recovery without caching private
mail. It does not reduce the cost of the first uninterrupted bootstrap. Existing
revision receipts still prevent repeated model extraction of unchanged revisions.
Expired history includes known referenced resources before sorting; deletions are
processed without a model call. Authorization and source cooldown are rechecked
under the connection lock between checkpoints.

The intelligence workflow contains identifiers only. PostgreSQL holds current
facts; Temporal retries work and refreshes time-dependent state and attention.
Pause and revoked scopes are checked again inside processing activities.

Manual sync status is read from the exact Temporal run through the authenticated
`GET /api/v1/intelligence/sync/status?workflow_id=...` endpoint. Connection ownership
is checked before contacting Temporal. Only state, a completed commitment count,
and allowlisted failure categories are returned; provider prose and workflow
payloads are never exposed. Polling does not initiate another source read. The UI
tracks jobs while the page stays open; it does not recover manual job IDs after a
full reload. Repeated status-service failures pause polling and offer **Check sync
status**, which checks the same job rather than submitting another one.

For `google_api_disabled`, enable the Gmail API or Google Calendar API in the
Google Cloud project that owns the OAuth client. For `google_scope_missing` or
`google_authentication_failed`, reconnect read access. A generic
`google_permission_denied` can also reflect Google Workspace policy and does not
prove that reconnecting will resolve it. `google_token_unavailable` can reflect
missing credential configuration or an unusable refresh token. Rate limits,
transport failures, and provider outages have separate categories. The failure
audit retains the legacy error type and adds `metadata.error_diagnostic` with a
fixed code and optional HTTP status. Older failures may only show the generic
category; a new sync on the rebuilt API and worker will use the new diagnostics.

For `provider_request_failed`, new diagnostics also preserve a fixed
`provider_code`: for example `timeout`, `transport_error`, `rate_limited`,
`quota_exhausted`, or `incomplete_response`. The same allowlisted category reaches
the audit, Temporal failure details, authenticated status endpoint, and UI help.
HTTPX timeouts are distinguished from other transport failures; either can occur
without an HTTP status. A missing HTTP status alone does not prove a timeout.
Raw exception text, network addresses, provider payloads, and source content are
excluded. Existing audit rows and completed workflow failures retain their older
diagnostics; inspect a fresh failure after rebuilding both API and worker.
AI response reads now default to 120 seconds, replacing the original 30-second
limit. Set `OPENAI_READ_TIMEOUT_SECONDS` in the private environment to a finite
value from 1 to 300 seconds; both API and worker receive it through their shared
settings. Existing environments get 120 without adding a variable. Connection,
write, and pool waits remain capped at 10, 30, and 5 seconds respectively (or the
configured read limit when smaller). The whole HTTP exchange, including receiving
the body, is bounded by the read limit plus 45 seconds: 165 seconds by default.
HTTPX's phase limits measure inactivity, so the overall deadline also prevents
a continuously trickling response from running indefinitely. Timeouts remain
sanitized and retryable, and external task cancellation still propagates.

The model smoke check uses a matching case deadline: read limit plus 60 seconds
for live CLI runs, 180 seconds by default. Its previous 45-second outer limit
would otherwise stop a valid slower response before the adapter's new deadline.
This does not increase retry counts, publish an unfinished Gmail cursor, or clear
completed receipts/checkpoints. A timed-out response is never accepted as an
empty extraction or quarantined as a completed message.

The owner initially reported AI timeouts after the diagnostic update, with 27
active commitments and 167 evidence references saved. On 2026-09-23, after the
timeout adjustment was delivered as `bb576277b888da1041f3d1c51cd4d7c7adb071e9`,
the owner supplied screenshots showing a successful API/worker rebuild, an active
AI read timeout of 120.0 seconds, and Gmail reporting "Sync complete" with 87
commitments processed. The dashboard showed 84 active commitments and 459 evidence
references. The processed count covers commitments handled by that sync; the
active count covers workspace commitments in active states, so the two counts
need not match. This records owner-observed completion, not a direct inspection of
the Mac runtime or proof that every source produced a valid extraction. The owner
subsequently reported the requested repeat Gmail sync was done and looked good,
with no issue reported. This is manual owner confirmation; repeat-run counters
and item identities were not independently inspected. Evidence review remains
open. The earlier timeout category did not identify whether connect/read/write/pool
timed out.

The owner's subsequent Today review showed a promotional-looking candidate and
only an opaque Gmail reference in **Why this is here**. The original message was
not supplied, so its intent has not been independently classified. The source
review gap is repaired with **View source text**: an explicit, authenticated
`GET /api/v1/intelligence/evidence/{evidence_id}` retrieves that Gmail message and
returns up to eight saved evidence spans, each at most 512 characters, only after
checking the source hash. Source ownership, workspace membership, Gmail read
permission, and agent pause are checked before and after provider I/O; narrowed
refresh grants and existing source cooldowns are honored. The request has a
30-second deadline and returns fixed error messages. Changed/deleted sources or
invalid locators return no excerpt. One compatibility hash omits only the newly
normalized unsubscribe flag for evidence saved before that change.

Today itself still performs no Google reads and stores only locators. The source
reader uses uncached responses, renders passages as escaped text, and retains no
email body or quote in the database. Gmail normalization now passes the boolean
presence of a nonempty `List-Unsubscribe` header into the email relevance
filter, without retaining the header's URL or token. Extraction instructions also
exclude optional promotional calls to action while preserving explicit account
obligations. This does not establish model accuracy or reclassify saved candidates;
review existing items against their sources and reject unwanted suggestions.

The next owner screenshot confirmed that the source reader displayed an email
subject. It also exposed a high-confidence active RSVP task supported only by an
invitation headline, with the same source header repeated. The excerpt establishes
the invitation, not that the owner chose to attend. Gmail proposals supported only
by subject-line spans now have confidence capped at 0.89, so new items require
confirmation and subject-only completion/waiting proposals cannot automatically
change task state. Body-backed requests and Calendar evidence retain their existing
rules. This conservative review rule does not measure overall extraction accuracy.

Today collapses identical citations from the same account and source revision;
distinct passages remain available under one source heading. Different accounts
and source revisions retain their evidence, and no evidence records are deleted.
Source buttons use the existing application styling and still require a click
before any Google read.

Saved active AI-created items have an explicit **Not a task** action. The owner can
dismiss an unwanted item through an authenticated, ownership-scoped state update
to `rejected`; repeated dismissal is idempotent and records one bounded audit event.
Matching subsequent evidence does not reactivate the rejected commitment. This
action sends no message and makes no Google or model request. Existing candidates
retain **Reject**. Previously saved active items are not silently demoted, because
their state may reflect an earlier user confirmation. After rebuilding API, worker,
and web, dismiss one unwanted active item and reload to verify it stays absent.
The owner-side check of these new controls remains open.

Gmail reads are spaced at least 250 ms apart within each source gateway. Temporary
rate-limit and server errors retry the failed GET (at most three HTTP attempts)
with bounded exponential backoff and jitter; they do not immediately restart the
entire fetched batch. Valid `Retry-After` seconds or HTTP dates are honored within
a one-day bound. Inline retries have a total backoff budget of 60 seconds per GET;
longer delays are handed back to the durable workflow. Pacing is local to the
gateway, not a global limiter across independent deployments or other Gmail apps.
Resumable Gmail plans also persist a 500 ms read spacing under that connection
lock, so overlapping NavoX jobs cannot multiply the plan's read rate. This spacing
does not account for other apps or separate connections to the same Google account.

Exhausted Google rate-limit, quota, and server errors record a source-specific
`retry_not_before` in the failure audit. Manual sync, source activities, and
background reconciliation check this persisted cooldown before another provider
request. Transient limits and server errors use at least the supplied retry delay
(60 seconds by default); explicit daily and
general quota errors stop activity retries and use a five-minute cooldown when
Google provides no retry delay. These defaults are backoff choices, not claims
about when Google's quota resets. A blocked check does not extend the cooldown.
Calendar is not blocked by a Gmail-only cooldown.

`google_daily_limit_exceeded` identifies an explicit daily project limit;
`google_quota_exceeded` identifies another quota cap. Check the Gmail API's quota
metric and configured limit in the owning Google Cloud project. The app does not
raise quotas or enable billing. `google_rate_limited` remains the temporary rate
category (including otherwise unspecified HTTP 429 responses).
For a recognized legacy HTTP 403 rate error, `provider_reason` also retains exactly
`userRateLimitExceeded` or `rateLimitExceeded`. Unknown, conflicting, or unspecified
reasons are omitted. This preserves Google's reported reason without inferring
which Cloud Console quota metric caused it; raw error prose remains excluded.

Briefing requests and lifecycle activities serialize proactive evaluation within
each workspace before loading preferences or signals. This prevents concurrent
first use from creating duplicate preferences or colliding on a signal's unique
fingerprint. The PostgreSQL integration gate exercises eight concurrent callers,
stable signal IDs, one creation audit per signal, and retained dismissal/snooze
state. CI captures API, worker, and database logs before cleanup if a gate fails.

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
then deletes only its own seeded workspace/user. This gate passed in GitHub CI
at commit `5a55c19673d0001c929ab5bab6f126d2c16f9bf5` (run 35822900374);
it is not a live Google/model claim.

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

The full live run makes up to nine model requests and API charges can apply. It stops
on the first provider or transport failure and records the remaining cases as skipped.
The CLI prints case progress to stderr while keeping its final JSON report on stdout.
It returns exit
code 0 only when every smoke case passes, 1 for failed cases, or 2 when private
configuration/report output is unavailable. An omitted mode is a dry run that
lists the planned case IDs and makes no requests. Add `--output /tmp/navox-smoke.json`
to retain the sanitized report in the API container. For local development,
run the same module with `uv run python -m ...` from `services/api`.

A successful live smoke verifies model connectivity and these bounded extraction
checks. It does **not** establish the SPEC-002 precision/recall targets or verify
Google sync, Temporal execution, or the full user workflow.

### Email relevance smoke check

The separate `email-triage` suite contains 24 synthetic Gmail cases: ten required
replies/actions, consequential alerts or commitment updates, and fourteen sources
that should create no item. Cases include optional webinar invitations, offer
deadlines, receipts, ordinary sign-ins, copied requests assigned to others, quoted
answered requests, genuine course deadlines, and failed payments with unsubscribe
footers. It uses the same owner context, schema/evidence validator and surfacing
policy as Gmail processing. The original default nine-case suite is unchanged.

Rehearse the fixture wiring without private configuration or network:

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.intelligence_smoke --offline --suite email-triage
```

After rebuilding API and worker, evaluate the configured model on these examples:

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.intelligence_smoke --live --suite email-triage
```

The live suite makes up to 24 billable model requests and stops on the first
provider failure. It reads no mailbox and writes no application state. Use
`--suite email-triage --case reply-needed` for one case. Reports include only fixed
case IDs, outcomes, counts and sanitized errors, never source/model text. A passing
offline run verifies authored fixtures, not model classification accuracy. The new
live suite has not yet been executed in the owner's environment; the earlier 9/9
report below does not verify this schema or relevance policy. A representative,
independently labeled mailbox evaluation remains necessary for quality targets.

### Recorded owner-side live result

The owner supplied a successful report generated at
`2026-09-23T06:01:05.578298+00:00`: all nine cases executed and passed, with no
failures or skips. The sanitized report is retained in
[`evals/intelligence/reports/2026-09-23-live-smoke.json`](../../evals/intelligence/reports/2026-09-23-live-smoke.json).
The delivery revision supplied for this run was
`88f426350648e093b73ae29e32f5ab48251f1ca7`. The submitted report does not itself
attest the container's Git revision. This is an owner-reported live result, not
a live run performed by CI. `live_google_chain_verified` and
`production_quality_measured` remain false.

### Diagnose a provider failure

Run one synthetic case when every case reports `provider_request_failed`:

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.intelligence_smoke --live --case explicit-request
```

The case's `provider_error` reports only an HTTP status and a fixed category such
as `authentication_failed`, `permission_denied`, `model_unavailable`, `quota_exhausted`,
`rate_limited`, or `invalid_schema`. Unknown provider codes and all raw messages,
keys, response bodies, and request content are excluded. Use this report to identify
the actual cause before changing account settings or rerunning the full suite.
See the [official API error reference](https://developers.openai.com/api/docs/guides/error-codes).

### Diagnose an extraction validation failure

`extraction_validation_failed` means a returned proposal did not pass the local
schema or evidence checks. The case's `validation_error.code` distinguishes fixed
categories such as `schema_invalid`, `evidence_text_mismatch`, `object_not_grounded`,
or `temporal_not_grounded`. Reports never include raw validation messages, model
output, source text, or Pydantic input/context values. Share the sanitized report
to identify the violated contract instead of repeatedly retrying a failed suite.

The extractor can correct a model's miscounted evidence offsets only when the
unaltered quote occurs exactly once in its declared subject/content field. It
computes Unicode character positions deterministically and reruns all evidence,
grounding, and instruction checks. Correct supplied offsets remain valid for
repeated quotes; incorrect offsets for ambiguous, absent, or paraphrased quotes
are rejected. Quotes are never fuzzily matched, normalized, or replaced.

This compatible validation repair retains the extraction/receipt version and does
not trigger automatic mailbox replay. Existing rejected receipts stay quarantined;
new source revisions use the repaired validation. Any reprocessing of historical
quarantined sources must be separately scoped and verified.

## Completion checklist

- [x] M1–M7 implementation and synthetic application/security regression demos.
- [x] Explicit, offline-rehearsable model smoke command and sanitized results.
- [x] Owner-side live model smoke: nine of nine cases passed on 2026-09-23;
  sanitized report and supplied delivery revision recorded above.
- [x] Owner-reported Gmail sync completion after the timeout adjustment on
  2026-09-23: 87 commitments processed, 84 active, and 459 evidence references.
- [x] Owner-reported repeat Gmail sync check: completed and looked good, with no
  issue reported. This is a manual result, not a database-level duplicate audit.
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

### Next owner-side check: connected sources

1. Open `http://localhost:3000` and find **Connected understanding**. Confirm both
   Gmail and Calendar show **Read access granted** and the agent is not paused.
2. Use **Sync Gmail** and **Sync Calendar**, keeping the page open until each source
   reports completion or a specific failure. Today refreshes after completion;
   **Refresh Today** remains available. Queue acceptance alone is not proof of
   completed processing.
3. Open **Why this is here** on a resulting item and check the source, request or
   meeting, and date against the original Google source. Relevant source content
   is processed by the configured AI provider under the enabled read permission.
4. Sync both sources again without changing them. Verify the same commitment is
   retained without an extra copy. Apply feedback to a test item, reload, and
   verify the feedback persists.
5. Record only sanitized outcomes and any fixed error categories. Do not copy
   private emails, credentials, or full source identifiers into this record.

If a previously rejected source stays absent, its quarantine receipt may still
apply. A fresh, non-sensitive source revision can exercise the repaired extractor;
this check must not silently clear receipts or rescan historical rejected content.
The three live demonstrations and representative quality evaluation remain
separate checklist items even after this connected-source check passes.

## Verified upstream contracts

- [Gmail incremental synchronization](https://developers.google.com/workspace/gmail/api/guides/sync)
- [Calendar incremental synchronization](https://developers.google.com/workspace/calendar/api/guides/sync)
- [Gmail error reasons](https://developers.google.com/workspace/gmail/api/guides/handle-errors)
- [Gmail per-method costs and quotas](https://developers.google.com/workspace/gmail/api/reference/quota)
- [Google service error categories](https://docs.cloud.google.com/java/docs/reference/proto-google-common-protos/latest/com.google.api.ErrorReason)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Temporal retries](https://docs.temporal.io/develop/python/best-practices/error-handling)
- [Temporal history rollover](https://docs.temporal.io/develop/python/workflows/continue-as-new)
- [Open-Meteo forecast](https://open-meteo.com/en/docs) and [city search](https://open-meteo.com/en/docs/geocoding-api)

No dependency version changes were needed for this implementation.
