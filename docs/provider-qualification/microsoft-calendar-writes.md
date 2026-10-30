# Microsoft personal calendar transport qualification

Reviewed on 2026-10-26 against the linked Microsoft Graph v1.0 documentation.
This is source and synthetic-wire evidence. No Microsoft account was connected,
no real event was created/deleted, and no runtime write flag was changed.

## Protocol boundary

[Create event](https://learn.microsoft.com/en-us/graph/api/user-post-events?view=graph-rest-1.0)
documents delegated `Calendars.ReadWrite` for both personal and work/school
accounts, creation in a selected calendar, custom properties and `transactionId`.
The transport sends one POST only. A transaction identifier is an additional
provider correlation/deduplication aid, never permission to replay a lost request.
The native scheduler must commit its uncertain-operation receipt before POST.

New requests contain no attendees, recurrence, attachments or online meeting;
they disable reminders and responses. Their text body is the reviewed provenance
`Added by NavoX`. UTC wire times preserve the exact approved instants, including
DST folds. A version-two review marker stores the separate approved IANA display
zone. Version-one markers retain their old local-zone readback semantics and
legacy normalized provenance rather than silently becoming new receipts.

[Get single-value extended properties](https://learn.microsoft.com/en-us/graph/api/singlevaluelegacyextendedproperty-get?view=graph-rest-1.0)
documents filtered collection lookup and a resource expanded with one selected
property. Its string value filtering is case-insensitive, so the transport also
checks the returned action marker exactly. Recovery first verifies the current
account and selected owned calendar, then performs one bounded marker query and
one version-matched direct metadata read. Empty, paged, duplicate, malformed or
missing results are unknown. They do not establish that an earlier POST cannot
still complete, or that an event was deleted or moved.

[Immutable Outlook IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)
can remain stable within a mailbox, including a move, while archive/export/reimport
can change them. The transport requests immutable IDs but does not turn a missing
selected-calendar marker lookup into proof of absence. Native recovery never
automatically creates another event.

## Exact Undo remains unavailable

[Delete event](https://learn.microsoft.com/en-us/graph/api/event-delete?view=graph-rest-1.0)
documents DELETE, its authorization header and a successful 204 response. It also
warns that deleting an organizer's meeting can send cancellation messages. It
does not state an event-specific `If-Match` stale-version rejection guarantee.

[Remove-MgUserEvent](https://learn.microsoft.com/en-us/powershell/module/microsoft.graph.calendar/remove-mguserevent?view=graph-powershell-1.0)
exposes an `IfMatch` argument, and the
[generic Graph error reference](https://learn.microsoft.com/en-us/graph/errors)
describes 412. These do not establish that the event DELETE endpoint atomically
rejects a mismatched event version. A read followed by an unconditional delete
cannot close the edit/invitation race.

Accordingly, `conditional_undo_supported` stays false. `delete()` raises a
truthful unavailable result before network I/O, including with the synthetic
`enabled=True` constructor. Shared admission must not advertise Microsoft event
writes as available until this protocol/release gate is resolved. A future
qualification needs an authoritative event-specific concurrency contract and
controlled stale-version/race evidence, not a fabricated successful mock of an
undocumented condition. No source-only result here qualifies live Undo.

## Fixture coverage

The focused provider suite exercises one-POST creation and positive recovery;
new and legacy markers; exact provenance, UTC and display-zone round trips;
DST folds; malformed/ambiguous/missing receipts; no retries; changed versions and
event content; current owned-target proof; bounded inventory/response handling;
revocation before late publication; and no DELETE or foreign-account endpoint
I/O. Shared owner/session/generation, study holds and receipt admission are owned
and tested by the native scheduling integration.
