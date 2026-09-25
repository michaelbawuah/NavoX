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
stops with their exact channel and resource IDs. It can also retire Gmail,
Calendar and Drive watches five minutes after a provider-confirmed expiry,
without reading an OAuth credential or making a provider request. Registration
records `expiration_confirmed_at` only after a successful, validated provider
response. The temporary ten-minute registration deadline cannot authorize
expiry cleanup. Google's [Gmail watch response contract](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/watch)
defines `expiration` as the time notifications stop.

Expiry retirement checks the disconnected owner and workspace and conditionally
matches the claimed row, confirmation and expiry, so a concurrent reconnect or
renewal cannot reuse stale authority. It unblocks delete-data once all remaining
subscriptions have retired; deletion then removes the retained encrypted cleanup
credential. The migration leaves existing confirmation fields empty because old
deadlines may be provisional. Those rows still require separately verified
cleanup. Gmail's stop API affects the whole mailbox, including a newer connection,
so the worker never calls mailbox-wide stop automatically.

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
