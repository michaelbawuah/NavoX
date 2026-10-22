# Student product delivery plan

This plan turns the requirements in [the student roadmap](student-roadmap.md)
into reviewable releases. The roadmap remains the source of truth for scope,
precedence and acceptance thresholds. Source implementation, automated checks,
device qualification and student validation are separate completion states.

The sequence follows the Update 1 priority: persistent Vox calls first, with
calendar availability and conversation memory prepared in parallel. Board work
follows its eight-sprint order and required real-phone demonstrations. Dates
depend on measured results rather than assuming that an engineering week meets
its acceptance gate.

## Release checkpoint — October 22, 2026

- The last verified production release is PR 38 head
  `ad24f96110115947c076e5ce481e09283319be2f`, deployed October 19 with schema
  `0048_vox_calls`. Home study plans, recorded outcomes, manual goals and manual
  morning/evening/week-so-far reviews are enabled. The large Home card is a
  study-plan entry, not the required large Vox entry.
- PRs 35, 36 and 37 are merged. Their source includes reviewed recovery/email
  handoffs, own-work PDF formatting, authored Board rendering/saves and the Vox
  ownership/adapter foundation. Disabled capabilities are not shipped features
  merely because their code is present in the deployed source.
- PR 39's bounded reservation renewal is merged but not deployed in that release.
  PR 40 head `143d11eee39a8c973238705768a42c698f2cabe0` adds media epochs,
  durable cleanup intents and dependency security updates. Its verified CI
  result is success on all six protected checks in run `39819823810`; it merged
  as `151df7ec03657c70e0564e1fbbb9888ffd10fb16`. No PR 40 deployment is claimed.
  Running API/worker images must be rebuilt and qualified to receive a source
  dependency patch.
- The next working increment connects provider admission, the voice worker and
  AudioWorklet capture. These changes are in progress and not live-qualified.
  Production Vox, generated lessons, complete multi-calendar availability and
  conversation-derived memory remain unfinished. No student gate is met by
  source or synthetic tests.

## Ranked completion slices

This is one ordered release queue using the roadmap's existing requirement IDs.
Source work can run in parallel; deployment and student validation retain their
own evidence. **Source** means the complete bounded behavior exists;
**verified** means its recorded automated/end-to-end checks pass; **deployed**
means that exact behavior is enabled and postchecked on the site;
**student validated** means the specified consented cohort gate is met.
Partial source is identified below rather than promoted to a completed stage.

| Rank | Student outcome and requirements | Current evidence | Remaining completion acceptance |
| --- | --- | --- | --- |
| 0 | A reliable current site — ARC-03, ARC-05–06 | PR 38 release deployed and postchecked; PR 40 source/CI checkpoint above | Qualify exact source, migrations and rebuilt API/worker images; pass required CI, authenticated browser flows, backup/rollback and production postchecks. |
| 1 | Tap Vox and talk off the record — VOI-01–06, VOI-10 | Partial verified controller/reservation/adapter source; provider/worker/capture integration in progress; not deployed | Actual supported-device mic → RTC → approved speech/model gateway → audible answer; independent mute/speaker, navigation, interruption and hang-up/logout/reset cleanup; no retained text in this first off-record slice. |
| 2 | Keep the call useful — VOI-03–04, VOI-07–11 | Synthetic standby, renewal and reconnect primitives; no long-call/device qualification | Exact session renewal/reconnect, no audio leaving during standby, local wake/tap resume, supported noise/Bluetooth behavior and measured first audio <800 ms median / interruption stop <200 ms. Native background entries remain explicitly separate. |
| 3 | Connect calendars and find free time — STU-27–28, CAL-01–03, CAL-05, AVL-01–03 | Read-only primary Google foundation and verified pure interval core; no complete availability endpoint | Separate provider permissions, all authorized calendars and fresh source coverage; routine/classes/shifts/travel/buffers respected; honest unknowns and two feasible options. Qualify each provider separately. |
| 4 | Approve a calendar event — STU-14, STU-29, CAL-04, AVL-04 | Generic reviewed-action foundation; personal-calendar writes missing | Exact contextual read-back/yes, selected write target, fresh conflict check, verified creation, uncertain-outcome deduplication and exact Undo. |
| 5 | Useful, controllable memory — STU-30, MEM-01–06, ARC-04 | Explicit user-saved memory only; ordinary conversation history/derivation missing | Disclosure/retention choice, encrypted history, source-linked summaries/facts/threads, authorized recall, correction/expiry/shared erasure; off-record text neither saved nor learned. |
| 6 | Act on coursework through one assistant — STU-12–17, LMS-01–06, ARC-01 | Substantial source, a mock-transport model-selector corpus and historical isolated checks; capabilities disabled | Qualified registered tool selection/chaining with bounded budgets and clarification; actual routed browser upload/feed preview/update/remove/search, recovery/email handoff and PDF download; refresh cadence, deletion races and exact source/model scopes. |
| 7 | Turn a question into a Board lesson — STU-31, TUT-01–10, TUT-21–22, TUT-28–31 | Five authored lessons, renderer and encrypted saves verified in source; generated lessons missing | Owned generated-step contract, four starter/home cards, full 100-question run: ≥95% valid scripts, ≥95% correct final answers, 100% KaTeX; median first checked step <2 s; actual-phone rendering. |
| 8 | Solve and talk through a lesson — TUT-11–12, TUT-16, TUT-25, TUT-29 | Authored quick checks only; interactive verified steps and Vox sync missing | Numbered step cards, correct/wrong/unknown checking with method, ≥95% check decisions; exact interruption/resume and drawing/word sync within 150 ms on supported devices. |
| 9 | Ask about slides and photographed work — TUT-13, TUT-23–24, TUT-30–31 | Existing course uploads do not implement the new page pipeline | Thirty permitted files/ninety page questions; explicit page first 100%, descriptive top-three ≥90%; first page <10 s / fifty pages <60 s; forty actual work photos meet checking thresholds; complete derivative deletion. |
| 10 | Personalized practice and exam prep — TUT-14–18, TUT-22, TUT-26–27 | Ten synthetic profile fixtures; generated topic evidence/home ranking/exam prep missing | Correct four-card ranking, editable evidence-linked topics, confirmed scope/diagnostic, feasible reviewed sessions, original illustrations and their phone checks. |
| 11 | Follow through each day — STU-18–20, STU-22–24 | Manual goals/day reviews deployed; full automatic coaching/delivery/attendance incomplete | Actual notification delivery and controls; grounded automatic check-ins, optional attendance/manual corrections and deletion; no invented progress. |
| 12 | Native and campus expansion — STU-21, STU-25–26, VOI-09, LMS-07 | Later source/integration/device work | Real OS alarms/background permissions, reviewed campus/degree sources and school-approved API access. A browser reminder is not a native alarm. |

Student-validation gates remain separate: class accuracy with at least twenty
students, setup completion, plan completion, draft usefulness and four-week
retention require real consenting cohorts and recorded denominators. No outreach
is sent by this plan. The roadmap's Board corpora, phone demonstrations and
voice/cost measures remain required evidence; synthetic fixtures cannot satisfy
them.

## Working efficiently

Keep one frozen release candidate and at most two independent feature increments
in active implementation. Give each increment one owner and a complete user
outcome; agree on shared API contracts before parallel edits. The integration
owner handles shared files, reviews the complete flow and deploys the qualified
release while the next source increment proceeds.

Run focused checks during edits, then the full required check set once on the
integrated candidate. Repeat a check for a new change, failure or unresolved
concern. Required CI now runs six isolated API test shards and verifies that
their combined evidence covers every collected test without failures or skips.
Retain successful checks when retrying an infrastructure failure. Package
installation has bounded network and step timeouts so an unavailable mirror
cannot silently consume the runner's full job allowance.

Prepare the next independent source slice while a pinned candidate runs CI or
release qualification. Build and deploy from exact revisions, preserving required
checks and production guards. Historical API CI took roughly eighty minutes;
report the new critical-path duration only after a complete measured run.
Parallel work is not evidence that CI or a user flow completed faster.

An external credential or device gate blocks that capability's enablement, not
independent source work. Keep it visible in the ranked queue with the exact
missing evidence. A milestone closes with the enabled flow's production
postchecks; merged code and disabled controls retain their separate status.

Update this checkpoint once per qualified release and keep remaining gates in
this queue. Reuse existing code and requirement IDs rather than add overlapping
plans, permission systems or test suites. The roadmap's optional cuts remain
illustrations 7–12, then exam scheduling while retaining readiness, then Word
while retaining PDF/PowerPoint; a cut changes the visible supported scope.

## Parallel source work

| Lane | Next bounded increment | What qualifies the complete feature |
| --- | --- | --- |
| Vox lifecycle | A root-mounted owner hosts the call dialog/dock, bounded renewable native reservation client and gated LiveKit adapter across client routes. Session checks suspend authority; auth/reset mutations terminate old calls before I/O. PR 40 adds media epochs and durable cleanup intents. Provider/worker integration and AudioWorklet capture are working changes. | Actual Cloud issuance/revocation, a qualified named voice worker and speech route, supported browser/device capture/playback and navigation evidence. No live call is enabled by this source work. |
| Calendar availability | A deterministic interval engine using confirmed waking hours, owned fixed commitments, authorized calendar coverage, buffers and disclosed movable study conflicts. Incomplete inputs produce provisional results. | Calendar-purpose authorization, all connected calendars read, fresh coverage, correct local-time free slots and honest unknowns; separately reviewed writes to the selected calendar with exact retry verification and Undo. |
| Conversation memory | A versioned disclosure/retention contract, encrypted owner-scoped history and shared lineage-aware deletion before extraction or recall. | Calls and chat use the same disclosed controls; source-linked summaries, facts and threads; correction, off-record mode, sensitive-fact agreement, expiry and deletion win over queued work and cached context. |
| Existing release checks | Complete CI and browser/device flows for the stacked drafts, including account changes, uploads/downloads, notifications and save/reset races. | Exact revision/configuration recorded; required migration rehearsal; real device outcomes; staged rollout and production postchecks. A passing source test does not substitute for device evidence. |

Availability and call-controller groundwork can proceed without external
provider calls. They are internal building blocks until connected through the
qualified account/session and provider boundaries. No new source module by
itself enables a calendar, microphone, history retention or AI route.

Historical source evidence: the original ownership increment passed 124 focused
checks: 47 controller,
34 shared-observer, 22 provider, ten actual auth/reset UI-boundary and eleven
navigation/handoff cases and the then-current 1,916-test web suite. Sign-in
recovery is bounded without terminating healthy calls merely for staying hidden;
late verification responses cannot restore expired authority. The unchanged
availability core retains its 69 passing tests and independent 500-schedule
minute-mask check. This evidence covers source and synthetic integration; actual
live transport, calendar authorization, browser routing and real-device behavior remain
open.

The historical browser increment added a call dialog and minimized dock, independent audio
controls, explicit autoplay recovery, a reservation client and a gated LiveKit
2.23 SDK adapter. Its fake SDK/Web Audio checks cover delayed joins, mute,
playback and cleanup races. Captions and the primary large Home entry remain
open. The current working capture uses AudioWorklet and remains unqualified on a
physical device. PR 39 adds native reservation renewal; PR 40 adds immutable
media deadlines rather than extending a connection epoch with that renewal. See
the [Vox contract](vox-session-contract.md) for the activation boundaries. The
then-integrated web suite passed 2,034 tests across 123 files; repository lint,
type checks and the assistant migration contract passed. The recorded runtime
suite passed 542 tests with 14 integration cases skipped without its test
database; all eight extension checks passed. A passing
source check does not qualify a speech route or an actual device.

## Release order and exit criteria

1. **Close the current foundation release.** Finish current CI; verify the actual
   Next routes, account changes, recovery/email handoffs, goals, PDF downloads
   and saved Boards in supported browsers. Record microphone and notification
   delivery on supported devices. Keep each capability's remaining evidence visible.
2. **Complete the Vox call foundation.** The app-scoped owner boundary is mounted
   behind its default-off flag. Native content-free reservations now bind to the exact login
   and share reset/logout revocation; see the [server contract](vox-session-contract.md).
   The call screen, separate controls, minimize and SDK adapter are implemented
   behind unavailable admission/capture gates. Next implement exact media grants,
   provider issuance/revocation over the new connection epochs and cleanup outbox,
   plus the qualified voice worker; qualify capture/playback, captions, standby
   and reconnect on supported devices. Hang-up, sign-out, account change and reset release resources and
   invalidate pending callbacks. Do not promise native lock-screen/background
   behavior from the web app.
3. **Connect calendars and answer availability.** Add separate calendar-purpose
   permissions for Google, Outlook and supported Apple/iCloud entry paths. Read
   all authorized calendars, normalize busy intervals and expose source freshness
   before using the deterministic engine. Then add exact reviewed event writes,
   verification, conflict refresh and Undo. Other people's availability is never
   inferred from a gap in the student's calendar.
4. **Enable useful conversation memory.** Implement encrypted history and its
   controls before summaries, facts, threads and retrieval. Integrate the same
   erasure authority with student reset and both assistant stores. Qualify the
   exact provider/task/data scopes before any memory enters a model request.
5. **Complete the Board sequence below.** Use the shared Vox, memory and calendar
   boundaries rather than adding a second permission or conversation system.
6. **Qualify the student beta and release.** Exercise the complete flows with
   consenting students; record denominators, failures, device coverage, latency
   and cost. Deploy and widen access only for the behavior actually qualified.

These items share work: memory storage must be ready before ordinary Vox calls
retain text, while calendar and Board work can continue independently of live
speech. Source work proceeds in small commits and draft PRs; release claims
advance only with their stated evidence.

## Board sequence

The current authored renderer closes much of sprint 1's source scope. It does
not close the full test-corpus or real-phone gate. Before each next Board sprint,
record and share the specified two-minute demonstration on an actual phone.

| Stage | Remaining delivery | Exit evidence |
| --- | --- | --- |
| 1 — Finish the foundation | Specialist review of 100 question answers; curate 30 permitted documents and 40 real student-work photos; qualify the five authored lessons and saves on a phone. | Corpus provenance, persistence checks and five lessons without overlap on an actual phone. |
| 2 — A question becomes a lesson | Qualified lesson writer, matching Python/TypeScript validation, real checked-step streaming and Board starter cards. Generated scripts require their own owned persistence contract. | Full 100-question run: at least 95% valid scripts, 95% correct final answers and 100% KaTeX rendering; median first checked step under two seconds. |
| 3 — Work through the problem | Numbered step cards, bounded checking and Solve with me. Unknown verification remains unknown. | Correct/wrong/unknown step checks, hints and honest checking provenance. |
| 4 — Talk and interrupt | Vox entry into Board, cue-based speech/drawing synchronization and inserted answers that resume in place. | Required interruption coverage and drawing/word synchronization within 150 ms on supported devices. |
| 5 — Learn from source pages | Bounded upload, Office conversion, page extraction/indexing, explicit/descriptive page finding, citations and overlays. | All 30 files and 90 page questions; first page askable under ten seconds; 50 pages ready under 60 seconds; owner/deletion races. |
| 6 — Check photographed work | Confirmed transcription, wrong-line feedback and six original parameterized illustrations. | All 40 real-work photos meet the specified correct/wrong thresholds; illustrations update correctly on a phone. |
| 7 — Personalize practice | Evidence-linked topic states, authorized home suggestions and six further illustrations. | All ten synthetic profiles meet ordering/dismissal rules; topic correction/forgetting and all illustration checks. |
| 8 — Prepare for an exam | Confirmed scope, diagnostic, feasible practice sessions, reviewed calendar publication and night-before review. Finish export/flashcard scope, deletion, originality and product polish. | Exact approved calendar results, complete acceptance checks and required student evidence; unqualified optional capabilities stay unavailable. |

The roadmap's authorized scope cuts remain available if needed: illustrations
7–12, then exam-prep scheduling while keeping readiness, then Word support while
keeping PDF and PowerPoint. A cut changes the visible scope and report; it does
not remove privacy, mathematical honesty or exact action approval.

## Memory implementation decisions

- Require an explicit 30-day, 90-day or never-auto-delete choice before ordinary
  conversation retention starts; no option is preselected. Existing accounts
  receive the new disclosure before the changed behavior activates.
- Off-record sessions neither save nor learn from their text. Raw audio is
  discarded under the existing speech-processing contract.
- Derived content follows its source's erasure and expiry. A fact may outlive
  automatic transcript expiry only if the student explicitly keeps it as a
  separately visible, independently forgettable fact. Explicit source-linked
  forgetting still erases dependent facts, threads and summaries; forgetting a
  fact also removes its derivatives.
- Do not rename existing plaintext explicit memory or runtime turns as encrypted
  history. Design and verify their migration/erasure and the current runtime's
  30-day session ceiling before enabling a longer retention choice.
- Unify deletion through native owner/session authority, preserving authenticated
  ownership, encryption bindings, generation fences and derivative lineage.
  Student reset and Delete everything must cover all promised layers.

## Evidence and release rules

Every increment records its source revision, configuration, meaningful tests,
known limits and remaining gates. Keep private schedules, credentials, messages,
work photos, audio and transcripts out of repository fixtures and logs.

Provider qualification uses the exact task, prompt/schema, model revision and
personal-data scope. The existing conversation grant does not silently authorize
a lesson writer, remembered context or student files. Candidate reports are
review evidence, not activation.

Browser qualification requires the supported browser capability. A static
render, DOM test or desktop viewport does not qualify a phone. The current
browser/phone gaps do not prevent independent, bounded source work, but remain
open release gates.

Recruitment and invitations are separate actions. Do not contact beta students
without an explicit outreach instruction. The first launch gate remains at least
99% class-answer accuracy with 20 real students; subsequent plan, draft usefulness
and four-week retention gates remain as recorded in the roadmap.
