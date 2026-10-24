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
- Real provider/device qualification of the implemented terminal worker/room replacement path; outages past the live authority lease remain terminal. Long-context compaction was pending at this earlier checkpoint; see the later implementation and live evidence below.
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


### Test-link access correction

The original temporary hostname failed TLS on the connected Mac. The replacement
https://vox-test.66-175-235-169.nip.io/ passes HTTPS without certificate bypass,
reports a secure browser context, and permits authenticated sign-in from that
Mac. The isolated web bundle and API configuration now use the replacement
origin. The private login file was updated without changing its credentials.
Physical-phone testing remains pending.

GitHub Actions run 40043920683 passed all jobs at commit 039c8ab, including all
six API test shards, web quality, static checks, dependency security, evaluation
and hardening, extension quality, and Compose integration.

The replacement-origin mobile-emulation check on the connected Mac also received
nonzero remote speech energy (35,436 inbound audio bytes), reported no page errors
and no API requests to the old origin, and completed both hang-up/close requests
with HTTP 204. Production and test roots returned HTTP 200; public registration
on the private test deployment remains HTTP 403. No certificate checks were
disabled. This is browser emulation, not physical-phone acceptance.


### Speech continuity correction

A user reported broken, scratchy speech in Safari on the replacement test site.
The worker queued only 20 ms of output while fresh authority and usage checks ran
before every <=20 ms frame. A local probe using the actual LiveKit AudioSource,
a two-second synthetic tone, and an 80 ms proof delay every 25 checks reproduced
four underruns totaling 263.04 ms (largest 71.05 ms). At 200 ms queue capacity,
the identical probe delivered all 96,000 output samples with zero underruns.

The output queue is now bounded at 200 ms. This adds at most 180 ms of queue
capacity; it is not a mandatory startup delay. Every frame still needs fresh
proof and committed usage credit. Interruption clears queued speech immediately.
Played-audio accounting now excludes samples still waiting for submission, so
interrupted context does not include the unplayed remainder of a provider chunk.
Content-free per-turn diagnostics report output frames, source starvation and
proof timing without logging audio, transcript text, or credentials.

69 focused backend tests passed. New deterministic tests reproduce the old
starvation, preserve exact resampled waveform samples across chunk boundaries,
bound the new queue, and verify interruption/revocation cleanup. Scoped lint and
mypy passed. Live deployment and Safari listening confirmation are still pending.


The first live long-response check after the queue increase received audio and
hung up cleanly, but did NOT pass continuity: 966 output frames had 21 source
starvations totaling 1,224 ms, with a 269 ms maximum authorization/metering wait.
The source was deliberately drained by voice-credit settlement roughly once per
second; a larger queue alone could not fix this recurring pause.

The worker now settles observed usage and reserves replacement credit in one
transaction while carrying the short queued tail into that replacement grant.
No queued credit is refunded without its replacement committing. Long input
bursts and day/month boundaries retain the existing bounded draining behavior.
Renewal failures roll back settlement; uncertain commit acknowledgements stop
media and retain conservative quota holds. New tests cover continuous buffered
speech without deliberate drains or double billing, replacement refusal, and a
lost commit acknowledgement. The 18 focused audio/credit/worker-usage tests and
scoped lint/type checks pass. All 53 PostgreSQL credit, usage, concurrency,
migration and authorization tests pass. Live continuity verification is pending.


After deploying ce2cdcf, the same longer live synthetic-prompt scenario completed
986 output frames with **zero source starvations / zero starvation milliseconds**;
maximum combined authorization/metering time fell from 269 ms to 76 ms. The Mac
browser received 218,685 audio bytes with nonzero energy, zero lost packets, no
page errors, no API requests to the wrong origin, and HTTP 204 for hang-up/close.
WebRTC reported 444 concealed samples (about 9.25 ms); that transport measurement
is separate from the zero source-buffer starvation result. This is a successful
Chromium mobile-emulation check, not proof of flawless physical Safari playback.
The user's Safari listening retest is still required.


The live interruption retest confirmed speech interruption after 6,087 ms of
actual submitted-minus-queued output. The interrupted segment (315 frames) and
replacement response (169 frames) both reported zero source starvations. The
browser received audio and completed hang-up/close with HTTP 204 and no page
errors. One network packet was lost and 857 samples were concealed; physical
Safari/device/network audio quality remains a separate listening acceptance item.


### Public web lookup addition (requested after voice retest)

The user confirmed that the scratchy output was gone. This confirms that specific
listening issue; it does not pass every M1 physical-phone acceptance item.

- [x] Shared read-only public search tool wired to realtime and fallback paths.
- [x] Require completed live lookup and valid source citations.
- [x] Fail without guessing on unavailable/uncited responses.
- [x] Show clickable sources only from the admitted agent/current owned call.
- [x] Clear results at hang-up/account change; no transcript retention change.
- [x] Reserve and log search request/token usage; combined cost explicitly unknown.
- [x] 75 focused backend and 189 focused frontend tests; scoped lint/type checks.
- [x] Live deployment and synthetic spoken El Clasico lookup on both voice paths.
- [ ] Physical-phone lookup/listening test (user).
- [ ] Search-inclusive cost reconciliation.

UI addition: a bounded, scrollable source panel inside the existing call controls,
with source-link tap areas at least 44 px high. No mascot or audio-playout changes.
Persistent memory remains a separate, unfinished milestone.


Live web lookup evidence (runtime 26683ba, 2026-10-24 UTC): the synthetic
question asked for the next Real Madrid / Barcelona El Clasico and requested
the current official schedule. The realtime call displayed an FC Barcelona
official fixture citation and received 269,743 inbound audio bytes with nonzero
energy. The forced cascade call displayed the same official article and received
172,883 inbound audio bytes with nonzero energy. Both completed hang-up/close
with HTTP 204, cleared their source panel, and had no page errors or wrong-origin
API requests. The 390 x 844 mobile layout had no horizontal overflow and source
links measured 44 px high. The final screenshot was inspected for legibility.
This verifies the browser/data/audio flow; physical-phone listening remains open.

An earlier cascade lookup introduced a wrong UTC conversion. Search requests no
longer receive the student's timezone; both search and speech instructions now
quote source-stated times without deriving offsets/local conversions. The final
answers quote the source's own timezone label. Deterministic local-time conversion
is not implemented by this search feature. Source labels can themselves be
inconsistent; the citation remains available for inspection. Provider emphasis
is safely rendered as React text/strong elements without changing citation offsets.

The final primary/cascade search streams completed with 8,839 / 13,109 tokens
respectively. Their dollar estimates remain null (unknown), not zero. No reset
of existing quota was performed. After the checks, realtime mode was restored
with search enabled; active calls, saved call checkpoints, unfinished cleanup,
LiveKit rooms and participants all measured zero.

Local verification remains 75 backend and 189 frontend focused tests passed,
with the affected UI tests rerun after formatting changes, plus scoped lint,
type checking and successful deployment builds. Full GitHub CI is tracked in
the draft PR and is not represented here as a completed pass. Production and
main are unchanged; the test site is https://vox-test.66-175-235-169.nip.io/.


### Long calls and crash recovery — 2026-10-24 UTC

Implementation commits: `1b90e60` (bounded context compaction and quiet provider
replacement), `c094b96` (deferred offline track removal), and `3656673` (owned
microphone handoff). Test deployment only;
main and production are unchanged. Picovoice approval is an external pending
item and does not block these checks, as explicitly directed by Michael.

- [x] 64 focused tests passed against isolated PostgreSQL, covering encrypted
  summary checkpoint restore, stale revisions, worker ownership and terminal purge.
- [x] Nine compaction tests cover concurrent new turns, retained corrections,
  malformed/oversized summaries, late results after close and speech during warmup.
- [x] Real-provider probe reduced 13,701 UTF-8 history bytes to 2,905, kept the
  recent turns unchanged and retained the corrected synthetic room/project fact
  after restoring a checkpoint and replacing the realtime socket. Provider audio
  output was 103,200 bytes. Its synthetic metering hook counted 2,365 summary and
  860 realtime tokens; this probe was not a student-ledger billing test.
- [x] Live mobile-emulated call with an accelerated 60-second renewal retained the
  earlier fact, produced a correct follow-up without a repeated greeting, stayed
  on one media admission, received 70,019 audio bytes with nonzero energy and
  returned HTTP 204 for cleanup. The normal 3,300-second setting was restored.
- [x] Forced isolated worker SIGKILL/restart after a completed checkpoint recovered
  through a second media admission in the same conversation. The follow-up used
  the earlier fact without repeating it in the question, with no repeated
  greeting. Audio energy was nonzero; cleanup returned 204 and no page errors.
- [x] 246 focused frontend tests passed, plus TypeScript and scoped Biome checks.
  Offline signalling is deferred while local capture stops synchronously. Full
  room teardown can supersede deferred per-track removal only when SDK disconnect
  and server lease cleanup succeed. Expiry/logout gates and uncertain-cleanup
  quarantine remain in force.

The accelerated renewal follow-up measured two source-buffer gaps totalling
45 ms (124 output frames; max proof latency 229 ms). The crash-recovery segments
measured zero gaps (129/111 frames). This does not close physical listening or
long-call audio quality acceptance, nor establish the cause of the two gaps.
The output/audio-buffer implementation was not changed in this work.

A 20-second offline browser test initially failed. Browser-only diagnostics
identified a LiveKit NegotiationError during microphone-track removal while
signalling was disconnected. The fix stops raw/processed capture immediately,
waits for browser connectivity plus the SDK's connected state before negotiating,
and cancels that wait when whole-room cleanup takes ownership. No raw speech,
account credentials, SDK error body or provider token was logged.

Open: actual provider-cap duration, physical-phone recording/network changes,
licensed wake/no-upload/onset checks, broader summary/correction corpus, last-turn
crash-before-commit window, complete cost reconciliation and real student usage.
Active-call summaries do not add cross-call memory; that remains milestone 4.
No UI/spec design substitution was introduced.


The first deferred-removal retest stayed alive but lost a prompt spoken during a
brief ready/reconnect transition. A diagnostic retest with a ten-second pause
recovered via a second admission and retained context, but that delay was not
accepted as the fix. The worker was still treating a new microphone track as an
unauthorized overlap after the old one had been unpublished. It now releases the
exact retired publication on unsubscribe/unpublish, drops its queued input and
accepts one fresh microphone from the same authenticated participant/epoch. Late
old retirement events cannot clear the replacement; simultaneous tracks and
foreign/changed ownership still fail. Muting the microphone does not cancel the
assistant's output. Eight handoff regression cases plus existing input/output,
compaction and inference checks passed (54 focused tests); lint/mypy also passed.


Final runtime checks (API/worker `3656673`, web `c094b96`):

- [x] Twenty seconds fully offline in Chromium mobile emulation, followed by an
  immediate spoken question when the dialog reports ready. One media admission
  throughout; two completed turns; earlier room/project facts retained; no repeated
  greeting; 70,445 inbound audio bytes with nonzero energy. Both output segments
  had zero source-buffer gaps (134/136 frames, max proof 77/87 ms).
- [x] Microphone mute/unmute followed immediately by the same unrepeated question.
  One media admission; correct retained facts; 61,909 inbound audio bytes with
  nonzero energy. Zero source-buffer gaps (131/116 frames, max proof 66/75 ms).
- [x] Both calls ended with HTTP 204 for call/media cleanup and no page errors.
  Their usage records contained 1,935 / 1,944 tokens and 64,875 / 43,669 ms of
  connected time respectively. Speaking plus idle exactly matched connected time;
  each final call had zero reserved tokens, voice or uncertain voice credit.
- [x] Final cleanup: zero active calls, checkpoints, unfinished media cleanup,
  test Cloud rooms and participants. Realtime/search remain enabled, licensed
  wake disabled, normal 3,300-second rollover restored.

The daily QA total still conservatively retains 9,778 reserved tokens and 1,000 ms
of uncertain voice credit from earlier interrupted/crashed checks. These were not
reset or represented as settled. Complete dollar totals remain unknown because
transport/search costs and invoice reconciliation are incomplete; configured
known-component estimates are not actual billed amounts. Phone and long-duration
acceptance remain open. Full GitHub CI runs separately from the focused checks.


### Live budgets and call restart — 2026-10-24 UTC

Implementation: `c838100` (stable pre-allocation allowance response, safe retry,
post-cleanup availability refresh) and `2870af6` (terminal track-removal race).
All services on the isolated test deployment run `2870af6`. No main/production
change, credential disclosure, quota reset, UI layout or audio-buffer change.

- [x] 298 focused frontend tests and 32 PostgreSQL-backed budget/readiness/usage
  tests passed for the budget change. Four API cases use actual service-created
  usage to establish daily/monthly tokens/voice limits, assert the stable HTTP
  429 body/private response headers and zero media/provision-intent/cleanup rows
  or provider calls, then successfully admit after policy restoration.
- [x] 242 focused frontend tests passed after the quick-hangup fix, including
  capture, controller, provider, controls and 84 transport tests. This overlaps
  the earlier suite and must not be summed as distinct tests. TypeScript and
  scoped Biome checks passed; deployment builds succeeded.
- [x] Final 390 x 844 Chromium emulation: a real paid two-turn conversation showed
  the server's 80-percent daily-token notice, retained the synthetic context,
  received 62,911 audio bytes with nonzero energy, had no page errors, and ended
  with HTTP 204 for call/media cleanup. Ledger: 1,936 tokens; 13,160 ms speaking,
  28,044 ms idle, 41,204 ms connected; zero outstanding call reservations.
- [x] Daily tokens, monthly tokens, daily voice minutes and monthly voice minutes
  each returned HTTP 429 with `vox_allowance_exhausted_before_media`. No new
  HTTP-200 media admission occurred while blocked. Each showed the explicit
  allowance/typed-chat message, and each admitted successfully on the same page
  after the original allowance was restored. Each subsequent quick hang-up was
  clean; no reload was needed.
- [x] Lowering daily tokens to already-consumed usage during a live call gated
  the next speech and replacement admission, then showed the allowance-ended
  message. This does not revoke an already-issued voice credit retroactively;
  credit expiry and per-frame authorization remain in force.
- [x] Separate three-cycle rapid-connect/hang-up test passed: all six end/close
  responses were 204, every captured microphone track ended, every peer closed,
  no page errors, and the next call remained available without reload.
- [x] Original policy was restored exactly with revision checking. Verified
  defaults: 60/600 voice minutes, 300,000/3,000,000 tokens. Temporary private
  policy backup was removed. Final checks found zero active calls, checkpoints,
  unfinished cleanup, Cloud rooms and participants. Existing conservative daily
  uncertainty was not reset.

Failures were investigated rather than counted as passes. The first paid-call
check found stale UI availability after asynchronous cleanup. After that fix, a
rapid hang-up immediately after connection exposed competing microphone-removal
and SDK-disconnect operations. A regression reproduced the failure before the
fix. Physical capture still stops synchronously; a one-microtask scheduling yield
lets terminal room shutdown supersede a not-yet-started per-track negotiation.
Late publication uses the same retirement path. Actual in-call removal errors,
failed disconnects and server cleanup failures still quarantine the adapter.

The first matrix runner also had an unobserved response-wait rejection that
terminated before its finally block. The private policy backup was used to verify
exactly the expected monthly-token edit/revision, restore it and check no live
calls remained. The runner now observes response waits and clicks together, and
the final matrix completed its restoration successfully. No student counters
were fabricated or reset to make these tests pass.

The short denied/startup calls recorded no worker speech/token usage. That is not
proof of zero LiveKit transport usage or cost. Complete provider/Cloud accounting
and invoice reconciliation remain open; the measured paid-call ledger above is
not a claim of complete billing coverage. Real phone/network recording, approved
wake assets/onset/no-upload checks, actual provider-cap duration, larger correction
corpus and student usage gates remain open.

Full GitHub CI run 40056292946 passed at `c0e981a`, including Compose integration.
The `c838100` run was superseded/cancelled by the next code push. The current
revision's CI is tracked separately in PR #56; local focused passes and live test
results do not imply that full current CI or physical-phone acceptance has passed.
