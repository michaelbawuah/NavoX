# SPEC-003: Google Calendar through the common read runtime

## Scope and actual route

The primary Calendar branch of `intelligence.ingestion.process_connection` now
uses `GoogleCalendarConnector` inside the same `ConnectorRuntime` used by other
connectors. The existing source workflow, manual sync/event dispatch, attention
refresh, OAuth endpoints and watch renewal remain the entry points. This is the
**Calendar portion of M2**, not complete Google migration or SPEC-003 completion.
Gmail keeps its existing resumable ID-enumeration/ordering/body-read plan.

```
Existing Calendar SourceWork / process_connection
  -> owner-checked Calendar registration and existing OAuth refresh
  -> ConnectorRuntime + GoogleCalendarConnector
  -> transient CanonicalResource with exact SourceDocument envelope
  -> existing SPEC-002 extraction, receipts and resolution
  -> locator-only registry + transactional revision receipt
  -> final accepted page + original Calendar cursor, committed together
```

## Compatibility

The internal `google-calendar` connection has a deterministic ID and reuses the
original Google connection as its provenance and credential reference. It does
not copy or re-encrypt refresh tokens, create another login, add OAuth scopes,
or send provider writes. The existing Google Workspace compatibility mirror is
queried by definition as well as legacy ID so the two cannot be confused.

Existing `IntelligenceSourceReceipt` rows keep the `calendar` namespace. The
lossless canonical envelope preserves exact text, identities, metadata and
source hashes. Existing observations/commitments keep their old source IDs;
replaying a migrated source does not require another model call when its prior
receipt is reusable. Calendar cancellation and correction still use SPEC-002.
No Calendar-specific reasoning is added to Commitment, Attention or Today.

Calendar remains owned by the source workflows, not two schedulers. Its internal
connection is not activated by generic reconciliation. Generic subscription
registration and the universal management API/UX remain subsequent work.

## Pagination and recovery

The adapter requests one bounded Google page per runtime step. A versioned
cursor records the original sync token, page token, stable run-start time and
bounded identifiers during reset recovery. Incremental pages reuse the same
sync token. Bootstrap pages use a fixed time window. The provider's final token
is required; a missing/malformed token or nonprogressing page fails closed.

HTTP 410 stages a bounded restart without deleting user commitments. The full
window is reread, then known resources missing from that window are individually
revalidated to recover old cancellations/corrections. Up to 2,000 known IDs are
handled in groups of 100, within the runtime's page budget. Raw bodies are not
stored in cursors. Provider/model operations remain time-bounded.

The runtime's finalizer stages the legacy `IntelligenceCursor` in the same
transaction as completion of the universal run. If an old worker advanced that
cursor, it is not overwritten. A crash after final-page acceptance replays
finalization, not provider reads or model extraction. A stable `SyncRequest`
start timestamp also preserves fallback revision timestamps on retry.

## Authorization and privacy

Calendar's original Google connection remains an independent authorization
source. Its user/workspace/account, status, granted scopes and credential
reference are reloaded before reads and before acceptance, page checkpoints and
finalization. The common runtime retains its generation/lease/owner fences.
A scope change, credential replacement, pause or membership removal invalidates
in-flight acceptance. Existing network requests cannot be undone.

The Google-specific read broker uses the original OAuth helper against a detached
record, with no connection lock over the network refresh. It rechecks ownership
before updating expiry metadata; reduced scopes are retained without granting
anything new. Only an operation-scoped access-token handle reaches the provider
adapter. General Secret Broker store/delete behavior for original Google tokens
is unchanged. This is trusted in-process code, not an arbitrary-code sandbox.

A trusted runtime setting selects locator-only storage for this route. Full
canonical envelopes are transient and hashed; the registry stores IDs, times,
status and a digest, not event descriptions, titles, attendees or credentials.
The general adapters' storage policies are not silently changed by this patch.

## Validation

`tests/test_google_calendar_connector.py` uses real database sessions and mock
provider/OAuth/model responses. `NAVOX_CONNECTOR_TEST_DSN` may repeat the suite
on disposable PostgreSQL; each test owns an isolated schema. Coverage includes
entry-point routing, bootstrap/delta pagination, legacy receipt reuse, exact
source hashes, cancellation, 410 reconciliation, partial-page and finalization
replay, overlapping workers, legacy cursor races, stale revisions, and
revocation during provider/model/refresh I/O.

Run the full `bash scripts/check-api.sh`, plus the Calendar suite on disposable
PostgreSQL and the normal hosted CI jobs. Mock providers do not establish live
Google acceptance or all SPEC-003 certification targets.

## Still open

Gmail migration, universal watch/subscription lifecycle, broader canonical-data
privacy, general policy-version changes, safe disconnect/delete-learned-data,
MCP, management UX and complete Google/Canvas/import/unknown-service acceptance
are not completed by this patch. Direct legacy `process_batch_connection`
remains available for existing compatibility tests; the application's Calendar
entry point is the universal path. Multi-calendar discovery is not introduced.

## Provider contracts checked

- https://developers.google.com/workspace/calendar/api/guides/sync
- https://developers.google.com/workspace/calendar/api/v3/reference/events/list

Google documents final-page sync tokens, identical sync-token queries while
paginating, restrictions on time filters during incremental sync, and 410 reset.
The implementation retains the existing primary-calendar scope and bootstrap
window rather than silently broadening access.
