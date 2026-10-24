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

At commit `090578c`, static quality, dependency security, evaluation/hardening, web quality, extension quality and five API shards passed. API shard 0 passed 920 tests and failed six historical media-migration cases during fixture setup because the newer wake queue still referenced the media table being removed. The historical fixture now explicitly removes the newer dependent wake table first. All six affected cases passed on PostgreSQL without CASCADE or changes to migration guards. The follow-up full GitHub run 40014508943 for commit `b32e1a1` passed every job, including all API shards, API quality and Compose integration. This qualifies that earlier checkpoint only; the subsequent continuity changes require their own CI run.

## Not established

- A real LiveKit/OpenAI call, live fallback, naturalness or accurate billed usage.
- Physical-phone wake, barge-in, no-upload silence, microphone release, echo/Bluetooth or mobile network recovery.
- True provider-cap rollover with continuing audio and no repeated greeting.
- Real provider/device qualification of the implemented terminal worker/room replacement path; outages past the live authority lease remain terminal. Long-context compaction remains unimplemented.
- Frame-level accounting throughput/latency at realistic call load.
- A deployed test URL or any student/cohort usage target. Student targets are **Waiting on real usage**.

See [vox-m1-acceptance.md](vox-m1-acceptance.md) for the user's acceptance items and unresolved work.

## Scope and visible differences

This checkpoint keeps the existing shell and call controls. The requested cost report adds an operator-only Settings card using the existing card palette and 1.25rem spacing, wrapping controls at least 44px tall, and a keyboard-scrollable horizontal table. Normal students receive no report card. Noa artwork/states and the shell redesign remain in the accepted later milestones. The call dialog or minimized dock can grow vertically to show a single allowance notice; the existing button styles, positions and tap areas are preserved. Standby copy now distinguishes muted, local wake listening, setup, and unavailable states. An encrypted wake question is described as queued, never as already answered.

Local voice/upload and browser behavior still require measurement; no synthetic assertion is labeled a phone pass. No requested specification has been waived. Future providers with different wire protocols require an adapter; OpenAI-compatible self-hosted routes can use operator configuration without rebuilding Vox UI.


## Conversation recovery checkpoint — 2026-10-23

The following checks cover the new encrypted checkpoint and browser replacement path; they do not pass a physical-phone or live-provider acceptance item.

| Check | Result | Evidence and limits |
| --- | --- | --- |
| Browser call/media/admission/reservation suites | 245 passed across 5 files | Terminal loss during SDK reconnect, standby/owner/hang-up fences, bounded cleanup wait, no retry of unknown side effects |
| Web typecheck and production build | Passed | No UI layout/assets changed by this checkpoint |
| Recovery/worker-lock/readiness targeted run | 30 passed, zero skipped (29 PostgreSQL-backed plus 1 codec-only test) | Restores completed turns across processes/rooms, revision races, physical backend death, expiry, corruption/config mismatch, Forget versus write, rollback after final authority check |
| Additional source erasure checks | 2 PostgreSQL cases passed | Task/course erasure clears context; rejected revision leaves it intact |
| Wider PostgreSQL-configured API run | 121 passed, 1 outdated expected-status assertion failed | Cleanup now returns explicit pre-allocation 425; that assertion was corrected and passed in the targeted run above |
| Source/privacy regression suites on Linux | 170 passed, 3 skipped | Owner memory, student email/API, coursework and feeds. Skips: real PDF case collected before Poppler installation; two feed cases requiring PostgreSQL. These skips are not passed |
| Planner/timetable/saved-Board regression suites | 121 passed, 2 skipped | Local regression checks; PostgreSQL-only cases remain for full CI |
| Full backend quality | Ruff/format passed; mypy 442 source files passed | Current checkpoint source |
| Actual Alembic PostgreSQL migration chain | Passed | Empty isolated database → head 0056 → 0055 → head 0056; separate test verifies nonempty downgrade refusal and catalog/FK parity |

The first Mac source regression attempt failed in Linux-only calendar parsing and missing fixed-path Poppler dependencies. Re-running inside Linux with Poppler resolved those environment failures; no production parser safeguards were weakened. No real credentials were printed or provider calls made. Dedicated test containers and their synthetic database/DSN were removed after verification.

Behavior added: temporary encrypted completed-turn state expires with the active renewable call, rather than surviving as saved memory/history. Source erasure conservatively ends the entire active call so retained provider RAM cannot repeat forgotten data. This is intentional; per-turn source provenance is not yet available. No control spacing or visual-design deviations were introduced.

Remaining: real worker/network/device qualification, last-turn crash window, long-context compaction, licensed wake/onset checks, deployment and live usage/cost reconciliation. No test link or phone recording is claimed by this checkpoint.


## Live browser checkpoint — 2026-10-23/24

This dated section supersedes earlier deployment/credential/live-call absence
statements in this historical record. M1 is still **in progress**, and all phone
acceptance remains pending. See the current [acceptance table](vox-m1-acceptance.md)
and [operator runbook](vox-m1-runbook.md) for exact evidence and limitations.

- Live primary calls completed two questions with correct follow-up context.
- The measured 980 ms sentence gap is retained with a configurable 1,200 ms
  end-of-turn setting; it originally split at the old 600 ms boundary.
- A six-second browser network disconnection recovered through fresh media
  admission; the unrepeated follow-up correctly used restored call context.
- Interruption occurred during output after 6,382 ms of worker playout; the new
  arithmetic question was answered correctly.
- A saved QA timetable query returned the expected course, time and room. Test
  data was cleaned up using the normal student API.
- End/close returned HTTP 204 and the UI showed Call ended. Model usage and
  known cost were logged; transport cost is still unknown.
- Focused backend regression run: 73 passed. Frontend controller/LiveKit/controls:
  177 passed; web typecheck passed. Counts overlap older test records and must
  not be summed as a distinct-test total. Full current CI is tracked in PR #56.

The test site uses a separate database and dedicated provider projects. Public
registration is blocked. No production student data was copied. Private operator
files and test logins remain outside Git. Real phone recordings, licensed local
wake, long-duration provider rollover/context compaction, invoice reconciliation,
and student/cohort usage targets remain outstanding.


Automatic fallback was also exercised by refusing only the primary transport in
the isolated configuration. A real browser call completed STT → text → speech
with correct first/follow-up arithmetic answers and settled 937 tokens with no
remaining reservations. Known model cost was USD 0.0018700; transport remained
unknown. Configuration was restored byte-for-byte. Earlier variability in
follow-up accuracy and provider latency remains a corpus/device qualification
concern; no broad quality or latency target is marked passed.


Final cleanup/read checks returned zero active calls, zero retained active-call
checkpoints, zero unfinished cleanup jobs, and zero LiveKit Cloud rooms or
participants in the dedicated test project. The operator daily endpoint returned
HTTP 200 and two scoped student rows. The QA row for 2026-10-24 recorded 2,875
tokens and 199,777 ms connected; its 18,832 ms speaking plus 180,945 ms idle
balanced exactly. Known model cost was USD 0.0059424, with the total explicitly
unknown. These are test-call figures, not production student usage.

Both production and isolated test roots returned HTTP 200 from the server.
The original primary route was restored. Private Mac credential/login files
retained mode 0600, and a common-secret-pattern scan of the branch diff found no
matches (a heuristic, not a guarantee). No physical-phone pass is inferred.
