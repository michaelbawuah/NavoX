# Reviewed Google Calendar events

This increment lets a student review a single personal event, approve its exact
calendar/title/location/time/timezone, and undo only the unchanged event NavoX
created. It does not invite attendees, send meeting invitations, create repeating
events, create conferencing links, override unknown availability, or move study
blocks. Source and synthetic provider tests do not qualify a live Google account.

## Authority

Calendar scheduling uses separate `calendar-scheduling.v1` consent and separate
native grant rows. Existing availability consent never grants event writes, even
when the provider token already contains a write scope. Start from an active
owned availability connection; request account identity, calendar-list/read-busy
scopes and `calendar.events.owned`. Callback completion remains bound to the exact
login, account, purpose, reset generation and still-active busy-time grant.

Google's `owner` access role and the owned-event OAuth scope must both permit the
selected calendar. NavoX does not claim that Google's manager role implies sole
ownership of the data. Outlook/iCloud and shared calendars requiring the broader
`calendar.events` scope remain unsupported in this slice.

Every route requires a current browser cookie and exact `X-NavoX-Account`; writes
also require the single configured Origin. Bearer substitution is rejected. All
responses and validation errors use `Cache-Control: private, no-store`; invalid
input and provider/database errors do not echo private values.

The shared owner/session/reset boundary is rechecked before and after provider
requests. Native locks are committed before network I/O. The existing availability
release guard and the independent default-off `STUDENT_CALENDAR_WRITES_ENABLED`
guard remain required for admitting new operations. The frontend uses the
separate `NEXT_PUBLIC_NAVOX_STUDENT_CALENDAR_WRITES_ENABLED` guard. Enabling
availability never automatically enables scheduling. Consent withdrawal,
native activity recovery and read-only reconciliation remain available while
admission is disabled. No source change enables production calendar access.

## HTTP contract

All paths below begin `/api/v1/student/calendars`.

| Method/path | Body/result |
| --- | --- |
| GET `/scheduling` | Separate scheduling grants with calendar-state generation/revision. |
| POST `/google/scheduling/start` | `CalendarCommand` plus `consent_version`, explicit `confirmed:true`, existing `connection_id`; returns account-bound OAuth URL. |
| POST `/scheduling/{grant_id}/disconnect` | `CalendarCommand`; revokes scheduling only and preserves busy-time consent. |
| POST `/scheduling/targets` | `CalendarCommand` plus `grant_id`; freshly lists bounded owner-role targets. |
| POST `/events/preview` | `CalendarCommand` plus `grant_id`, `calendar_id`, title/location, aware start/end, IANA timezone and `AvailabilityPreview`; returns exact encrypted review. |
| GET `/events?cursor={last_owned_event_id}` | Twenty event reviews per page, held requests first; `next_cursor` is an owned opaque event UUID or null. No provider I/O. Stale cursors return 409 to restart. |
| GET `/events/{id}` | Recover one known owned receipt with fresh native generation/revisions and no provider I/O. |
| POST `/events/{id}/approve` | `CalendarCommand` plus `event_revision`, `digest`, explicit `confirmed:true`. |
| POST `/events/{id}/reconcile` | `CalendarCommand` plus `event_revision`, `digest`; GETs the one exact provider event, never inserts/deletes. |
| POST `/events/{id}/undo` | `CalendarCommand` plus `event_revision`, `digest`, explicit `confirmed:true`; conditional exact-event deletion followed by verification. |

`CalendarCommand` contains `generation`, calendar-state `revision` and UUID
`request_id`. Event views distinguish their own `revision` from global
`calendar_revision`. Replaying a preview UUID with changed fields is rejected.
Changing a draft requires a new preview and approval. The exact review expires
after ten minutes; a current login that did not create that preview cannot approve
it. A fresh login for the same owner may recover, reconcile and explicitly undo
previously submitted work under fresh same-account scheduling authority.

Titles are limited to 200 characters, locations to 500, target identifiers to
1,024, and events to one minute through eight hours. Provider inventory must be
complete within 100 targets/four pages. API bodies remain bounded to 4 KiB.

## State and outcome

A preview is `pending` and causes no provider event mutation. Preview and approval
both require fresh complete availability, a confirmed routine and an opening that
contains the entire event without moving a study block. Changes or uncertainty
return 409 for a new availability check. Native timetable/routine digests and the
60-second availability deadline are checked again immediately around admission.
Google does not expose an atomic free-busy-plus-insert operation; another actor
can change its calendar after a fresh check.

Before the single insert, an `executing` receipt is committed with a UUID-derived
Google event ID. No submitted receipt ever returns to `pending`. Request loss,
timeout, provider ambiguity or missing post-insert read-back leaves the outcome
held as `executing`/`uncertain`. A conflicting unresolved request blocks another
preview/submission for the same interval. Reconciliation never retries creation,
and absence after an uncertain insert is not proof that a delayed insert cannot
still arrive.

Each new insert or Undo admission advances the shared native calendar revision
under the owner lock. Requests that collected availability before another action
was admitted must check again, even if that earlier action already became
`created`. The admitted operation recaptures its own authority at the new
revision; a lost response recovers fresh metadata through the read-only receipt
endpoint before reconciliation. This revision fence forces a fresh review; it
does not release the separate encrypted native interval claim merely because a
fresh provider free-busy response omitted the event.

Known `created`, externally `changed`, `executing`, `uncertain` and `undoing`
events retain a native interval claim across resets. Preview and final submission
check these claims under the owner lock in addition to the provider's availability
answer. Their stored intervals receive the same ten-minute meeting buffer used
by the existing availability engine. Explicitly unknown travel uses that engine's
conservative bounded local-day uncertainty span; it is never silently treated as
zero. Nonoverlapping candidates at the buffer boundary remain eligible.

The guard reads/decrypts at most 500 owned active claims; a 501st matching-status
row rejects new scheduling with an explicit coverage-limit message before any
provider write. The activity list is bounded to 500 current-generation reviews
plus 500 retained claims and paginates twenty at a time, including older claims.
Known event claims are released only after exact-event GET confirms absence or
verified Undo completes; free-busy propagation never releases them. If an
external edit moved an event, NavoX conservatively continues to block its original
buffered interval until exact absence is established. It does not infer the new
interval or silently adopt the edited event for Undo. Exact absence can move a
sticky `changed` receipt to `undone`, but cannot restore deletion authority.

The initial insert ETag is retained when available. Exact read-back checks the
intent marker, digest, title/location, UTC instants, timezone, private/busy flags,
reminders and the absence of unapproved editable content. Only a matching event
becomes `created`; its initial verified ETag is never replaced by a new external
version. The original creation ETag and the first GET-observed ETag are separate
proofs. Undo requires both to match; a GET-only confirmation after losing the
insert response may report `created`, but offers no NavoX Undo because its
original creation version was not confirmed. A late original response that
differs from a concurrent observer's ETag makes the action sticky `changed`.
The student can review that event directly in Google Calendar. `changes_applied` is true only for `created`, false for
`pending`/`failed`/`undone`, and null for `executing`/`uncertain`/`undoing`/`changed`.

Undo first reads the exact event and requires the original ETag and approved
fields. It commits `undoing`, then sends one DELETE with the strong ETag in
`If-Match`. A 412 or external edit becomes sticky `changed` and disables Undo;
reconciliation cannot silently adopt an edited version. A timed-out delete stays
`undoing` while the event is still present and is not automatically repeated.
Only verified absence completes `undone`. Observations publish through exact
revision checks, so a stale read cannot reverse a newer Undo transition.

## Storage, reset and release limits

Drafts, target identity/label, title, location, availability choices and ETags are
stored only in an owner/action/generation-bound encrypted envelope using the
existing source-content encryption boundary. Audit events contain opaque IDs,
not draft text, calendar IDs, credentials or provider bodies. Credentials remain
in the existing vault. No AI provider sees an event draft in this increment.

Reset revokes scheduling grants, removes unsent/failed previews and advances the
retained generation fence. Submitted operational receipts remain held rather
than being erased, reset or replayed; the old generation cannot submit or undo an
action. Held receipts from a prior reset remain listed and can be reconciled
read-only under fresh consent for that same Google account; they cannot be
approved or undone from the old generation. These operational receipts are not conversation memory. The UI must not
promise that forgetting conversations deletes an uncertain external action.

Migration 0052 adds only scheduling grants/event receipts and refuses destructive
downgrade while either table contains data. It does not mutate the retained Gmail
uncertain inventory, existing Temporal workflows, worker pins or production flags.
Live provider acceptance, production signed-in qualification and supported-device
evidence remain separate before enabling this capability.

Google references: [scopes](https://developers.google.com/workspace/calendar/api/auth),
[insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert),
[calendar roles](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList),
[conditional deletion](https://developers.google.com/calendar/api/guides/version-resources).
