# Apple/iCloud calendar provider foundation

Status: provider implemented and synthetic protocol checks passed. This is not a
connected-account flow, calendar-writing feature or live iCloud qualification.

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

## Required connection integration

Do not create an OAuth attempt for an app-specific-password connection. Current
StudentCalendarGrant and StudentCalendarWriteGrant require an OAuth attempt FK;
an actual credential-origin authorization method needs a schema change or a
separate Apple purpose-consent entity under StudentCalendarState. A future
connection must:

- use the current web cookie, exact account header, configured Origin, generation,
  revision, fresh owner lock and idempotent request identifier;
- disclose Apple app-specific credential access honestly and obtain separate
  busy-read and scheduling choices; the provider credential is not OAuth-scoped;
- encrypt the credential immediately, bind it to the exact owner/connection, and
  never place it in URLs, return views, audit text, exceptions or logs;
- verify discovery/read access before marking connected, then recheck native
  authority before committing the connection;
- revoke and remove only the matching native purpose/credential on disconnect,
  fencing in-flight reads and reset generations;
- feed complete/partial snapshots into the shared availability calculation.

Scheduling additionally needs an approved single writable target, exact reviewed
event content, fresh conflict checks, a durable single-attempt receipt and
verified completion. Conditional CalDAV ETag operations and exact-event Undo
must be implemented independently. This provider has no PUT, DELETE, invitation,
event-creation or credential-validation endpoint.

## Validation

54 synthetic mock-wire checks cover discovery, all-calendar reads, duration and
multi-period free-busy, clipping, completeness failures, malformed XML/iCalendar,
host/redirect restrictions, body/inventory limits, cancellation and authority
revocation/timeout around I/O. Ruff and scoped mypy pass. No actual Apple
credential or external calendar was used.

References:
- Apple app-specific passwords: https://support.apple.com/en-us/102654
- CalDAV discovery and free-busy semantics: https://www.rfc-editor.org/rfc/rfc4791.html
