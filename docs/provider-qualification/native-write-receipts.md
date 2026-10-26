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
