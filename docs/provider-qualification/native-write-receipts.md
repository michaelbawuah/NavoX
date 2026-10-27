# Provider-aware calendar receipts

Migration `0064_provider_calendar_writes` preserves the provider on native write
grants and event receipts. Existing rows default to Google. Google and Microsoft
grants require a real OAuth attempt; Apple consent uses the current independently
authorized Apple connection and must have no OAuth attempt. No existing busy or
mail consent becomes event-write permission through this migration.

The receipt keeps its own content-free provider identity because it can outlive
the grant, a disconnect, or a reset. Its encrypted payload and exact provider
proof still require their existing owner/session/generation boundaries. Local
study holds and unresolved external outcomes must survive those changes.

After admitting a non-Google action, retain compatible provider-aware readers
and reconciliation. Do not restore the old `f95dfc8` / `8b67620` application code
or a pre-action database over the external outcome. An additive schema alone
does not make those old readers safe. The migration's downgrade locks both
tables and refuses any non-Google receipt or grant, including revoked grants and
receipts whose grant was deleted. It never deletes such evidence to permit a
downgrade.

The schema change enables no feature flag, provider configuration, connection,
or background replay. Provider qualification and explicit write consent remain
separate gates.

Native provider integration keeps the existing Google commands and omitted
Google provider fields compatible. Microsoft and Apple setup use separate
`/outlook/scheduling/start` and `/apple/scheduling/start` routes. Targets and
event commands derive the provider from the current owned grant or saved
receipt; a request cannot override it. Apple consent binds the current busy-time
credential without fabricating an OAuth attempt or resubmitting a password.
Both new provider settings default to false, in addition to the existing global
write gate. Busy-time and mail permissions remain separate from write consent.

Apple times must have whole-second precision before a review or any local study
admission. The transport keeps the original strong PUT ETag separate from a
later GET observation. A matching event can therefore be confirmed as created
while exact Undo remains unavailable when the original version was not proven.
Unknown writes and deletions retain their native claims and are never replayed.
Calendar Undo does not undo separately saved study moves.

Microsoft OAuth can record a separate exact write-purpose consent, and bounded
same-account positive recovery can confirm a saved action. New Microsoft
creation and deletion stay unavailable even when the new setting and OAuth
scopes are present: the event endpoint's exact-version conditional deletion
contract remains unproven. An empty or ambiguous search never proves absence.

The synthetic source fixtures do not qualify real iCloud ownership, privilege,
normalization, stale-ETag, or no-invitation behavior. Those checks and secure
provider app/account setup remain prerequisites for live activation.
