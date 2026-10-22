# Vox reservations and browser transport

The reservation surface adds durable lifecycle metadata before live audio. Its
responses explicitly say `media_enabled: false` and never issue a media grant.
Separate, disabled-by-default media routes now connect that metadata to a
LiveKit Cloud adapter and named voice worker. Source implementation does not
qualify the Cloud service, an AI route or a supported device. No conversation
text is retained by these off-record calls.

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
does not allocate another one. A reservation itself issues no room or join credential.

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
| `POST /{client_call_id}/media` | `generation`, unique `request_id` | Provision an opaque connection epoch and named worker, then confirm fresh authority before returning the bound browser token. Requires independent media/Cloud/worker qualification. |
| `POST /{client_call_id}/media/{connection_id}/renew` | `generation` | Revalidate the exact immutable media binding; does not extend its deadline or return a token. |
| `POST /{client_call_id}/media/{connection_id}/close` | `generation` | Revoke that epoch and durably queue provider cleanup, including with new admission disabled. |

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
revocation, connect the browser adapter through that authority and a qualified
voice worker, verify interruptions/reconnect on supported devices, and complete
disclosed encrypted history and shared erasure before retaining ordinary
conversation text. A reservation response must never be treated as a successful
`VoxTransport.connect()`.

## Browser implementation and admission gates

An opt-in web build mounts one call dialog/dock inside the app-owned provider.
It offers a timer, independent microphone/speaker controls, minimize/restore,
typed navigation and Hang up. Captions are visibly unavailable. No microphone,
reservation or SDK connection starts on mount. The default transport has neither
available media admission nor qualified capture, so enabling the public UI flag
alone cannot start a call or request microphone permission.

The reservation client uses authenticated, account-scoped, no-store requests
with bounded bodies and deadlines. It preserves the captured generation for
same-call retries and sends terminal cleanup even when an earlier start is
still pending. Metadata reservations never supply media credentials. The client
renews the same server reservation only while the exact call epoch and account
remain verified. Renewal is independent of microphone mute, speaker controls,
minimizing and standby. One-shot scheduling uses the remaining lease minus a
30-second margin; there is no automatic retry after a failed renewal. The
original absolute expiry and a conservative 120-second monotonic bound from
request dispatch both remain effective until a strictly later, identity-matched
renewal is checked. Response latency never adds authority, and a delayed result
cannot revive a lease that expired while the browser was throttled.

Account verification or privacy suspension synchronously aborts an in-flight
renewal, including during a pending SDK join, and retains the old expiry. A fresh
same-owner verification can resume renewal before that expiry; missing the lease
ends the call rather than silently extending it. Requests without synchronous
ownership notifications do not receive automatic renewal. Every outbound,
playback and local-wake gate also checks the native lease, independently of the
timer. Terminal cleanup keeps the original generation and closes late joins;
uncertain physical cleanup blocks replacement admission in that transport.
This metadata lease is not media authority. Future admission must integrate its
lifetime with independently qualified media renewal and server revocation.

The LiveKit 2.23 adapter loads the SDK only after explicit media admission. It
checks the supplied owner/call/room/participant binding, waits for the expected
agent, subscribes only to that agent's audio and limits outbound subscription to
it. Browser claim checks are defense in depth; they do not verify JWT signatures
or replace server authority. SDK automatic reconnect with cached credentials is
disabled. A controller retry requires a fresh admission for the same call.

The concrete capture factory processes local microphone frames with VAD and
publishes a processed track, never the raw microphone track. Quiet frames are
zeroed and idle upstream publication is paused. Explicit mute, ownership loss,
hidden-page suspension and termination synchronously retire capture. Remote
audio is separately silenced and detached; a blocked browser sound permission
offers an explicit Enable sound action without unmuting the microphone.

Capture uses a versioned same-origin AudioWorklet PCM bridge with bounded local
queues. Main-thread VAD, permission and speech acknowledgments gate processed
frames; a stalled bridge fails closed within 350 ms. Capture is unqualified by
default until supported-device acceptance. Fake SDK/Web Audio tests cover sequencing and
cleanup; they do not establish device support, naturalness, background behavior,
noise rejection, latency or speech-provider suitability. The existing bounded
single-question speech gateway is not qualified for hours-long calls.

LiveKit token expiry limits initial admission, not an already connected
participant. Before activation, implement exact server connection epochs, a
qualified named worker, expiry enforcement and durable room/participant cleanup
that survives logout/reset and delayed external operations. Cloud revocation
and room recreation need explicit reconciliation; short token expiry or room
deletion alone must not be treated as a permanent replay fence. No such server
media authority, provider worker or credentials are enabled by this increment.

Authority references: LiveKit's [tokens and grants](https://docs.livekit.io/frontends/reference/tokens-grants/),
[participant lifecycle](https://docs.livekit.io/intro/basics/rooms-participants-tracks/participants/)
and [Room SDK reference](https://docs.livekit.io/reference/client-sdk-js/classes/Room.html).

## Internal connection intents and durable cleanup

The server records an immutable, server-generated connection
UUID beneath the reservation. Each connection gets distinct opaque room,
browser participant and agent identities. These contain no user names, email,
account IDs or conversation content. A committed intent is not a room, a token,
a worker assignment or a media grant. Only the separately gated admission
route can provision and confirm it before issuing a token.

An exact request retry retains its connection and deadline. Confirmation checks
the fresh owner, exact login, reservation generation, personal membership,
pause state and database clock under the shared User lock. A reservation renewal
does not renew a media connection. Expired or revoked epochs cannot revive.
Another connection waits until the previous connection's cleanup is verified.

Cleanup locators are saved with the intent, before any external room
operation. This separate ledger deliberately has no cascading foreign keys:
reset, session removal or account deletion cannot erase the instruction to
retire a remote resource. It contains opaque locators and operational timestamps,
not retained speech, transcripts, credentials or personal workspace content.
Terminal reservation paths and pause atomically revoke media and request cleanup.
A bounded sweep also discovers expired sessions, lost membership and abandoned
connections without a new browser request. Cascade-orphaned cleanup is rescued.

Provisioning adapters report external completion through the trusted
internal finalizer even if the original browser request was canceled or its
account records were erased. A late completion reopens cleanup and invalidates
older worker claims or completion receipts; it cannot regain media authority.
The ordinary authenticated confirmation method alone cannot compensate a late
external result after logout or erasure.

The cleanup reconciler commits one short-lived claim before provider I/O. Crashed
claims can be reclaimed; an old worker cannot acknowledge a replacement claim.
Failures, timeouts and partial evidence remain pending with bounded backoff.
Completion requires a receipt for the exact epoch, attempt and three locators,
explicitly verifying both participant revocations and room absence. Room absence
alone is insufficient. A real provider adapter must establish those facts; a
callback returning the receipt is not by itself provider qualification.

The browser adapter retires its media lease on Hang up, failed joins and late
admission. Lease retirement and physical audio shutdown are independent: one
failure does not skip the remaining cleanup. An unresolved join or teardown
blocks replacement attempts, and uncertain cleanup quarantines the adapter.
The reservation wrapper remains responsible for ending the reservation.

## Provider and worker source integration

The server-only Cloud adapter uses bounded HTTPS control requests, restrictive
microphone-only JWT grants and opaque epoch metadata. Both participant identities
are explicitly revoked with the Cloud cutoff before room deletion and verified
absence. Not-found responses alone never prove token revocation. The adapter
requires a separately qualified Cloud configuration; self-hosted room removal
is not substituted for Cloud token revocation.

Admission retains trusted provisioning work through HTTP cancellation and always
finalizes late external completion against the durable cleanup ledger.

Migration `0050_vox_provision_attempts` records one globally exclusive physical
provisioning attempt per epoch before external I/O. An HTTP timeout, crash or
unknown outcome cannot be marked settled by a room-absence observation or an
assumed waiting period. Its independent evidence survives account deletion and
holds cleanup incomplete until specific provider/operator evidence resolves it.
Retries in another API replica never launch a second attempt or retire its holder.

The browser captures the already-owned reservation generation, checks the server
connection UUID in token metadata and rejects swapped or extended renewal
proofs. Its close request has an independent bounded lifetime after caller abort.

`python -m navox.vox.worker_livekit start` runs the named off-record worker.
`python -m navox.vox.worker_reconcile` runs independent durable cleanup. Cleanup
must continue after disabling new calls while outstanding entries exist. The
worker uses the existing approved Python gateway for speech and conversation;
SDK model plugins do not acquire provider authority. It does not create retained
assistant sessions or transcripts. Before allocating media, a read-only preflight
requires all three exact gateway paths to have ordinary owner grants, configured
adapters, healthy full-rollout assignments and the bounded turn budget. It does
not reserve quota, execute a prompt, prove provider connectivity or qualify voice.
The shared text-answer contract allows up to 3,000 characters; the bounded worker
rejects answers above its 600-character speech ceiling rather than silently
truncating them. Live spoken-answer qualification remains required independently
of valid RTC credentials.

Each media epoch has an immutable deadline of at most 120 seconds. Its periodic
proof checks do not extend that deadline. Continuous calls still require fresh
epoch handover and real interruption/reconnect qualification; these bounded
workers do not establish hours-long Vox behavior.

Browser media and capture qualification default off through
`NEXT_PUBLIC_NAVOX_STUDENT_VOX_MEDIA_ENABLED` and
`NEXT_PUBLIC_NAVOX_STUDENT_VOX_CAPTURE_QUALIFIED`. Server media, worker and Cloud
qualification are separate. Credentials remain server-only. Real Cloud
revocation/late-creation acceptance and verified disabled project recording/auto-egress,
actual supported-device microphone/playback,
worker teardown and complete end-to-end gateway behavior are required before
live activation. Synthetic tests do not satisfy those gates.
