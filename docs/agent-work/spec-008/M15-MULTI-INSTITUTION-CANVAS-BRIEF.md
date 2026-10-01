# SPEC-008 M15 — multi-institution personal Canvas connection

Baseline: `3ba6083` on `spec-008-navoxbot`, draft PR #22. The owner rejected a
Cornell-only Canvas deployment: students at other schools must be able to select
their own Canvas institution. Keep this personal-account flow; do not build Team
NavoX. Preserve the existing Google Calendar/Canvas ClassMeeting reconciliation.

## Architecture contract

Canvas developer keys are scoped to a Canvas root account, unless Instructure
issues a global key and an institution enables it. NavoX must use an
operator-reviewed institution catalog. Each catalog entry contains a stable
public ID, display name, exact HTTPS Canvas origin, OAuth client ID and protected
client secret. A global key can be registered for multiple reviewed origins
when available. Never accept a caller-provided OAuth target, arbitrary user URL,
personal access token or Canvas password. Unknown schools remain unavailable
until an approved key/origin is registered; the UI should explain that plainly.

Use a bounded `CANVAS_OAUTH_DEPLOYMENTS` JSON setting (at most 50 entries) while
retaining the existing single-institution environment fields as a legacy
fallback. Reject duplicate IDs/origins, malformed entries, insecure origins and
missing credentials. Do not expose or log client secrets. Preserve the existing
approved HTTPS transport, DNS/IP checks, redirect refusal, credential broker,
selected read scopes and post-I/O authorization. Do not add a schema migration if
the existing authorization attempt fingerprint and connection config can bind
the selected deployment safely.

`GET /connectors/canvas-lms/setup` returns only public configured institutions
(`id`, `name`, `origin`), supported read permissions and read-only status. Keep
legacy `origin` compatibility when exactly one institution is configured.
`POST /connect` accepts an institution ID and selected capabilities, never an
origin. For a single legacy entry, omission may retain existing behavior; when
multiple entries exist, an explicit selection is required. The server resolves
the deployment and stores only its fingerprint in one-use OAuth state.

The callback resolves the exact deployment from that saved fingerprint before
token exchange, consumes state once, and rechecks owner, workspace, membership,
deployment fingerprint and selected grants afterward. Reconnection resolves
from the owned existing connection and cannot switch institution, Canvas account
or increase permissions. Sync/refresh selects the deployment bound to that
connection's origin and fingerprint; removal/rotation fails closed and requires
reauthorization. Existing connection IDs include origin, so duplicate Canvas
numeric user IDs at different schools must remain distinct.

The Web Canvas panel presents a school selector from the public catalog,
displays the selected origin and consent/read permissions, and validates the
returned authorization URL against that selected origin. Reauthorization uses
the owned connection's configured origin and the public catalog, not a global
singleton. The user signs in on the institution's Canvas page.

## Worker scope and acceptance

The Flash worker owns Canvas-specific backend/API/settings, Web selector and
reauthorization code, relevant tests, `.env.example`, the Canvas architecture
doc, and a unique M15 worker report. It must preserve unrelated changes and
the untracked SPEC-006/007 directories. It is not alone in the codebase: the
root owns the planner v9 checkpoint and local SPEC-005 speech rollout; do not
revert those edits. Do not commit, push, change real `.env`, publish/deploy,
invoke live school OAuth, or spend provider credits.

Prove with focused API and Web tests: two reviewed schools, explicit selection,
public-only catalog, unknown/duplicate/rebound institution rejection, wrong
school or replayed callback, removed/rotated key, same-school/account reconnect,
no permission growth, refresh from the owning deployment, duplicate numeric
Canvas IDs staying separate, and hostile URL/redirect cases. Existing single
school tests must continue to pass. Run relevant lint, strict typing, focused
tests and Web lint/typecheck/test/build. Report exact checks and retained live
institution-key limitations. The root will review security and run the full
pre-push/hosted CI gates after integration.

Official Canvas references: https://developerdocs.instructure.com/services/canvas/oauth2/file.developer_keys
and https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth.
