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

**Historical provenance:** Older identities and plans remain unattributed after
migration. The private review workflow below can explicitly attribute their
complete history; it does not guess or erase them during migration. New bounded
plans record all source connections for the root and included related cards,
including sources beyond the twelve-source display limit. Email approval plans
also record their outbound connection. Plan creation shares the owner lock with
connection deletion. Unknown or foreign source edges keep attribution incomplete.

Deletion removes attributed, source-influenced plans, steps, actions, approvals,
workflow references and their source-bearing audit records. Independent plans
and manual commitments survive. A mixed-source execution history is erased as a
whole because its free text cannot be safely separated; surviving commitments
still follow the independent-evidence recomputation rules above. Active plans or
actions block deletion. Terminal uncertain email histories are erasable locally;
this cannot recall an already-issued provider request or certify its outcome.
Other ownership, surviving-evidence and subscription guards remain in force.

## Inspect historical blockers without changing data

Run this against the deployed app after updating the API image:

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.connector_deletion_review --list

docker compose exec -T api uv run --no-sync python -m navox.evaluation.connector_deletion_review \
  --connection-id 00000000-0000-0000-0000-000000000000 \
  > spec-003-deletion-review.json
```

The first command lists at most 100 eligible connection IDs and reports truncation.
The second reports counts for pending/unconfirmed watches, pending universal
subscriptions, un-attributed identities, potentially embedded plans/actions/
approvals, shared anchors, and partial ingestion without an anchor. It includes
no emails, identity values, tokens, source bodies or provider errors. It does not
change a grant, assign an identity to a guessed source, cancel a watch, or erase
anything. A false `requires_historical_review` is an inventory result, not deletion
authorization: the deletion transaction still checks ownership and surviving
provenance. Run it for the connections used before migration `0020_identity_sources`.

When a review count is nonzero, preserve the existing guarded behavior until the
actual records have been reviewed. Provider names alone cannot reconstruct lost
identity provenance, and an old provisional watch deadline is not confirmation
of provider expiration. These are historical-data acceptance cases, not a reason
to bypass the source-only deletion boundary.

## Apply an owner-reviewed historical attribution

Migration `0022_plan_sources` adds plan provenance without assigning old rows.
The review command works for an owner with a personal workspace. Ambiguous
identities in shared workspaces require a separate ownership review and are
rejected by this tool. It never changes grants, sends email, disconnects, or
deletes data. No flag can skip the existing deletion guards.

1. After updating the API and worker, export a private manifest:

   ```bash
   docker compose exec -T api uv run --no-sync python -m navox.evaluation.connector_provenance_review \
     --connection-id YOUR_CONNECTION_UUID --file /tmp/spec-003-provenance.json
   docker compose cp api:/tmp/spec-003-provenance.json ~/spec-003-provenance.json
   chmod 600 ~/spec-003-provenance.json
   ```

   The output includes only a review digest, counts and a notice. The file is
   created with mode 0600 and cannot overwrite an existing path. It includes
   identity values, saved plan snapshots, action text and source records needed
   for local review. Keep it private and outside the repository; do not paste it
   into chat or publish it. It contains no connection credentials or tokens.

2. Review `snapshot` and each `assignments` entry against your actual history.
   A sole matching provider connection is only a proposed identity attribution;
   it cannot prove there was never another account or source. Include **every**
   supporting source connection. Plan proposals use current root/related source
   records and the direct connection in email approval snapshots. Check old
   snapshots and payloads because current source edges can differ from history.
   Change proposals in `assignments` when needed; do not edit `snapshot`.

   `connection_ids: null` is unresolved and cannot be applied. An empty list is
   allowed only for a reviewed independent plan, never an identity. A current
   source link or an explicit approval-plan connection cannot be removed by a
   review. If the available source IDs cannot represent the actual history,
   leave it unresolved rather than assigning an inaccurate source.

3. Copy the reviewed file back, obtain its digest, and explicitly approve that
   exact file only after the owner has confirmed its complete attribution:

   ```bash
   docker compose cp ~/spec-003-provenance.json api:/tmp/spec-003-provenance-reviewed.json
   docker compose exec -T api uv run --no-sync python -m navox.evaluation.connector_provenance_review \
     --digest --file /tmp/spec-003-provenance-reviewed.json
   docker compose exec -T api uv run --no-sync python -m navox.evaluation.connector_provenance_review \
     --apply --file /tmp/spec-003-provenance-reviewed.json --approve-sha256 REVIEWED_DIGEST
   ```

   Apply validates ownership, the exact reviewed record set, every source ID,
   existing source links and an unchanged database snapshot before committing.
   Failure rolls the whole transaction back. An identical approved replay is
   idempotent. A content-free audit receipt stores the digest and record count.
   If history changed while reviewing, export a fresh file under a new path.

4. Re-run the read-only deletion inventory. Review its remaining blockers.
   Disconnect and Delete learned data remain separate, explicit owner actions
   in Connected Apps. A successful attribution reports `deleted_records: 0`.
