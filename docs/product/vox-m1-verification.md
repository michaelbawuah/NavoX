# Vox milestone 1 engineering checkpoint — 2026-10-23

This is a code-review checkpoint. It does not pass milestone 1, change production, or replace physical-phone acceptance. No paid provider requests were made for this verification. Default activation flags remain disabled.

## Completed verification

| Check | Result | What this establishes |
| --- | --- | --- |
| Focused frontend suites | 372 passed across 16 files | Synthetic call, media, identity, wake lifecycle, reconnect and allowance notice behavior |
| Web production build | Passed | Next.js application builds; no deployment implied |
| Web TypeScript and scoped Biome | Passed | Changed web files typecheck and format cleanly |
| Backend full `mypy navox` | Passed | Complete API package passes configured type checks |
| Backend runtime Ruff | Passed | Changed runtime, API, settings and migrations pass lint |
| Earlier broad Vox/timetable/AI-settings integration | 409 passed, 51 explicitly skipped | Broad regression check before final targeted hardening; skipped PostgreSQL cases are not passes |
| Final usage ledger/migration PostgreSQL suite | 26 passed, zero skipped | Real reservation locking, accounting, exact retry identity, local periods, Alembic 0052→0054→0052→0054, and unsafe populated downgrade refusal |
| Media lifecycle PostgreSQL suite | 27 passed, zero skipped | Actual database authority/lease races and cleanup fencing |
| Queue/auth/media reconciliation PostgreSQL suite | 43 passed, zero skipped | Actual concurrent consumption, ownership/replay fences and independent expiry cleanup |
| Worker lock PostgreSQL suite | 3 passed, zero skipped | Exclusive worker ownership, exception release and terminated lock-session fencing |
| Usage endpoint authentication review | 62 passed, zero skipped | Browser-only authenticated reads, operator/account isolation, revoked and expired sessions, and existing adjacent routes |
| Independent React review | No blocking findings | Inert mount, stable dependencies, cleanup, owner fencing, bounded reads, semantic controls and status accessibility |
| Changed-source credential-pattern check | No findings | Common secret formats absent from changed text; this is a heuristic, not a guarantee |

Suites overlap; their counts must not be added as a distinct-test total. Dedicated PostgreSQL checks establish only their named behavior, not all skips in the earlier integration suite. Tests use isolated synthetic PostgreSQL data, never production student data. All owned temporary database containers/networks and private test credential files were removed after verification. One earlier run exhausted its synthetic database tmpfs during fixture setup; the fresh-resource rerun passed all 43 affected cases, with no skipped tests.

## Not established

- A real LiveKit/OpenAI call, live fallback, naturalness or accurate billed usage.
- Physical-phone wake, barge-in, no-upload silence, microphone release, echo/Bluetooth or mobile network recovery.
- True provider-cap rollover with continuing audio and no repeated greeting.
- Conversation recovery after terminal worker/room replacement or outages past the live authority lease; long-context compaction.
- Frame-level accounting throughput/latency at realistic call load.
- A deployed test URL or any student/cohort usage target. Student targets are **Waiting on real usage**.

See [vox-m1-acceptance.md](vox-m1-acceptance.md) for the user's acceptance items and unresolved work.

## Scope and visible differences

This checkpoint keeps the existing shell and call controls. Noa artwork/states and the shell redesign remain in the accepted later milestones. The call dialog or minimized dock can grow vertically to show a single allowance notice; the existing button styles, positions and tap areas are preserved. Standby copy now distinguishes muted, local wake listening, setup, and unavailable states. An encrypted wake question is described as queued, never as already answered.

Local voice/upload and browser behavior still require measurement; no synthetic assertion is labeled a phone pass. No requested specification has been waived. Future providers with different wire protocols require an adapter; OpenAI-compatible self-hosted routes can use operator configuration without rebuilding Vox UI.
