# SPEC-003: fenced read-sync recovery

This checkpoint implements read-sync recovery. It does **not** complete SPEC-003,
Google migration, subscription registration, MCP, the management UI, full
safe-delete semantics, or live-provider acceptance.

## Persisted ownership of work

`connector_connections` carries a run ID, generation, opaque attempt token,
90-second expiry and a provider retry deadline. Claiming work is a short locked
transaction with a conditional update. Duplicate active requests are busy, not
successful. A restarted attempt may resume its request after the lease expires;
a different request can supersede an expired attempt. The old token can no
longer acknowledge a resource or overwrite the winning cursor.

The expiry uses the database clock. PostgreSQL uses `clock_timestamp()` rather
than transaction-start `now()`. A separate AsyncSession renews the lease every
15 seconds. Network operations are bounded to 240 seconds. A heartbeat failure
never implies success: every persistence checkpoint requires a live matching
token. Process death leaves recoverable state; cancellation closes the secret
handle, stops heartbeats, and records an interrupted attempt when the database
is reachable.

## Acceptance and cursor rules

A resource, downstream database changes, result IDs and a revision receipt commit
in the same transaction. The trusted consumer may use savepoints but cannot
commit the outer session before the runtime's final authorization check.
A page cursor is saved only after all its accepted resources are durable. The
connection's source cursor changes only when the final page has been accepted.
A crash between final-page acceptance and finalization replays finalization,
not model calls. An incomplete or failed run is never returned as completed.

Receipts contain IDs/hashes/outcomes and a consumer version, not source bodies.
The current resource registry retains the existing adapter-specific canonical
storage policy; this change does not claim that all generic canonical content
is safe to persist. Unchanged accepted resources can reuse the same consumer
version's receipt across requests; an explicit new consumer version can
reprocess them. A request ID cannot silently switch consumer versions.

Provider timestamps, where present, prevent older revisions from resurrecting
newer resources/tombstones. This does not invent ordering for providers without
reliable version/timestamp semantics. Pagination loops and undeclared provider
or resource identities fail closed. Full-scan adapters still need their own
consistent snapshot/pagination semantics; the runtime cannot manufacture those.

## Authorization and external I/O

No connector ownership lock is held over a provider or model request. Before
committing accepted work, the runtime reloads and locks the owner, membership,
definition and connection. It compares the original definition/version,
manifest, configuration, credential reference, capabilities and policy snapshot.
A pause, disconnect, membership removal, disabled definition, credential
replacement or capability/configuration change invalidates in-flight acceptance.
Changes that commit *after* an accepted revision do not retroactively remove
that already authorized revision. A pause is not a delete operation.

Already-sent external read requests cannot be undone. Tokens are still trusted
in-process handles, not a sandbox against arbitrary Python code. No write
execution or capability grant is introduced by this checkpoint. The caller
supplies deterministic policy decisions; introducing dynamically changing
workspace policy requires the same versioned recheck contract.

## Retry, scheduling and compatibility

Retry-After seconds/HTTP dates are reduced to a bounded duration (at most one
day). Rate limits and transient failures persist a connection-level deadline;
new request IDs do not bypass or slide an existing deadline. Reconciliation
resumes an incomplete run's request instead of declaring it successful or
continually creating new attempts. Completed replay returns durable result IDs,
so the activity's returned count does not fall to zero after a retry.

Temporal sync activities heartbeat identifiers only and preserve cancellation.
Workflow patches retain prior heartbeat/child-ID behavior while replaying old
histories. Google compatibility mirrors stay on the existing Google workflow;
they are excluded from universal reconciliation until the real migration is
implemented. Their OAuth scopes are never replaced with canonical capabilities
by the compatibility provenance helper.

Migration `0016_connector_sync_recovery` adds the lease/checkpoint fields and
`connector_sync_receipts`. Completed historical runs remain readable. Old
uncheckpointed incomplete runs require an explicit new request; the migration
does not fabricate receipts or claim that old partial work was accepted.

## Verification

Run `bash scripts/check-api.sh`. Additional tests in
`tests/test_connector_sync_recovery.py` use real sessions and can be repeated on
a disposable PostgreSQL database via `NAVOX_CONNECTOR_TEST_DSN`. Each PostgreSQL
fixture owns and drops a unique schema. Never point this test option at a
production database. CI adds a PostgreSQL migration round trip and the same
recovery suite without removing any existing CI gate.

Coverage includes same/different-request overlap, worker expiry and takeover,
partial-page replay, finalization replay, committed authorization changes during
both provider and model I/O, consumer savepoints/early-commit rejection,
versioned acceptance reuse, stale tombstones, cursor loops, persisted backoff,
lease renewal/cancellation, reconciliation and real SPEC-002 ingestion.
Local mock-provider tests do not establish live Canvas/REST availability or
prove the remaining SPEC-003 portability/security targets.

## Implementation references

- SQLAlchemy 2.0 asyncio: separate sessions for concurrent tasks and explicit
  handling of expired ORM state: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- PostgreSQL locking semantics: https://www.postgresql.org/docs/17/explicit-locking.html
- PostgreSQL current clock vs transaction timestamps:
  https://www.postgresql.org/docs/17/functions-datetime.html
