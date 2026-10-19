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

## Starting point

- The timetable and class-answer foundation is deployed as `62981c2b`.
- Planning/coursework foundations are in draft PR 34; reviewed actions,
  recovery, goals, own-work PDF export and manual day review are in draft PR 35.
  Both drafts passed their recorded protected CI checks; their device and
  student evidence is still incomplete.
- Draft PR 36 contains five authored Board lessons, validated rendering,
  progressive sketches and letter contours, checks, pen notes and encrypted
  explicit saves. Its source tree at `97429f3` is
  `961886050e0a8f94ef3249c062c828b4d0faf035`. The web suite has 1,791 passing
  tests; current-head web, extension and dependency checks pass. Full API CI
  remains running at this checkpoint.
- These foundations do not establish live Vox, generated lessons, multi-calendar
  coverage, conversation-derived memory or real-student benefit. Their default
  feature flags remain off unless a previous release explicitly enabled them.

## Parallel source work

| Lane | Next bounded increment | What qualifies the complete feature |
| --- | --- | --- |
| Vox lifecycle | A root-mounted owner boundary now hosts the transport-injected controller across client-route children behind a default-off flag. Session checks suspend authority; matched auth/reset mutations terminate old calls before I/O. | Qualified server sessions and one LiveKit room per call; actual capture/playback; browser navigation continuity; reconnect; no silent audio transmission or wake listening after hang-up; supported-device latency and interruption checks. |
| Calendar availability | A deterministic interval engine using confirmed waking hours, owned fixed commitments, authorized calendar coverage, buffers and disclosed movable study conflicts. Incomplete inputs produce provisional results. | Calendar-purpose authorization, all connected calendars read, fresh coverage, correct local-time free slots and honest unknowns; separately reviewed writes to the selected calendar with exact retry verification and Undo. |
| Conversation memory | A versioned disclosure/retention contract, encrypted owner-scoped history and shared lineage-aware deletion before extraction or recall. | Calls and chat use the same disclosed controls; source-linked summaries, facts and threads; correction, off-record mode, sensitive-fact agreement, expiry and deletion win over queued work and cached context. |
| Existing release checks | Complete CI and browser/device flows for the stacked drafts, including account changes, uploads/downloads, notifications and save/reset races. | Exact revision/configuration recorded; required migration rehearsal; real device outcomes; staged rollout and production postchecks. A passing source test does not substitute for device evidence. |

Availability and call-controller groundwork can proceed without external
provider calls. They are internal building blocks until connected through the
qualified account/session and provider boundaries. No new source module by
itself enables a calendar, microphone, history retention or AI route.

The prepared ownership increment passes 124 focused checks: 47 controller,
34 shared-observer, 22 provider, ten actual auth/reset UI-boundary and eleven
navigation/handoff cases. The complete web suite passes 1,916 tests. Sign-in
recovery is bounded without terminating healthy calls merely for staying hidden;
late verification responses cannot restore expired authority. The unchanged
availability core retains its 69 passing tests and independent 500-schedule
minute-mask check. This evidence covers source and synthetic integration; actual
transport, calendar authorization, browser routing and real-device behavior remain
open.

## Release order and exit criteria

1. **Close the current foundation release.** Finish current CI; verify the actual
   Next routes, account changes, recovery/email handoffs, goals, PDF downloads
   and saved Boards in supported browsers. Record microphone and notification
   delivery on supported devices. Keep each draft's remaining evidence visible.
2. **Complete the Vox call foundation.** The app-scoped owner boundary is mounted
   behind its default-off flag. Next connect qualified server sessions and LiveKit
   transport. Add the call screen,
   separate microphone/speaker controls, captions, minimize, standby and scoped
   reconnect. Hang-up, sign-out, account change and reset release resources and
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
