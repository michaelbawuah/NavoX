# SPEC-003: institution-approved Canvas reads

## User flow and deployment prerequisites

Connections → Browse → Connect Canvas → choose school and read permissions → explicitly
approve source processing → authorize on the institution's Canvas website →
identifiers-only workflow → shared Connector Runtime → existing SPEC-002 → Today.

Canvas's OAuth documentation requires multi-user applications to use OAuth;
there is no personal-token or account-password collection form. An institution
administrator must enable an endpoint-scoped OAuth developer key. NavoX accepts
multiple operator-reviewed schools through `CANVAS_OAUTH_DEPLOYMENTS`, a JSON
array of at most 50 entries. Each entry has a stable lowercase `id`, public
`name`, exact HTTPS `origin`, numeric `client_id`, and protected
`client_secret`. Example shape, with placeholder values only:

```json
[{"id":"example-school","name":"Example School","origin":"https://canvas.example.edu","client_id":"12345","client_secret":"from-secret-store"}]
```

Configure this JSON in the deployment secret store. An institution-scoped key
works only at that school; an Instructure-issued global key can be used at
multiple reviewed origins only after each institution enables it. Students
choose from the approved list; they cannot submit an arbitrary Canvas URL or
personal token. The older single-school fields remain a fallback when no
multi-school catalog is configured:

- `CANVAS_BASE_URL`: exact public HTTPS institution origin, port 443.
- `CANVAS_OAUTH_CLIENT_ID` and protected `CANVAS_OAUTH_CLIENT_SECRET`.
- `CANVAS_OAUTH_REDIRECT_URI`: registered callback ending in
  `/api/v1/connectors/canvas-lms/callback`. Production uses HTTPS. Local HTTP is
  restricted to localhost/127.0.0.1 in development/test.
- Existing Connector Secret Broker encryption configuration.

The catalogue stays `setup_pending` until configuration and encryption are
available. This is configuration readiness, not proof the institution approved
its developer key or that a live account has successfully connected.
Unknown schools remain unavailable until NavoX obtains an approved key and
adds their exact origin. NavoX does not require students at other schools to
use Cornell's Canvas account.

Only selected GET scopes are requested. Courses are required to enumerate
student enrollments; submission state requires assignment permission. The
supported developer-key scopes are:

```text
url:GET|/api/v1/courses
url:GET|/api/v1/courses/:course_id/assignments
url:GET|/api/v1/announcements
url:GET|/api/v1/calendar_events
```

Submission state uses the current user's assignment `include[]=submission`;
that include is absent unless explicitly selected. Grades and unrelated raw
provider fields are not forwarded. No assignment-submission or resource-write
route is implemented. OAuth token POSTs are solely credential acquisition.

## OAuth and credential boundaries

One-use random state is hashed and bound to the authenticated owner, workspace,
ten-minute expiry, selected capabilities and deployment fingerprint. The
fingerprint includes the chosen school's stable ID for multi-school entries.
callback atomically consumes it before exchanging the code. A replay, wrong
owner/workspace, expired attempt or changed deployment cannot exchange tokens.
Membership is checked again after OAuth I/O before storing anything.

Only the renewable token is stored, through the existing tenant-bound encrypted
Secret Broker. Access tokens are short-lived operation-local values. Refresh and
read use expiring handles; current membership, configuration and grants are
checked before refresh and again before the resource request. The runtime's
existing post-I/O authorization and generation fence remains the acceptance
boundary. Reflected credential material in provider JSON is rejected before
normalization/model processing. Models receive no credential handles.

Reconnect must authenticate the same Canvas account and cannot add permissions.
It also cannot switch schools. Refresh and sync resolve the existing connection's
origin and fingerprint; removed or rotated keys fail closed until reauthorization.
Credential replacement fences in-flight attempts. A paused connection remains
paused; neither reconnect nor connection resume overrides the global agent pause.
Existing connection IDs, provenance and source receipts are retained.

## Reusable outbound transport

The new approved-origin transport requires HTTPS on port 443 and rejects
userinfo, credential query parameters, unsafe paths, redirects and ambient
proxies/cookies. Every request resolves the approved hostname, rejects the whole
answer if any address is private/reserved/loopback/link-local or an unsupported
IPv6 translation/tunnel address, and connects to a validated numeric address.
Host and TLS SNI/certificate hostname verification retain the approved hostname.
A subsequent DNS change cannot redirect the current TCP connection to an
unchecked address; subsequent requests are resolved and validated again.

Responses have a 2 MB bound and a fixed timeout. Compression is not negotiated;
a provider ignoring identity encoding is rejected before decompression. Errors
are fixed, source-free diagnostics; provider response bodies do not become error
messages. REST onboarding can reuse this transport, but is not enabled here.

## Scans, normalization and limitations

The managed adapter has a separate versioned OAuth contract (`canvas-lms` 1.1.0).
The earlier raw-token adapter is not registered as the managed runtime path.
Each operation reads one provider page; cursors carry only phase, course IDs,
index, bounded pagination URL and a fixed scan date, never source bodies/tokens.
Pagination cannot change the origin, endpoint or selected permission/filter
parameters. The shared runtime checkpoints only accepted pages. Security-rejected
runs require an explicit new request; retryable outages follow persisted backoff.

Reads cover active student courses, published assignments, selected submission
state, announcements, and calendar context (30 days back / 180 days forward).
A scan is limited to 100 courses, 1,000 pages and the existing resource budget.
This is bounded full reconciliation, not a claimed Canvas incremental sync token.
Missing resources are not automatically interpreted as deleted. Source revisions
use provider update/submission timestamps; absent revision timestamps use a stable
epoch marker rather than falsely making unchanged data new on every scan.

Source bodies are transient and the resource registry is locator-only. Normalized
text/evidence enters the unchanged SPEC-002 extraction/resolution path. Direct
source links remain same-origin and credential-free. No Canvas-specific logic is
added to Commitment, Attention or Today.

**Submission status is source context, not a new automatic-completion rule.**
The existing conservative engine does not equate any external completion text
with user completion. The connector does not forge Gmail SENT provenance to
force a task closed. Broader provider-neutral lifecycle semantics and live-model
quality remain separate validation work. Calendar recurrence expansion is the
provider's responsibility; no local recurrence engine is added.

## Verification

Run the existing full API and pinned web gates. New tests exercise the real
OAuth API, state persistence, encrypted broker, pinned HTTP transport, common
runtime, source normalization, extraction validation, resolution and Today.
Only DNS/provider/model responses and workflow dispatch are fixtures. Repeat
`test_canvas_managed.py` on disposable PostgreSQL with
`NAVOX_CONNECTOR_TEST_DSN`; its fixtures use isolated schemas.

Security cases cover wrong owner/workspace, reflected credentials, permission
subsets, one-use state, revoked membership during OAuth/model/refresh I/O,
reconnect account binding, pause preservation, prohibited provider redirects,
DNS rebinding, mixed public/private DNS responses and bounded provider bodies.
Passing fixture tests does not establish live institution access, live-model
accuracy, full TLS interoperability or full SPEC-003 certification.

## Primary references

- https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth
- https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints
- https://developerdocs.instructure.com/services/canvas/oauth2/file.developer_keys
- https://developerdocs.instructure.com/services/canvas/resources/courses
- https://developerdocs.instructure.com/services/canvas/resources/assignments
- https://www.encode.io/httpcore/extensions/#sni_hostname
