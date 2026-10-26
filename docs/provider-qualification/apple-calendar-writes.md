# Apple CalDAV personal-event write source

This adapter implements a narrow conditional-write protocol. Its default is
disabled. Fixture success does not qualify a live iCloud account, change native
consent or enable a deployment. The native scheduling service must provide the
current owner/session/reset/source boundary and preserve held uncertain outcomes.

The source uses the existing server-vault Apple Account and app-specific password
explicitly in memory. It does not manufacture a bearer token or request another
credential. Only the fixed iCloud CalDAV hosts accepted by the busy reader can
receive Basic authentication. Discovery redirects are bounded; object reads and
mutations never follow redirects.

Fresh authenticated discovery yields a principal, calendar home and bounded
calendar inventory. A writable target must be a VEVENT collection beneath that
home, name the exact current principal as `DAV:owner`, and grant read, bind,
unbind and write-content privileges. The standard write/all aggregations are
recognized. Missing, failed, duplicate or ambiguous properties fail closed.
An owned calendar may still have other readers; ownership does not prove sole
access to its data.

A new object has a UUID-derived resource name and UID. The only supported content
is one private, opaque VEVENT with UTC start/end, its original IANA display zone,
the native action/digest markers and `Added by NavoX` provenance. Attendees,
organizers, scheduling METHODs, alarms, recurrence, attachments and unknown
content are rejected. Creating uses exactly one `PUT` with `If-None-Match: *`.
No timeout or conflict is retried.

Creation is returned only after an exact resource GET proves the reviewed
semantics and a strong ETag. A strong PUT ETag additionally requires identical
stored bytes and version. When the server normalizes storage and omits a strong
PUT ETag, the GET representation/version supplies the pin. Undo reads the exact
marked resource again and uses only `DELETE` with the pinned strong `If-Match`.
A stale version is changed, never an unconditional deletion. The native service
must still verify the post-delete outcome.

An exact GET 404 is an observation, not permission to replay an uncertain create
or release its claim. All other missing/ambiguous recovery evidence remains
unknown. A DELETE 404 is not assumed to be confirmed deletion.

Fixture coverage includes hostile redirects, partial/duplicate discovery,
ownership and privileges, Unicode folding, both UTC DST folds, unexpected
calendar content, malformed/weak versions, create and delete races, lost native
authority, unknown outcomes, response bounds and single-attempt mutation counts.
Provider requests have an eight-second cap and share a thirty-second operation
budget. No real credentials or provider calls were used.

Live qualification still needs to confirm iCloud's actual owner/privilege
properties, preservation of markers and supported normalized content, strong GET
ETags, stale conditional DELETE refusal, conditional create conflicts, credential
revocation, and absence of invitations. Unsupported or absent properties remain
unavailable rather than being inferred from calendar-home membership.

Primary references, checked 2026-10-26:

- [RFC 4791 §§5.3.2, 5.3.4 and 6](https://www.rfc-editor.org/rfc/rfc4791.html):
  conditional creation, stored representations, strong object versions and ACLs.
- [RFC 3744 §§3 and 5](https://www.rfc-editor.org/rfc/rfc3744.html): ownership and
  effective read/bind/unbind/write-content privileges.
- [RFC 9110 §13](https://www.rfc-editor.org/rfc/rfc9110.html): strong If-Match and
  create-only If-None-Match preconditions.
- [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html): VEVENT and UTC DATE-TIME
  representation.
- [Apple app-specific passwords](https://support.apple.com/102654) and
  [third-party access](https://support.apple.com/121539): credential mechanisms;
  neither page is a live CalDAV conditional-write qualification.
