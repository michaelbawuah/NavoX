# SPEC-003 checkpoint: credential ownership and lease lifetime

This checkpoint implements a narrower Secret Broker boundary; it does not close
all SPEC-003 milestones or certify the connector platform for production.

## Enforced boundary

- Every credential store, lease and delete operation requires explicit connection,
  workspace and user IDs. The server reloads ownership and membership. New leases
  also require an unpaused owner, an active connection and an enabled definition.
- New encrypted envelopes bind all three owner IDs and the credential row ID.
  Moving a credential reference or copying ciphertext into another credential row
  cannot authorize decryption for that other identity.
- Only `health.read` and `sync.read` lease purposes are supported. The requested
  secret names must be a nonempty subset of the connection's bound bundle.
  There is no write-purpose credential path in this checkpoint.
- Leases expire after at most 300 seconds using a monotonic clock. Context exit
  closes a handle on success, failure and cancellation; closed/expired handles
  refuse reads. Ordinary copying, JSON serialization and pickling are rejected.
- The sync runtime requires an explicit user ID and evaluates read authority
  before its credentialed health probe. It leases separately for health and each
  provider sync call, closes the handle before downstream intelligence, and
  rechecks owner/connection authority before the next provider operation.
- Health activities use the same ownership checks and always close their handle,
  including a provider factory exception. No credentials appear in audit metadata;
  successful broker operations add content-free audit events in the caller's
  transaction. Failed operations may roll back their pending audit transaction.

## Credential formats and compatibility

New bundles record the encryption-key source as `connector-v2:primary` or
`connector-v2:google-local`. Adding a dedicated connector key does not change
which key decrypts an existing locally encrypted v2 bundle. Losing or replacing
that recorded key requires explicit reauthorization; this is not a general key
rotation/key-ring implementation.

The old `connector-v1` format did not bind a bundle to its owner. New leases
refuse it rather than silently inferring ownership. An explicit owner-scoped
`store` with newly supplied credentials replaces that connection's reference;
it does not decrypt or delete the old unbound bundle. Retention/cleanup of those
old protected rows remains a separate migration task.

Google's original credential flow is unchanged. This broker will not overwrite
or delete original Google credentials. A v2 credential still referenced by a
legacy provenance row or another connection is retained when its owning universal
connection detaches it; cleanup must reconcile those references separately.
No basic disconnect/delete-learned-data completion claim is made here.

## Limits

A lease is a trusted in-process interface, not a sandbox against arbitrary Python
introspection. Closing it drops references held by that handle; it cannot erase a
string already copied by an adapter or cancel a network request already sent.
New lease requests see committed revocation, but an issued handle can remain
usable until its operation finishes or its TTL expires. Provider clients still
need bounded operation timeouts and outbound-network isolation.

The runtime's crash recovery, duplicate-consumer semantics, concurrent sync
fencing, revocation checks around downstream commits, backoff, and final cursor
commit require their own M3 checkpoint. Google universal migration, MCP,
connection-management UX and full portability/security acceptance remain open.

## Validation

Run `bash scripts/check-api.sh` from the repository root with the committed
lockfile. The full API suite includes tenant mismatch, ciphertext swapping,
stale-session revocation, owner pause, definition disablement, scoped names,
expiry, serialization, key-source compatibility, safe credential deletion, and
real runtime/health-activity cleanup paths using synthetic providers.

Hosted API, Web, extension, dependency-security, evaluation/hardening and Compose
checks must still pass on the exact published head. Synthetic tests do not prove
live-provider behavior or full SPEC-003 security certification.
