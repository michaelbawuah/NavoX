# SPEC-003: conservative connection lifecycle

`DELETE /api/v1/connections/{id}` requires an owner-scoped JSON command UUID.
It atomically disconnects the Google account or native connector, increments
every relevant sync generation, closes leases, interrupts an active run, revokes
local credential references, deletes unreferenced local encrypted credentials,
invalidates bound OAuth attempts, and marks subscriptions `cancel_pending` for
upstream cleanup. It does not claim that an already-issued provider request can
be recalled or that a remote OAuth token has been revoked at the provider.
Repeated command IDs are idempotent; a disconnected account cannot resume.

The legacy Google watch cleanup worker retries Calendar and Drive channel
stops with their exact channel and resource IDs and records cancellation only
after Google confirms it. Gmail's stop API affects the whole mailbox, including
a newer connection. Gmail channels therefore remain `cancel_pending`; the
encrypted cleanup credential and delete-data 409 remain until a separately
verified expiry or coordinated manual cleanup. The worker never calls Gmail
mailbox-wide stop automatically.

`POST /api/v1/connections/{id}/delete-data` requires prior disconnect. It
deletes this connection's evidence, receipts, canonical resources, encrypted
import snapshot, cursors, source events and source-linked knowledge. A card
with independent surviving evidence is rebuilt from an active surviving
observation; its source-derived metadata, status, timing and projections are
reset. Cards without other supporting evidence are deleted. Briefing snapshots
are invalidated. Commands check membership and ownership under the same row
lock order as the connector runtime. Both commands reject an untrusted Origin.

**Safety boundary:** Older `person_identities` have no connection provenance.
Deletion returns 409 if a target observation references a person or if the
workspace has a provider identity without connection-level attribution;
pre-migration identities still need reviewed provenance backfill before those
cases can be deleted. Plans and actions may embed source text in fields
without a provenance key, so deletion returns 409 when they coexist with an
affected card. A shared legacy provenance anchor also returns 409. This is an
honest partial implementation and is not full learned-data deletion across
every existing account. Audit retention and upstream subscription cancellation
must be reviewed as part of production acceptance.
