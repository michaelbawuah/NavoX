# SPEC-003: source contention is not a provider failure

## Observed failure

Normal PR CI run 161 failed the Compose PostgreSQL/Temporal smoke after the
Gmail migration. The harness required a second activity to block on a legacy
`connections ... FOR UPDATE` row while the first provider call was suspended.
The common runtime intentionally releases database locks around network I/O and
uses expiring attempt ownership instead. Reintroducing a network-duration row
lock would defeat revocation and recovery behavior.

The failure also exposed a real boundary bug: a local "already active" exception
was classified as a new provider outage. The source activity wrote a shared
provider cooldown and marked a pending event failed even though the provider
had not been called by that activity. Repeated local contention could extend
that artificial delay.

## Repair

`SyncAlreadyActive` distinguishes common-runtime claim contention from upstream
failures. The Google adapters translate it to `GoogleSourceBusyError`; Gmail's
pre-claim guard emits the same type. The source activity rolls back the losing
caller's transaction and asks Temporal to retry after 15 seconds, without
creating a failure audit/cooldown or failing the queued event. Real rate-limit,
quota and provider errors retain their existing failure and cooldown paths.
This does not add a permission, write operation, or new external API.

The Compose test now gates the first actual provider response, waits for the
second real source activity to receive the typed contention result, and checks:

- The active run, generation, attempt token and checkpoint remain unchanged.
- Both source and connector authority rows can be locked with PostgreSQL
  `FOR UPDATE NOWAIT` while the provider is blocked (no lock over network I/O).
- The losing activity makes no provider/model request and creates no cooldown.
- A real quota response still produces exactly one durable quota audit/deadline;
  queued retries and a later request observe it without contacting Google.
- Resume preserves the original receipt, chronological order, message-read
  counts, and final source token, with no duplicate intelligence processing.

The resume fixture keeps its existing isolated source-audit deadline expiry to
avoid waiting five minutes in CI. Daily-quota failures are non-retryable provider
errors and do not invent a transient-runtime backoff deadline. The separate
quota test still verifies that real queued requests cannot bypass or slide the
source deadline. Production checks and durations are unchanged.

## Verification

Run the full `bash scripts/check-api.sh`. New direct source-activity regressions
exercise provider and model I/O contention, queued-event preservation, fence
identity, no duplicate reads/extractions, and immediate post-completion replay.
Two source-boundary cases verify rollback for both Gmail and Calendar. With the
specialized handler removed, the new concurrent activity tests fail because an
artificial shared cooldown is created.

The PostgreSQL suites and the complete unmodified Compose smoke invocation must
also pass; local SQLite tests alone cannot verify row-lock behavior or Temporal
retries. The patched smoke continues to test quota serialization, resumption,
relevance, feedback, pause/revocation, rechecks, and proactive concurrency. Its
production checks are not skipped, its timeouts are not lengthened, and its
positive/negative extraction assertions are preserved.

This repair does not implement management UX, MCP, general subscription
lifecycle, safe learned-data deletion, or full SPEC-003 acceptance. Provider
responses remain synthetic; a passing smoke is not live-provider certification.
