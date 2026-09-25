# SPEC-003: Gmail reads through the shared runtime

Gmail's existing source entry point now executes `GoogleGmailConnector` through
`ConnectorRuntime`. Together with the preceding primary Calendar migration,
both established Google read paths use the shared acceptance/fencing mechanism.
This is not completion of SPEC-003, a universal subscription lifecycle, a public
launch, or evidence of live-account acceptance.

## Preserve the existing mailbox behavior

The adapter retains the bounded, chronological Gmail plan: enumerate identifiers,
obtain metadata for chronological ordering, then fetch/accept one full message
at a time. Stable bootstrap bounds, pre-scan history anchors, incremental
history, bounded expired-history reconciliation, pagination reset, and the
existing read-spacing seam remain. A reset revisits known source IDs rather than
assuming missing messages were deleted. Disappearance after metadata becomes a
later tombstone, not an out-of-order fact. No extra profile health probe changes
the synchronization anchor. Existing relevance filters and owner-aware
extraction/resolution remain in the SPEC-002 pipeline.

The source namespace, original document IDs/hashes, original OAuth connection,
credential reference and granted scopes are retained. Gmail writes remain behind
SPEC-001; this adapter neither grants permissions nor exposes an execution path.
Existing source workflows, watches and recheck workflows remain responsible for
scheduling. The migration does not introduce duplicate universal reconciliation.

## Accepted progress and recovery

The runtime checkpoints each bounded provider operation, with a trusted larger
checkpoint budget for this multi-phase plan, not a larger message enumeration
limit. It persists only IDs, timestamps, positions and history/page tokens.
`GmailSyncPlan` remains an IDs-only progress projection for existing status UI.
Existing partial plans can be adopted without restarting enumeration.

Resource acceptance, SPEC-002 state, processing receipts and result IDs are
transactional. The page callback updates the compatibility projection only after
acceptance; a crash before that callback can reread the page but uses the receipt
to avoid a second model call. A failed finalizer resumes from the accepted final
page without repeating provider/model work. Only finalization advances the
original Gmail history cursor, removes the progress projection and records the
completion audit, in the same transaction as runtime completion. A competing
attempt is busy, not falsely successful. Committed cursor/plan ownership changes
invalidate acceptance. Provider backoff is durable and cannot be bypassed by a
new request. Expired enumeration tokens get a bounded checkpointed reset, then
surface the original error to the workflow rather than looping indefinitely.

The existing source-activity final bookkeeping remains separate from the shared
runtime's accepted completion. A failure *after* runtime completion but before
source-event bookkeeping may enumerate incremental history again on activity
retry; unchanged accepted revisions reuse receipts. This change does not claim
zero provider calls for that separate activity-replay window.

## Authorization and privacy

The original Google read authority and protected credential vault are reused.
Authority is reloaded before every provider operation and accepted checkpoint,
including committed owner pause, membership removal, connection/definition
changes, credential replacement and grant narrowing. Late OAuth refresh cannot
restore revoked permissions. The broker may cache an access token inside this
one in-process sync attempt, with the existing 45-minute/expiry refresh bounds;
each adapter receives a new short-lived scoped lease that closes before
intelligence processing. The cache is not serialized, logged or passed to models.
Closing a lease cannot undo a request already sent to Google.

Full message envelopes are transient. This path explicitly selects locator-only
resource persistence, while existing bounded evidence remains in SPEC-002.
Provider bodies, subjects, senders and credentials do not enter the runtime
checkpoint or resource registry. Gmail source links point to the original
message. The storage policy of unrelated generic adapters is unchanged.

## Validation

Run `bash scripts/check-api.sh` on the unchanged locked dependencies. The new
`tests/test_google_gmail_connector.py` suite supports disposable PostgreSQL
schemas through `NAVOX_CONNECTOR_TEST_DSN`; never use a production database.
It covers runtime routing, chronology, legacy-plan adoption, receipt reuse,
privacy, competing attempts, revocation during model/provider/OAuth I/O,
cancellation, backoff, and replay. Existing Gmail resumption tests retain their
assertions; their helper explicitly advances persisted backoff to exercise
post-cooldown resumption without wall-clock sleeps. Separate new tests assert
that an early retry is blocked.

Tests use provider/OAuth/model fixtures. They do not establish live inbox
precision, live provider availability, full Google watch lifecycle migration,
MCP, management UX, safe deletion, or final connector portability certification.

## Provider references

- Gmail incremental/full synchronization: https://developers.google.com/workspace/gmail/api/guides/sync
- History ID and expiry semantics: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Message metadata/full formats: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get
