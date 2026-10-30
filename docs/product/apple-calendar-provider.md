# Apple/iCloud calendar busy connection

Status: provider and native busy-purpose connection flow implemented in source
with synthetic protocol and native transaction checks. Live iCloud qualification,
calendar event writes and supported-device acceptance remain open.

## Read boundary

AppleAvailabilityProvider.collect accepts an Apple Account identifier and
app-specific password already loaded from a server credential vault. It does
not collect or store credentials. Never supply an ordinary Apple Account
password. App-specific passwords require Apple two-factor authentication and can
be revoked in the Apple Account security settings.

The caller authenticates the owner and exact session, checks the separate
calendar-availability purpose and passes an asynchronous check callback. This
callback runs immediately before and after each provider request, including
failures and bounded manual redirects. Caller revocation, cancellation and
authority-check errors propagate rather than publishing a stale snapshot.

Discovery begins only at https://caldav.icloud.com/. Returned links and redirects
must remain HTTPS on caldav.icloud.com or pNN-caldav.icloud.com partition hosts
(two or three digits), without user information, nonstandard ports, query or
fragment. Proxy environment configuration and automatic redirects are disabled.
Bodies, inventory, redirects, requests and overall time are bounded. Untrusted
XML DTD/entity input and ambiguous or failed discovery properties are rejected.

All discovered VEVENT calendar collections are queried separately using the
CalDAV free-busy-query REPORT. Recurrence, recurrence exceptions, cancelled and
transparent events, all-day events and timezone conversion are the server's
responsibility under RFC 4791. The reader never silently substitutes a partial
local recurrence expansion. Each returned VFREEBUSY must prove the exact queried
window; only aware intervals are admitted. FREE periods are ignored; other
valid busy periods conservatively block. Snapshots expose counts, instants and
completeness, with no calendar identifiers, event titles or credentials.

Unsupported or denied reports, incomplete inventory, malformed data and limits
produce partial/unavailable coverage. Existing known blockers can remain in a
partial snapshot, but the combined availability engine must not turn missing
coverage into confirmed free time. A real iCloud account may reject this REPORT;
such a result requires a separately implemented and qualified bounded fallback,
not a claim of empty calendars.

## Native connection flow

Migration 0063 adds AppleCalendarGrant under the existing StudentCalendarState.
No OAuth attempt is fabricated. The connection uses the current web cookie,
exact account header, configured Origin, generation, revision, fresh owner lock
and idempotent request identifier. The disclosed consent is busy-time use only;
an Apple app-specific credential does not provide OAuth-style scope restrictions.
Scheduling consent and event writes are not enabled by this connection.

The app-specific credential is accepted only in its generated format. Discovery
and a complete free-busy read must succeed before any credential or active grant
is persisted. Provider I/O releases native locks and rechecks the exact owner,
session and calendar revision immediately around each request. The final commit
checks them again. Failed or cancelled verification persists no credential.

Successful credentials are immediately encrypted in the existing server vault
with an envelope binding the exact owner, personal account, connection and
credential version. The entered account label is ordinary connection metadata;
it is not represented as a separately verified email identity. Exact retries do
not duplicate credentials or provider reads. Reconnecting rotates and erases the
old credential. Disconnect erases its grant and unreferenced credential and
advances the common calendar revision, including while new admission is disabled.
Reset cleanup preserves the common generation fence.

| Method/path under /api/v1/student/calendars/apple | Behavior |
| --- | --- |
| GET base path | Private generation/revision, setup availability and owned grants |
| POST /connect | Exact busy consent, Apple Account email and app-specific password |
| POST /{grant_id}/disconnect | Erase exact owned grant/credential using CalendarCommand |

Request validation and failures never echo private values. Bodies are bounded
by the common 4 KiB JSON limit. Responses use private/no-store caching. Only
opaque grant IDs enter audit events; no password, event body or provider error
body is logged or returned.

Common integration calls apple.snapshots when capturing native calendar scope,
apple.read for iCloud busy coverage and apple.forget_calendar_data during reset
before the common generation advances. The separate API router and database
model must be registered in the assembled application. The connection component
clears the password on submission and owner/privacy transitions and never stores
it in browser storage or a URL.

Scheduling additionally needs an approved single writable target, exact reviewed
event content, fresh conflict checks, a durable single-attempt receipt and
verified completion. Conditional CalDAV ETag operations and exact-event Undo
must be implemented independently. The provider has no PUT, DELETE, invitation
or event-creation operation.

## Validation

54 synthetic mock-wire checks cover discovery, all-calendar reads, duration and
multi-period free-busy, clipping, completeness failures, malformed XML/iCalendar,
host/redirect restrictions, body/inventory limits, cancellation and authority
revocation/timeout around I/O. Native transaction/API checks additionally cover
encrypted owner binding, exact retries, credential rotation, incomplete provider
coverage, generation/session changes during I/O, account isolation, disconnect,
reset, validation privacy and cancellation. Ruff and scoped mypy pass. No actual
Apple credential or external calendar was used. SQLite transaction tests are
separate from PostgreSQL concurrency and live-provider qualification.

References:
- Apple app-specific passwords: https://support.apple.com/en-us/102654
- CalDAV discovery and free-busy semantics: https://www.rfc-editor.org/rfc/rfc4791.html
