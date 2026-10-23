# Vox milestone 1 engineering checkpoint — 2026-10-23

This is a code-review checkpoint. It does not pass milestone 1, change production, or replace physical-phone acceptance. No paid provider requests were made for this verification. Default activation flags remain disabled.

## Completed verification

| Check | Result | What this establishes |
| --- | --- | --- |
| Focused frontend suites | 427 passed across 21 files | Synthetic call, media, identity, actual wake SDK wiring, reconnect, allowance notice and operator report behavior |
| Web production build | Passed | Next.js application builds; no deployment implied |
| Web TypeScript and scoped Biome | Passed | Changed web files typecheck and format cleanly |
| Backend full `mypy navox` | Passed | Complete API package (440 source files) passes configured type checks |
| Backend runtime Ruff | Passed | Changed runtime, API, settings and migrations pass lint |
| Earlier broad Vox/timetable/AI-settings integration | 409 passed, 51 explicitly skipped | Broad regression check before final targeted hardening; skipped PostgreSQL cases are not passes |
| Initial usage ledger/migration PostgreSQL-configured suite | 26 passed, zero skipped | Real reservation locking, accounting, exact retry identity, local periods, Alembic 0052→0054→0052→0054, and unsafe populated downgrade refusal |
| Media lifecycle PostgreSQL suite | 27 passed, zero skipped | Actual database authority/lease races and cleanup fencing |
| Queue/auth/media reconciliation PostgreSQL suite | 43 passed, zero skipped | Actual concurrent consumption, ownership/replay fences and independent expiry cleanup |
| Worker lock PostgreSQL suite | 3 passed, zero skipped | Exclusive worker ownership, exception release and terminated lock-session fencing |
| Usage endpoint authentication review | 62 passed, zero skipped | Browser-only authenticated reads, operator/account isolation, revoked and expired sessions, and existing adjacent routes |
| Private wake configuration API | 44 focused cases passed plus the separately added extension-cookie regression | Connection-bound browser-only delivery, lifecycle fences, default-off licensing approval, no lease renewal/provider calls |
| Earlier inference and existing worker suite | 51 passed | Persistent inference/worker regression check before credit batching |
| Final affected runtime suite | 89 passed, zero skipped | Current inference/worker/accounting APIs, burst and duplex activity, exact credit/checkpoint binding |
| Voice-credit hook run | 12 passed: 10 PostgreSQL-fixture + 2 pure unit cases | Bounded accounting, exact-cap handling, uncertainty and activity normalization |
| Independent credit/migration run | 17 passed: 16 PostgreSQL + 1 SQLite case | Quota races, policy serialization, stale proof denial, authority, period boundaries, actual 0055 catalog parity and guarded down/up |
| Historical media migration regression | 6 PostgreSQL cases passed, zero skipped | Fixes the CI fixture dependency order without CASCADE or weakening migration guards |
| Operator report focused suites | 27 passed | Student-local dates, pagination, unknown costs and account/session clearing |
| Independent React review | No blocking findings | Inert mount, stable dependencies, cleanup, owner fencing, bounded reads, semantic controls and status accessibility |
| Changed-source credential-pattern check | No findings | Common secret formats absent from changed text; this is a heuristic, not a guarantee |

Suites overlap; their counts must not be added as a distinct-test total. Dedicated PostgreSQL checks establish only their named behavior, not all skips in the earlier integration suite. Tests use isolated synthetic PostgreSQL data, never production student data. All owned temporary database containers/networks and private test credential files were removed after verification. One earlier run exhausted its synthetic database tmpfs during fixture setup; the fresh-resource rerun passed all 43 affected cases, with no skipped tests.

## Mobile browser emulation

Eight isolated desktop-Chrome mobile emulation scenarios passed at 390×844 and 320×844, using synthetic account/report fixtures only. Document widths matched 390/320px; report card widths were 358/288px. Report controls were 44px tall, the table scrolled within its card, date refresh worked by touch, missing cost remained Unknown with a known subtotal, and a 403 hid the operator card. Controls and the last report row could be scrolled/hit-tested above the floating dock; no bottom-spacing patch was needed.

A separate local-only presentation case enabled only `NEXT_PUBLIC_NAVOX_STUDENT_VOX_ENABLED`; media and capture qualification stayed false, and no server flags changed. Zero microphone requests were made. This checks the unavailable Vox entry only, not a call. Mobile center-tab placement and duplicate-entry removal remain milestone 2 work, and preexisting smaller Settings controls remain unpassed redesign items. No live audio, real billing, physical phone, licensed wake activation, or Safari behavior was exercised. Owned test servers, Chrome processes/profiles were removed, port 3127 was released, and generated Nextdev imports restored.

Receipt SHA-256: `8ae4eb59e13b222d4362165118600f219f0dbaf9e839310ef84bf1b8d377b232`. Subsequent plain-language copy cleanup and minimum-13px report text corrections leave behavior unchanged; those final copy/CSS edits passed their scoped checks. Internal synthetic captures are not a phone recording or completed milestone deliverable.

## GitHub checkpoint CI

At commit `090578c`, static quality, dependency security, evaluation/hardening, web quality, extension quality and five API shards passed. API shard 0 passed 920 tests and failed six historical media-migration cases during fixture setup because the newer wake queue still referenced the media table being removed. The historical fixture now explicitly removes the newer dependent wake table first. All six affected cases passed on PostgreSQL without CASCADE or changes to migration guards. A new full GitHub CI run is required after pushing these changes; the full CI gate is not yet passed.

## Not established

- A real LiveKit/OpenAI call, live fallback, naturalness or accurate billed usage.
- Physical-phone wake, barge-in, no-upload silence, microphone release, echo/Bluetooth or mobile network recovery.
- True provider-cap rollover with continuing audio and no repeated greeting.
- Conversation recovery after terminal worker/room replacement or outages past the live authority lease; long-context compaction.
- Frame-level accounting throughput/latency at realistic call load.
- A deployed test URL or any student/cohort usage target. Student targets are **Waiting on real usage**.

See [vox-m1-acceptance.md](vox-m1-acceptance.md) for the user's acceptance items and unresolved work.

## Scope and visible differences

This checkpoint keeps the existing shell and call controls. The requested cost report adds an operator-only Settings card using the existing card palette and 1.25rem spacing, wrapping controls at least 44px tall, and a keyboard-scrollable horizontal table. Normal students receive no report card. Noa artwork/states and the shell redesign remain in the accepted later milestones. The call dialog or minimized dock can grow vertically to show a single allowance notice; the existing button styles, positions and tap areas are preserved. Standby copy now distinguishes muted, local wake listening, setup, and unavailable states. An encrypted wake question is described as queued, never as already answered.

Local voice/upload and browser behavior still require measurement; no synthetic assertion is labeled a phone pass. No requested specification has been waived. Future providers with different wire protocols require an adapter; OpenAI-compatible self-hosted routes can use operator configuration without rebuilding Vox UI.
