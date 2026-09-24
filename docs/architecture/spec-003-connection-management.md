# SPEC-003: connection discovery and explicit management

## Delivered code boundary

This checkpoint adds the authenticated catalogue and owner-scoped connection
view, plus explicit Google linking, scoped reauthorization, Sync Now, and
pause/resume. The workspace includes a searchable, paginated Connections panel
with source-specific permission, freshness, cooldown and processing status.
It is not the full SPEC-003 completion gate.

The catalogue deliberately separates **available**, **setup pending**, and
**planned**. Google setup uses the established OAuth entry point. Canvas,
imports and configured REST have adapters but their public setup paths are
not activated by this checkpoint. MCP remains planned. Listing a connector
is not permission to call it, nor evidence that its setup is usable.

## API and privacy

- `GET /api/v1/connectors` and `GET /api/v1/connectors/{id}` expose curated,
  content-free catalogue metadata, not arbitrary provider manifest text.
- `GET /api/v1/connections` and `GET /api/v1/connections/{id}` scope both
  user and workspace. Google source/compatibility rows appear as one account
  under the original connection ID, preserving older clients and approvals.
- Reads do not instantiate adapters, decrypt credentials, contact providers,
  call models, create mirrors, or copy source content. They return neither
  credential references, cursor values, raw errors, connector configuration,
  arbitrary metadata, nor bodies/subjects from provider resources.
- A last successful source sync is separate from authorization/health checks.
  A one-hour window classifies saved sync freshness; missing or future times
  are never described as fresh. This is a UI policy, not a guarantee that the
  provider has delivered every recent change.
- The bounded overview rejects oversized collections rather than returning a
  silently incomplete list. Individual connection detail remains available.

## Explicit commands

`POST /connectors/{id}/connect` initially supports Google identity linking only.
Gmail/Calendar read access remains an explicitly consented action in Connected
understanding. Sending email retains its separate permission and exact approval.

`POST /connections/{id}/sync` delegates to the existing source dispatch path:
current ownership, scopes, agent pause, provider setup and durable cooldowns
remain authoritative. The interface says **queued**, not **synced**, after
an accepted dispatch.

`POST /connections/{id}/reauthorize` binds the original account and requests only
existing allowed scopes plus identity binding, with PKCE and expiring one-time
state. The callback rejects additional unrequested permissions and wrong accounts.
Successful authorization does not resume an explicitly paused connection.

`POST /connections/{id}/pause` and `/resume` require a JSON command UUID and
reject an untrusted Origin when present. They lock the owned connection group,
verify current membership, and write a minimal replay receipt in the audit table.
Repeating the original pause command after a later resume cannot undo that resume.
A new command UUID denotes a new explicit action.

Pause fences all currently known source attempts by advancing their generation
and closing their leases. It retains credentials, source cursors, accepted
receipts, checkpoints and learned knowledge. Rapid pause/resume does not revive
an old attempt. Resume does not clear the user's global agent pause, provider
backoff, or missing/failed authorization. Only a successful reauthorization may
clear an authentication failure. No provider operation occurs in these commands.

Pause is **not disconnect** and does not revoke a provider token. Disconnect and
Disconnect + Delete Learned Data are deliberately not exposed as working buttons.
Removing source-linked evidence and recomputing surviving knowledge is separate
unfinished work. Requests already sent to a provider cannot be undone.

## Validation requirements

Run the full API gate and all web workspace gates. The new management tests
cover authorization isolation, metadata minimization, catalogue availability,
command replay, Origin/JSON constraints, OAuth scope boundaries, pause/reauthorize
interaction and freshness. Four Gmail cases use the actual shared runtime to
pause during provider/model I/O, with and without immediate resume, and assert
that stale work cannot be accepted or advance the saved cursor.

`NAVOX_CONNECTOR_TEST_DSN` enables isolated PostgreSQL schemas for management and
Google runtime tests. Normal CI's existing PostgreSQL step now includes those
suites in addition to the original recovery suite; no existing gate is removed.

A local SQLite pass does not establish PostgreSQL locking or browser behavior.
The native web install/lint/typecheck/tests/build and real PostgreSQL rerun must
pass before publication; all six hosted CI jobs must then pass on the new SHA.
Provider fixtures are not live Google account acceptance. Do not count status
labels, adapter files or a green unit test run as full SPEC-003 certification.

## Still open in SPEC-003

Universal subscription lifecycle; certified Canvas/import/REST public setup and
end-to-end ingestion; MCP discovery and policy-controlled access; generic outbound
and storage safety; disconnect and learned-data deletion; browser-assisted
contract certification; complete unknown-service portability demonstrations.
SPEC-004 subscription intelligence is not implemented here.
