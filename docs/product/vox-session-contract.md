# Vox server reservations

This increment adds durable lifecycle metadata before live audio. It does not
connect LiveKit, start a microphone, retain conversation text, issue media
credentials or qualify an AI route. Every response explicitly says
`media_enabled: false`. The existing Vox transport stays unavailable.

`STUDENT_VOX_ENABLED` defaults to false. The browser ownership flag remains
separate and also defaults to false. Enabling either does not enable the other
or grant access to a provider.

## Ownership and lifetime

A reservation belongs to the authenticated user, personal workspace and exact
web `UserSession`. A new login by the same user cannot adopt a prior session's
reservation. Cookie tokens, hashes and authentication-session IDs stay on the
server. `X-NavoX-Account` narrows the cookie's authority to the account the page
expected; it does not authenticate the caller. Bearer credentials are rejected
by this web-only surface, including requests that also carry a valid cookie.

Each owner/workspace has at most one reserved call, enforced by the shared User
lock and a partial unique database index. The client call UUID is a correlation
key. The server allocates its own stable reservation UUID; a retry or renewal
does not allocate another one. No room or join credential is issued yet.

A reservation lease lasts at most 120 seconds and never outlives its authenticating
session. Renewal keeps the same server reservation. A missed lease becomes
terminal when observed and cannot be renewed or recreated by its old command.
This is an admission lease, not a qualified background-call behavior or a fixed
conversation-length limit. Future media grants must have their own checked
connection epochs, revocation and provider cleanup.

## HTTP contract

All paths are under `/api/v1/vox/calls`. Responses, including handled failures,
use `Cache-Control: private, no-store`. Mutations require exactly one matching
Origin and exactly one expected-account header. JSON is limited to 1,024 bytes
and a ten-second read; duplicate keys and extra fields are rejected without
echoing their contents.

| Method and path | Body | Result |
| --- | --- | --- |
| `GET /context` | None | Current admission generation and this login's reserved call, if any. An empty read creates no state. |
| `POST /` | `client_call_id`, `generation`, required `mode: "off-record"` | Allocate one reservation or return its exact still-live retry. Does not extend a retried start's lease. |
| `POST /{client_call_id}/renew` | `generation` | Renew that same live reservation for the same login and generation. |
| `POST /{client_call_id}/end` | `generation` | Idempotent terminal cleanup; remains available with Vox disabled or the assistant paused. |

`generation` is an immutable call-lifetime/admission epoch, not a mutable renewal
revision. Start and renewal require a fresh epoch and an unpaused assistant.
Ending an existing call checks its own immutable epoch, so concurrent renewal
cannot make Hang up stale. An old end command cannot terminate a later call
that deliberately reuses a client UUID after metadata cleanup.

Unknown, foreign and other-login reservations cannot be adopted. Terminal
reservation retries return an ended error rather than an obsolete live result.
Start with a fresh UUID and freshly checked generation after a prior reservation
ends. This surface accepts only off-record reservation mode while conversation
storage is unavailable; it creates no transcript or memory policy.

## Revocation and replay fences

All reservation operations acquire the fresh User lock, then reread the exact
authentication session and the database wall clock. A session that expires or
is revoked while waiting cannot pass using an older ORM identity or an earlier
dependency check. Reset and logout serialize with this same boundary.

Hang up can arrive before a delayed Start. An opaque terminal row or admission
epoch fence cancels that pending UUID. Ending an unknown second UUID does not
change another live call's epoch. Terminal transitions advance admission state,
so pruning an old row cannot make the original request valid again.

Successful login or registration replaces only its presented old web cookie:
that session and its reservations are revoked in the same transaction. Failed
authentication or registration does not revoke it. Unrelated logins remain
valid. Existing User rows are locked in sorted order during cross-account
replacement, before the fresh authentication-session read.

Logout closes the presented session's reservations without requiring a provider,
encryption key, active membership, enabled Vox flag or unpaused assistant.
Student-data reset deletes all owned reservation rows and advances a retained
generation fence in its existing transaction. A retried reset does not advance
that fence again. This hook covers Vox metadata; it does not complete erasure of
legacy explicit memory or either assistant-history store.

## Bounds and remaining qualification

At most 200 reservation/tombstone rows are admitted per owner/workspace. Terminal
metadata becomes eligible for pruning after 24 hours and is pruned on the next
owned operation; no timed cleanup worker is introduced here. Ending an existing
call remains possible at the quota. Unknown cancellation at capacity uses the
admission fence or the existing live-call barrier, without adding rows. Reset
erases the rows immediately; only opaque owner generation state survives.

The stored fields contain identifiers, lifecycle state and timestamps. They
contain no utterances, summaries, audio, email, schedule context, model payloads,
provider secrets or cookie hashes. The operational metadata is private and is
not conversation history.

Before live calls: qualify server-controlled room/token issuance and connection
revocation, wire the browser transport and call UI, enforce capture/playback
leases at the actual media boundary, verify interruptions/reconnect on supported
devices, and complete disclosed encrypted history and shared erasure before
retaining ordinary conversation text. A reservation response must never be
treated as a successful `VoxTransport.connect()`.
