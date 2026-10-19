# NavoX for students

NavoX is moving toward one job: help a student know their day, make a workable
plan, and follow through. The target student has several courses, a job or clubs,
and deadlines scattered across a timetable, learning system, and inbox. Students
at any college must be able to start with their own schedule, without waiting for
their school to approve an integration.

This is the canonical build specification for the **NavoX for Students — Product
Plan, October 17, 2026** (38 pages), amended by **NavoX Plan Update 1 — Builder
Changes, October 17, 2026** (13 pages), and extended by **NavoX Whiteboard Tutor —
Research & Build Guide, October 17, 2026** (14 pages), supplied on October 18,
2026. **Update 1 wins wherever the original plan conflicts with it.** The tutor
guide adds the learning experience specified below; it does not replace Update
1's Vox, permission or memory requirements. References labeled `Update` point to
the amendment, `Tutor` to the tutor guide, and other page references to the
original plan. Requirements and priorities describe the intended product, not a
claim that it has shipped.

The supplied amendment is `NavoX-Plan-Update-1.pdf`, SHA-256
`76f742d76c022dd80125889e88cdd4aaceacf85988d34ea7b9bf269209c9cca8`.
The supplied tutor guide is `NavoX-Whiteboard-Tutor-Guide.pdf`, SHA-256
`ecf28e86306ec7353a4a29a663f157a83f55458e21346c6ecb2f0cdc92cfa17c`.
The source PDFs are retained separately; this specification records their
builder requirements without placing private conversations or test operations
in git.

The student build is in progress. Existing connected accounts, daily priorities,
voice questions, reviewed email actions, reply watches, previewable routines and
editable saved preferences are foundations for the new experience.

## Update 1 decisions

| Change | Current product requirement | Priority / traceability |
| --- | --- | --- |
| Primary voice entry | One tap on **Vox** starts a hands-free call that stays open until hang-up. The chat-box mic remains for single questions. Push-to-talk and a wake word are no longer the main entry. | Highest / VOI-01, VOI-03–06, VOI-10–11; Update 1–5 |
| Personal calendars | Connect Google, Outlook and Apple calendars separately from coursework feeds. Read busy times across connections; write only to the chosen calendar after approval. | High / STU-27, CAL-01–05; Update 5–6 |
| Availability and scheduling | Answer when the student is free, suggest two suitable meeting slots and add the reviewed event on a contextual natural yes. | High / STU-28–29, AVL-01–04; Update 6–8 |
| Conversation memory | Keep ordinary calls and chats as text, discard raw audio and derive summaries, facts and open threads. Provide disclosure, off-the-record mode, history, correction and forgetting controls. This replaces transcripts-off-by-default as the target. | High / STU-30, MEM-01–06, ARC-04; Update 8–10 |
| Voice quality | Never ask students to rate naturalness. Use behavior metrics and separately permitted team reviews. | Medium / VOI-08, beta voice measures; Update 10–11 |

The original eight-second silence nudge and ten-second automatic call closure
are superseded by quiet idle and standby at about two minutes. A contextual
“yeah,” “of course,” “sure” or “go ahead” answers an exact action read-back;
unrelated acknowledgments do not approve an action. Raw-audio disposal, exact
action scope, academic-integrity boundaries and student launch gates still apply.

## What counts as complete

Each requirement advances through four stages:

| Stage | Evidence required |
| --- | --- |
| Planned | The behavior and acceptance criteria are agreed; implementation may be in progress. |
| Implemented | The complete behavior exists in source, including its error and permission paths. |
| Automated verified | Relevant tests and an end-to-end check of that behavior pass on the recorded revision and configuration. Synthetic fixtures are labeled. |
| Student validated | Consenting students use the feature, and the stated accuracy or usage target is met with a recorded denominator and observation window. |

Passing tests does not create student-validation evidence. A partially reusable
feature does not make a new student requirement implemented. Update the stage
only with a source revision, relevant check results, and any remaining limits.

The tables below combine the plan and its amendment. Implementation and
verification are tracked separately from student validation. Update 1 additions
remain **Planned**. Later engineering is prepared behind disabled flags; a launch
phase advances only after its preceding student gate. No student gate has been
claimed as met. Updating this document does not enable a provider, microphone,
calendar write or conversation retention for an existing account.

## Current engineering

| Increment | Source behavior | Remaining evidence |
| --- | --- | --- |
| Know the day | Any-school timetable setup; manual, calendar and reviewed local screenshot imports; clock-based class answers; private quick asks; opt-in class alerts; installable web app. Isolated PostgreSQL, migration and synthetic browser checks passed, followed by all six protected CI checks and a production postcheck for [PR 33](https://github.com/michaelbawuah/NavoX/pull/33), deployed as `62981c2b`. | Actual microphone and notification delivery on supported devices; the real-student accuracy gate. |
| Plan the day | Adjustable study blocks, source-change review, private coursework feeds, course notes/materials and scoped search. The earlier exact planning checkpoint passed 191 isolated PostgreSQL checks and migration rehearsal. All six protected CI checks passed on PR 34 head `edb6ebeb`; its API suite had 3,390 passes with no skips. Separately observed synthetic source-component browser flows covered planner progress, calendar review/update/removal, notes/search, version conflicts, native sign-out and account isolation. | Full production Next routing, browser file uploads, genuine mobile/background/focus behavior, qualified model tool selection and real-student evidence. Synthetic browser checks do not qualify a personal-calendar provider, automatic worker cadence or every course-view lifecycle. [Draft PR 34](https://github.com/michaelbawuah/NavoX/pull/34) remains unmerged. |
| Reviewed actions and recovery | Selected-email review, reviewed task handoff, catch-up previews and editable extension text. The earlier exact recovery checkpoint passed 170 isolated PostgreSQL checks, including concurrency and cold-import cases. All six protected CI checks passed on PR 35 head `f16cb9ee`; its API suite had 3,717 passes with no skips. | Browser verification of recovery/email handoffs, realtime voice, the new conversation-memory behavior and the draft usefulness gate. Corrections apply to the current email review; no learned filtering is claimed. Flags remain off. |
| Goals foundation | Up to three active goals and explicit daily yes/no or count reports; unknown progress remains unknown. The earlier exact goals checkpoint passed 94 isolated PostgreSQL checks, including five races and migration verification; the later PR 35 CI run also passed. | Goal browser flows, complete coaching and the four-week retention gate. This foundation does not infer attendance, sleep or grades; flags remain off. |
| Own-work PDF | A blank course editor formats the student's entered text and confirmed metadata. Local renderer checks, inspection of 15 sample pages, web checks and build passed. Three actual PostgreSQL publication-lock cases passed in isolated verification and the later PR 35 CI run. | Browser upload/download verification and the stated limited left-to-right text profile. This is not assignment generation; export flags remain off. |
| Manual day review | Explicit morning, evening and week-so-far reviews use saved schedules, recorded outcomes and self-reported goals. The assembled web suite/build passed; required PostgreSQL boundary cases and all protected PR 35 CI checks later passed. | Browser verification and complete coaching. Default off; no automatic coaching, rescheduling, mood intake or stored review history. |
| Model selector evaluation | The OpenAI structured-output adapter can propose only registered host tools when model metadata also permits it. A 24-case public synthetic corpus exercises real adapter wire handling through mock transport; 71 new and 160 compatibility tests pass, including exact task-scope denial. | Live synthetic evaluation, separately reviewed qualification import, exact sensitive-data grants and explicit one-question consent. Reports are review-only and cannot qualify traffic; no new provider route is activated. |
| Update 1 foundation | The shared top-bar one-off voice launcher, page-owned hands-free/VAD primitives, read-only primary Google Calendar connector, generic reviewed-action machinery and explicit saved memory can be reused. | Vox/LiveKit call lifecycle, standby/reconnect, multi-calendar busy reads and writes, Outlook/iCloud support, combined availability, conversation-derived memory and behavior analytics are new work. Existing quick asks and saved preferences do not satisfy these requirements. |
| Whiteboard tutor | **First renderer increment implemented and automatically checked:** a custom SVG board with validated hand-authored scripts, five practice lessons, the initial six action types, reading-paced play/pause, step replay, timeline, pan/zoom, temporary pen notes and two quick checks per lesson. | Source: `whiteboard-tutor.ts`, `whiteboard-lessons.ts`, `whiteboard-playback.ts`, `whiteboard-scene.tsx` and `student-whiteboard.tsx` in apps/web, with schema, renderer, reducer, DOM-interaction, account-lifecycle and route tests. The `/whiteboard` route and navigation require `NEXT_PUBLIC_NAVOX_STUDENT_WHITEBOARD_ENABLED=true`; it defaults off. It does not yet provide generated lessons, actual OCR, live Vox/narration sync, animated handwriting paths, course saving, board PDF export, flashcards or automatic topic/memory updates. Smooth playback on real phones is a separate acceptance gate; desktop or simulated tests cannot satisfy it. |

Private files and feed links are not sent to a model by these features. Course
search runs locally over the selected course's authorized text. The private
student assistant panel currently sends only the current question, with no saved
history. The separate `/navox` assistant can use bounded verified prior turns.
That describes today's implementation, **not** the amended memory target.
Update 1 requires disclosed conversation history and authorized memory retrieval
under MEM-01–06. It needs a dedicated session, retention and provider contract;
the current one-question grant must not silently expand into persistent context.
Model selection over student tool results still needs qualification and exact
sensitive-data scope before activation.

Disabling a feature does not authorize deletion. Explicit owner deletion and the
student-data reset remain available; reset also removes feed credentials and
derived study plans so a delayed refresh cannot recreate forgotten content.

## Delivery order

| Phase | Student outcome and scope | Launch gate |
| --- | --- | --- |
| 1 — Know the day | Any-school onboarding; timetable imports and reliable class lookup; alerts; installable web app. Update 1 makes the Vox call foundation the highest-priority voice work; a small chat mic remains secondary. **In progress.** | At least 99% correct class answers with 20 real test students. Recruit 20–30 students across several colleges. |
| 2 — Plan the day | Shared tool core, daily blocks and replanning, coursework feeds/workspaces, personal-calendar connections and combined availability. | At least 60% of plan blocks marked Done. |
| 3 — Act for you | Reviewed email-to-action, recovery and extension drafts; approved personal-calendar scheduling; complete Vox conversation and interruptions. Text history and memory are a high-priority cross-phase dependency, not postponed until this phase launches. | At least 70% of email drafts approved with light edits. |
| 4 — Coach | Semester goals; morning, evening, and weekly check-ins; assignment-help tools and PDF export; whiteboard tutoring under TUT-01–20; course AI policies; opt-in attendance; study, practice, and wind-down voice modes. | At least 50% of students retained after four weeks. Tutor capabilities also need their own staged checks and real-device evidence below. |
| 5 — Grow | Degree consequences, campus guide, more school profiles, native mobile app, verified public Gmail access, and school-approved learning-system APIs. | Expand only with the preceding gates and the permissions each integration requires. |

The source plan labels these roughly weeks 1–2, 3–5, 6–8, 9–11, and week 12
onward. These are planning estimates, not promised dates or substitutes for gates.

### Next engineering work under Update 1

1. Build the Vox entry, persistent call lifecycle and call controls first. Design
   session ownership, hang-up cleanup and disclosed/off-the-record storage before
   connecting a live speech provider. A renamed quick-ask button is not Vox.
2. In parallel, define personal-calendar permissions and the combined free-time
   engine, and implement text-history/memory provenance and forgetting. These are
   high-priority additions; coursework feed tests do not satisfy them.
3. Wire reviewed event writes and natural contextual approvals through the
   existing server-owned action boundary. Add LiveKit reconnect/standby and the
   native-dependent entries without advertising unsupported web behavior.
4. Instrument the specified behavior metrics as calls become available, with no
   in-app naturalness survey. Verify privacy, provider behavior, cost and real
   device results before enabling a wider rollout.
5. Build the whiteboard renderer in parallel from hand-authored practice scripts,
   following the tutor phases below. Add generation and Vox integration only
   through the same qualified provider/session boundaries; a local board is not
   evidence that a live tutor call is ready.

## Product requirements

All rows below are **Planned**. Phase 1 rows are currently being implemented; the
table does not mark unfinished work as verified.

| ID | Requirement and acceptance criteria | Phase / plan pages |
| --- | --- | --- |
| STU-01 | **Skippable setup.** Ask preferred name, school and learning system, timetable, optional connections, shifts, routine, 1–3 goals, notifications and optional attendance. Immediately after timetable, offer Google, Outlook, Apple/iCloud or Skip for personal calendars. Include the plain one-screen memory disclosure and explanation path. Steps can be revisited; skipping must not silently grant microphone, calendar or memory access. Target about three minutes; measure 80% finishing in under five minutes. | 1, extending with later features / 1–3; Update 5, 10 |
| STU-02 | **Any-school start.** An unknown school works with a student-owned timetable. School profiles carry calendar, buildings, services and learning-system type; unknown facts remain unknown. Publicly drafted profiles require review. | 1 foundation; 5 expansion / 2, 25–26 |
| STU-03 | **Plain permissions.** Explain each source's reads, writes, purpose, disconnect and deletion controls. Personal-calendar busy-time reads and approved writes are separate from read-only coursework feeds. Disclose text memory, raw-audio disposal and off-the-record controls. Gmail and attendance are optional; attendance starts off. Learning-system access never submits work. | All / 1–3, 10–11, 24–25; Update 5–6, 10 |
| STU-04 | **Manual timetable.** Save course code/name, meeting type, weekdays, start/end, building/room, instructor and term dates. Multiple meeting sections belong to one course. Review the exact values before saving; warn about class/shift overlaps. | 1 / 16–17 |
| STU-05 | **Schedule import.** Import recurring `.ics` schedules and extract screenshot/photo schedules into editable previews. Do not save uncertain extracted times or rooms without confirmation. Preserve dates, timezones, sections, locations and recurrence exceptions; refuse unsupported input honestly. | 1 / 2, 16–17 |
| STU-06 | **Timetable changes.** Edit a recurring meeting or one occurrence; change room/time, mark online, or cancel. Add/drop archives the course workspace instead of deleting its material. Resolve exceptions before answering or scheduling alerts. | 1 / 16–18 |
| STU-07 | **Trusted clock and class answers.** Server time plus a confirmed IANA timezone determines now, next, an explicit time, and a named day. Now uses start ≤ now < end. Respect term bounds, breaks, holidays and confirmed cancellations; distinguish no class from incomplete data. | 1 / 4–5, 16–18 |
| STU-08 | **Walking context.** Use reviewed building coordinates and a known starting building for travel estimates. State the estimate and unknowns. Do not imply current device location without consent. Next-class and leave-now alerts link to the correct occurrence. | 1 / 5, 16–18, 21, 25–26 |
| STU-09 | **Fixed commitments and study preferences.** Record recurring shifts, meals, sleep, focus hours, maximum study-block length (default 90 minutes), breaks and never-schedule hours. These constrain planning rather than becoming casual model suggestions. | 1 setup; 2 planning / 2, 14–16 |
| STU-10 | **Feasible daily plan.** Combine confirmed deadlines/effort, all connected personal-calendar busy time, fixed blocks and travel. Use focus hours, split large tasks and reserve 15% capacity buffer. Meeting scheduling also applies its 10-minute before/after buffers. Show what cannot fit. Calendar writes require exact approval. | 2 / 14–16; Update 6–8 |
| STU-11 | **Replanning.** Every study block has Done, Took longer and Skip. Preserve fixed classes/shifts and real outcomes. Availability can treat internal study blocks as movable, but disclose displaced blocks and the resulting plan. Moving an external calendar event still requires the student's approval. | 2 / 15–16; Update 7–8 |
| STU-12 | **Course workspace.** Keep overview, assignments, notes/files, linked emails, syllabus and course chat together. Course chat retrieves only authorized material from the selected course. Show meeting times, staff, office hours and grade breakdown when sourced. | 2 foundation; 4 learning tools / 20–21 |
| STU-13 | **Email to action.** With read permission, label Needs reply, Has deadline, FYI or Ignore; show why, link the course when confirmed, and propose deadlines for the plan. Support a student correction such as Not important. Never automatically reply to unknown senders. | 3 / 18–19 |
| STU-14 | **Exact reviewed actions.** Display recipient, subject and full body for email; read back calendar title, time, target calendar and known place. A natural yes directly answering that action question is valid approval; an unrelated acknowledgment is not. Changed payloads require a new read-back and approval. Verify completion before saying Sent or Added. Long drafts use screen review; approving an event does not approve an email send. | Existing foundation; 3 student flow / 5, 10–11, 18–20; Update 5–8 |
| STU-15 | **Fall-behind recovery.** Accept a reported absence, overdue/underestimated task, or three skipped blocks. Retrieve only actual missed material, changed assignments and sourced late/extension policies. Offer a manageable multi-day catch-up plan and an editable extension draft. Never invent reasons or documentation. | 3 / 6, 19–20 |
| STU-16 | **Assignment help.** Explain prompts/rubrics, break work into timed steps, teach concepts and similar examples, quiz, brainstorm, outline and review student drafts. Check citations/formatting and export the student's own work as a clean PDF. | 4 / 20–21 |
| STU-17 | **Academic integrity.** Tutor/outline/review/format by default. Show the course's sourced AI policy; only follow a more permissive policy when explicitly supported. Decline assistance during a disclosed timed exam. No graded ghostwriting for submission as the student's own work. | All learning flows / 1, 11, 21, 37–38 |
| STU-18 | **Installable web app.** Provide an installable mobile web experience, relevant deep links and clear unsupported-browser behavior. Installation must not silently request notification or microphone permission. | 1 / 4, 21–23, 36 |
| STU-19 | **Useful alerts.** Deliver one-glance next-class (default 15 minutes), leave-now (walk + 3 minutes), due-soon (morning + 3 hours), shift (20 minutes), plan-block, email-action, missed-class, morning and evening alerts. Each opens the right screen; next-class and morning messages also offer a Vox action. Unsupported channels are not shown as delivered. | 1 class alerts; later phases for dependent types / 21–23; Update 2 |
| STU-20 | **Notification control.** Independent type switches, quiet hours default 11 pm–7 am, daily cap default 12 and five-minute bundling. Respect timezone changes, pause, disabled types and deleted source events. Record delivery and explicit tap/Done outcomes without sensitive lock-screen detail. | 1 foundation; extend with types / 21–22 |
| STU-21 | **Wake-up alarm.** Support daily/per-day schedules, vibration/sound where supported, gradual volume, nine-minute snooze up to three times and dismissal into morning briefing. Suggest changes for early/cancelled classes, but let the student decide. A web reminder is not a reliable native alarm; use supported Clock shortcuts until native support exists. | 5 native dependency / 22–23 |
| STU-22 | **Semester goals and coach.** Save 1–3 editable academic, habit or personal goals. Ground morning encouragement in the actual day; evening celebrates real wins or resets tomorrow; weekly check-in reviews actual progress and asks how the student feels. Unknown progress needs a question, not a fabricated success. | 4 / 2, 23–24 |
| STU-23 | **Support without dependency.** Use specific, supportive language, no guilt/comparisons or diagnosis. Offer breaks and reviewed campus support. Crisis signals interrupt coaching and show verified country-appropriate help plus campus resources. Remain clear that NavoX is AI; no romantic-partner roleplay or encouragement to replace people. | All conversation; 4 coach / 7–11, 24, 37 |
| STU-24 | **Optional attendance.** Location processing stays on device and runs only during class. Ten minutes in the first half can mark attendance; send only attended/missed. Offer manual I'm here, let I was there override an inference, allow history deletion and never share with a school or professor. | 4; native geofences dependent on 5 / 3, 24–25 |
| STU-25 | **Degree consequences.** Read an uploaded audit or requirement list; distinguish done/in progress/remaining. Explain prerequisite, term, deadline and credit consequences with sources and uncertainty. Always refer final decisions, including aid/status implications, to the school/advisor. | 5 / 25–26 |
| STU-26 | **Campus guide.** Answer service/location/opening-time questions from a reviewed school profile. Show hours and source links, account for exceptions, and offer a plan addition for review. | 5 / 25–26 |
| STU-27 | **Personal calendars.** Connect Google, Outlook/Microsoft 365 and Apple/iCloud; use all authorized calendars for busy time and only the selected writable calendar for approved events. Keep these connections distinct from coursework feeds. | High priority / Update 5–6; CAL-01–05 |
| STU-28 | **When am I free?** Answer today/week availability using waking hours, calendar events, classes, shifts, commitments, travel and meeting buffers. Treat study blocks as movable; omit sub-30-minute gaps unless short slots or a shorter meeting are requested/confirmed. | High priority / Update 6–8; AVL-01–02 |
| STU-29 | **Find and schedule a meeting.** Suggest two suitable options with reasons, ask before adding, accept a contextual natural yes, verify creation and offer Undo and a useful next step. Other people's availability or agreement must not be invented. | High priority / Update 7–8; AVL-03–04 |
| STU-30 | **Get to know the student.** Disclosed ordinary call/chat text becomes encrypted history, summaries, facts and open threads. Memory is useful, correctable and forgettable; off-the-record conversations are neither saved nor learned from. | High priority / Update 8–10; MEM-01–06 |
| STU-31 | **Show it on a whiteboard.** Explain aloud while writing and drawing step by step on one board; allow interruptions answered in place, student contributions and course-aware practice. Deliver Explain, Solve with me, Check my work and Recap under TUT-01–20 and the existing academic-integrity boundary. | Renderer first; full learning flow in 4 / Tutor 2–14 |

## Voice requirements

Voice and text use the same authorized student tools. The existing browser
capture, speech gateway, local wake detector and saved-answer playback are useful
foundations; they do not yet establish the following targets. All rows remain
**Planned** for the complete student behavior.

| ID | Acceptance criteria | Phase / plan pages |
| --- | --- | --- |
| VOI-01 | **Vox is the main entry.** A large always-visible Home button and a small floating button elsewhere use a sound-wave icon, the label Vox and a first-use Talk with NavoX tooltip. One tap starts a hands-free call; nothing must be held. Keep a small chat-box mic for one-off asks and a typed alternative. Wake-word entry is available only inside an already-open Vox call. | Highest priority / Update 2–5; supersedes original primary push-to-talk entry |
| VOI-02 | Handle general chat, learning, brainstorming, practice and stress as well as schedule questions. Offer a relevant planning action without forcing a school-command flow. Keep a warm, consistent personality, short answers, natural numbers, at most three spoken list items, and one question at a time. | 2–4 / 3–10 |
| VOI-03 | Use **LiveKit Agents, one room per call**, with the same authorized tool boundary as text. The agent joins on Vox and leaves on hang-up. Keep the call and conversation context across app navigation/minimizing and network drops; resume the same session or reconnect/reload its authorized text context if it expired. If speech was interrupted by a drop, briefly acknowledge it. Keep the cheaper pipeline fallback. Measure first audio under one second for streaming and median under 800 ms for conversation; under 500 ms remains a goal. | Highest-priority call foundation; 2–3 / 6, 12–14; Update 3–5 |
| VOI-04 | Detect thought completion, allow interruption at any time and stop speech within 200 ms. Distinguish backchannels and background speech; preserve only what was heard. Allow 10–20 seconds to think in study mode. Calls stay quiet through silence instead of nudging at eight seconds or closing at ten. Bye, talk later or Hang up ends the call. | 3 / 6–9, 14; Update 2–4 replaces silence timeout |
| VOI-05 | Read the exact consequential action back, then ask. Contextual yeah, of course, sure and go ahead count as yes; an ambiguous reply gets one short follow-up. Bind approval to that action/version, re-read changes and show long drafts on screen. Calendar Undo reverses the exact created event; do not imply sent email can always be recalled. | 3 / 5–6, 10–11; Update 5–8 |
| VOI-06 | Discard raw audio; retain ordinary call/chat text under the disclosed MEM-01–06 contract, including deletable history and off-the-record mode. This replaces transcripts-off-by-default as the target. Warn before requested private information is spoken aloud. Never identify/profile a student from voice. Team transcript review needs separate student permission, anonymization and revocation; ordinary memory disclosure is not review permission. | Privacy foundation; all voice / 10–11, 14; Update 8–11 |
| VOI-07 | Offer quick ask, conversation, study/quiz, practice, wind-down and walking/driving behavior. Add 4–6 sampled voices, speed controls, multilingual practice and editable pronunciation for names/course codes/buildings. Mode changes must not expand permissions. | 4; native access in 5 / 7–10, 14 |
| VOI-08 | Test dining-hall/bus/wind noise, mumbling, accents, Bluetooth delay, echo cancellation and safe loudness. Retain the consented 200-question corpus, randomized interruptions and 95% correct-action/clean-interruption targets. Team members use voice daily, log awkward moments and run blind comparisons against human-recorded replies. Never show an in-app naturalness rating or Did that feel natural prompt. Use the behavior measures below and only separately permitted anonymized student transcript reviews. | All voice releases / 9–10, 14; Update 10–11 |
| VOI-09 | Add native lock-screen widget, earbud shortcut and alarm-to-call later, respecting OS limits and microphone consent. Phone-locked/background audio requires native iOS/Android support. Web promises an open-tab call only where supported, not reliable phone-background operation. Wake listening stops when a call ends. | 5 / 4, 14, 22–23; Update 2–4 |
| VOI-10 | **Call screen.** Show a calm animated shape that reacts to listening/speaking, call timer, microphone mute, speaker on/off, captions on/off and red Hang up. Microphone mute stops outbound audio; speaker off stops playback and is a separate control. Minimize without ending the call while opening courses/plans. Show useful cards for slots, email drafts and the daily plan. Leave a short recap after a call with decisions, listing only verified actions. Next-class and morning notifications offer Vox entry. | Highest priority / Update 2, 12 |
| VOI-11 | **Long calls and standby.** Calls can remain open for hours. On-device voice detection stops audio transmission during idle; do not send silent audio to speech models or fill silence except useful important notices. At about two minutes enter visibly labeled Standing by: say Hey NavoX. Local wake-word detection or a screen tap resumes the same context; shorter pauses need no wake word. After an hour, give a brief useful catch-up when appropriate. Ignore roommates/TV and tune wake detection across voices, accents and noise. Show a battery note after 2+ call hours. No audio leaves the device in standby; no wake listening continues after hang-up. | Highest priority / Update 3–5 |

Natural approval comes from the student's current turn in the owner-scoped call
that holds the exact pending read-back action. Retrieved text, tool output,
background speech and unrelated transcripts are never action authority. Account
change, reset or hang-up ends that authority and releases capture, wake detection
and call resources; minimizing the call does not end it.

## Learning-system connections

For coursework, the default is a private calendar feed plus reviewed
schedule/syllabus uploads. This is distinct from the personal-calendar
read/write connections below; neither grants the other's permissions.
Full school-approved APIs are an upgrade. Keep the existing Canvas OAuth
connector unchanged while adding the generic feed connector. All rows are
**Planned** for the student integration.

| ID | Acceptance criteria | Phase / plan pages |
| --- | --- | --- |
| LMS-01 | Show appropriate feed-copy instructions for Canvas, Blackboard, Moodle and Brightspace; Google Classroom can use existing Google Calendar authorization. Support Not sure and multiple links. Preview detected courses/items and ask for course matches before saving. | 2 / 28–32 |
| LMS-02 | One Python connector fetches iCalendar, expands recurrence, preserves UID/occurrence identity, normalizes time, strips description HTML and keeps safe source links/location. Map title, course hint, starts/due, source URL and source modification time without inventing grades or announcements. Ask once for an all-day due-time default. | 2 / 32–34 |
| LMS-03 | Conditional fetch with ETag/Last-Modified; diff creates, updates and cancels without duplicates, including cross-feed duplicates. Sync every two hours, every 30 minutes within 24 hours of a due item and on demand. Communicate source refresh delays and missing-course limitations. | 2 / 30, 33–35 |
| LMS-04 | HTTPS only; resolve and pin public addresses, block private/local targets and unsafe redirects, enforce a 5 MB response and ten-second timeout. Encrypt the complete feed URL in the Secret Broker, never log it and display only its domain. Repeated 401/403/404 pauses the feed with a refresh-link prompt. | 2 / 33–35 |
| LMS-05 | Removing a feed deletes its synced items and derived copies, stops future work and cannot resurrect data from an in-flight sync. Feed source content remains untrusted data, never action authority. | 2 / 34 |
| LMS-06 | Verify DST, all-day and recurring events, HTML, 1,000-item feeds, moved/removed/renamed items, duplicate sources, SSRF, size and log redaction. Use real anonymized fixtures and at least two beta students per platform at different schools before claiming platform launch validation. | 2 / 35 |
| LMS-07 | Add full Canvas/Blackboard/Brightspace/Moodle/Classroom APIs only with their school/app approval. Pursue a school at 50+ active students; consider Edlink when several schools need it. Do not ask students for personal access tokens as an OAuth substitute. | 5 / 28–32 |

## Personal calendars and scheduling

All requirements in this section are **Planned**, at Update 1's **High** priority.
The existing primary-calendar read connector is not multi-calendar availability
or approved event writing. Provider verification, tenant restrictions and device
permissions must be checked during each integration; a connection button alone
does not establish support.

| ID | Acceptance criteria | Update pages |
| --- | --- | --- |
| CAL-01 | **Connection entry.** Immediately after timetable setup, offer Connect your calendar so I can find free time and add things for you, with Google, Outlook, Apple/iCloud and Skip. Settings → Connections → Calendar and Connect my calendar in voice/chat open the same screen. Explain read-busy and reviewed-write permissions separately from LMS access. | 5–6 |
| CAL-02 | **Providers.** Google Calendar uses a calendar-purpose OAuth flow without requiring Gmail access; Outlook/Microsoft 365 uses Microsoft Graph and explains school IT restrictions. Native Apple uses the phone calendar, including accounts on the device. Web Apple/iCloud uses CalDAV and an app-specific password with step-by-step help. Keep credentials encrypted and out of transcripts/logs; use secure credential entry. Do not represent an unavailable provider or missing write permission as connected. | 5–6 |
| CAL-03 | **Read all, write one.** Read busy time from every authorized connected calendar for planning, free slots and conflict notices. Write only to the student's selected calendar, using their main calendar as the initial default where available. Recheck the selected calendar and permission before publication; disconnect/revocation invalidates stale results. | 6 |
| CAL-04 | **Reviewed writes.** Never add, move or delete an event without the student's contextual yes. Read back title, local date/time and timezone, target calendar and place when known. Include Added by NavoX in created-event notes. Confirm only verified provider results; an unknown result must not produce a duplicate on retry. Offer immediate Undo that removes the exact created event. | 6–8 |
| CAL-05 | **Private titles and honest availability.** Default spoken answers reveal only busy intervals, such as busy 2–3, unless the student asks what the event is. Detect overlaps with class/lab/shift commitments. Identify stale, incomplete or unavailable calendar data instead of calling an unchecked gap free. No meeting invitation or email is implied by saving an event. | 6–8 |
| AVL-01 | **Free today / this week.** Compute local waking hours minus all connected-calendar busy intervals, confirmed classes, shifts and fixed commitments. Respect date/term exceptions and timezone/DST boundaries. Return useful day/week summaries, with a fuller slot list on screen when requested. Sleep is never presented as available. | 6–7 |
| AVL-02 | **Movable work and buffers.** Treat daily-plan study blocks as movable when finding slots. Include known walking time and 10 minutes before and after meetings. Omit gaps under 30 minutes unless short slots or a shorter meeting duration are requested/confirmed; a 15-minute office-hours appointment can use a suitable shorter gap after buffers. Disclose displaced study work and its proposed destination; keep fixed commitments fixed and avoid an infeasible replacement plan. A calendar-backed study event still needs approval to move. | 7 |
| AVL-03 | **Suitable suggestions.** Infer clear durations: lunch 60 minutes, office hours 15–30, project meeting 60, coffee 30–45; ask only when unclear. Fit lunch between 11:30 and 14:00 and coffee in daytime. Prefer slots near sourced other-person availability, without assuming their agreement; avoid stacking immediately before exams/deadlines. Speak two options, best first, with a short reason; show more if requested. | 7–8 |
| AVL-04 | **Approve, add, verify.** Selecting an option is followed by Should I add it to your calendar? A natural contextual yes approves the exact proposed event and disclosed changes; ambiguous responses get a short follow-up. Refresh busy data before writing and handle a newly conflicting slot by returning to review. Save title/time/known place/provenance to the chosen calendar, verify, then offer Undo or a separately reviewed email/reminder. Approval to draft is not approval to send. | 7–8 |

Verify interval merging, overlaps, all-day and recurring events, DST, missing
travel context, read-only calendars, concurrent edits, revoked connections,
unknown write results and exact Undo. Include the professor-lunch flow and a
meeting that displaces an internal study block. Do not use private calendar
titles as the spoken default or fabricate another attendee's availability.

## Conversation history and memory

All requirements in this section are **Planned**, at Update 1's **High** priority.
Ordinary conversations are retained as text after the disclosed memory setup;
off-the-record conversations are excluded. The retention choices are specified
by the update, but its default is not: record that product decision before
enabling retention rather than silently choosing never. Existing accounts need
the same clear disclosure before this new behavior starts.

| ID | Acceptance criteria | Update pages |
| --- | --- | --- |
| MEM-01 | **Plain disclosure.** Explain on one screen that NavoX remembers conversation text rather than audio to be more helpful, and that the student can see/delete memory or talk off the record. Offer Sounds good and Show me how it works. Keep controls reachable from calls, chat and Settings; skipping disclosure cannot silently begin persistence. | 10 |
| MEM-02 | **Four layers.** Keep encrypted call/chat transcripts in conversations; after each conversation, background work writes conversation_summaries, updates stable user_facts and opens/closes open_threads. Raw audio is processed and discarded, never archived as history or analytics. Every fact retains source-conversation lineage. Summaries and threads must also retain enough provenance for correction and derivative deletion. | 8–10 |
| MEM-03 | **Useful recall.** At call/chat start load the owner's permitted key facts/open threads; search summaries/transcripts when past context is relevant. Use memory to help, not recite it gratuitously. Follow up once on an open thread at a natural moment, then let it go. Promptly apply corrections such as quitting a job, including superseding stale downstream facts. Never invent progress or prior conversations. | 8–10 |
| MEM-04 | **Visible controls.** What NavoX knows about me lists every saved fact with edit/delete and its source. Voice Forget what I said about… removes the targeted saved content and what was learned from it. Off-the-record mode has a persistent call/chat label and neither saves nor learns from that conversation. History supports read/search/delete and transcript auto-delete choices of 30 days, 90 days or never. Settings provides one-step Delete everything. | 9–10 |
| MEM-05 | **Sensitive topics.** Health, mental health, family problems and similarly sensitive facts require a separate Want me to remember that agreement before becoming facts or future check-ins. Crisis conversations never automatically create facts or follow-ups. Ordinary transcript handling must still honor disclosure, retention and off-the-record state. Memory is never used for advertising, sold or shared with schools/professors. | 10 |
| MEM-06 | **Deletion and scope.** Keep transcript, summary, fact and thread ownership isolated. Make forgetting remove dependent learned content, and prevent queued extraction, in-flight retrieval, reconnect or cached context from resurrecting it. Rebuild any retained summary only from remaining authorized sources. Off-the-record mode must also exclude background jobs and durable reconnect history. Auto-delete enforces the selected transcript lifetime; define clearly which separately saved facts remain and make them independently forgettable. Delete everything covers all conversation/memory layers, including existing saved memory and assistant runtime history; student-data reset must call the same erasure boundary. Team review permission is separate from ordinary memory use. | 9–11; existing ARC-03 deletion boundary |

Verify both voice and chat retention, off-record entry before and during a
conversation, account switching, corrections, source-linked forgetting, all
retention choices, deletion while extraction/reconnect is in flight, and a crisis
conversation that produces no automatic facts or follow-ups. Check the four
layers and caches rather than treating a hidden history row as deletion. Never
log transcript bodies or place student memory in repository fixtures.

The existing explicit-memory store and assistant runtime session store are not
the encrypted history model above. Define their migration/retention behavior
before reuse; do not relabel plain stored text as encrypted conversation memory.
Vox's persistent session must not disable existing account/course privacy
clearing when routes, source revisions or owners change. Preserve call continuity
through a scoped session boundary rather than keeping stale course UI alive.

## Whiteboard tutor

NavoX teaches on one persistent, editable board: narration and drawing progress
together, and the student's question is answered beside the current explanation
rather than buried in a separate chat thread. This is NavoX's own implementation
from the supplied guide. Competitor descriptions in the guide are research
references, not verified claims about their private implementation.

The first renderer increment is **implemented and automatically checked**, limited to validated
hand-authored practice lessons and the six initial actions. Scripts use narration
cues for reading-paced reveals, not speech. Pen notes, check answers and review
markers stay in RAM and clear on lesson change, navigation, restart or account
session revalidation; they are not course history or saved learning progress. All complete tutor
requirements below remain **Planned** until their full behavior and error paths
are implemented and verified. Selecting a mode or displaying a sample script
does not establish generated tutoring, grading, voice, OCR or personalization.

| ID | Requirement and acceptance criteria | Tutor pages |
| --- | --- | --- |
| TUT-01 | **Open in context.** Open from Vox with Can you show me? / Draw it out without ending the call; from a problem photo; from Explain this on an authorized course slide, prompt or note; from a reviewed missed-lecture recap; and from Show me why that's wrong after a quiz mistake. Preserve selected course/source provenance and disclose unavailable entry paths. These entry points do not grant microphone, photo, model or course access by themselves. | 2–3 |
| TUT-02 | **Four distinct modes.** Explain teaches a concept step by step with a final check. Solve with me asks for the student's next step and offers hints before revealing it. Check my work reproduces confirmed student work and identifies the exact erroneous line with feedback. Recap builds a short lesson from the selected course's actual slides/notes. Do not present generic practice samples as source-grounded Recap or completed Check my work. | 3–4 |
| TUT-03 | **One navigable board.** Keep prior steps visible on a board that grows downward. Support play/pause, replay current step, timeline jumps, zoom and pan; offer Slower, Explain it differently, Give me an example and Skip ahead by buttons and, when Vox is qualified, voice. The alternate explanation changes approach rather than repeating the same wording. Controls must remain usable with touch and keyboard; preserve access to the explanation text. | 2–5, 8 |
| TUT-04 | **Student contributions and useful outputs.** Allow finger, stylus or mouse drawing and typed/written next steps, with Is this right? feedback. Offer explicit Save to course workspace, board-to-PDF export and flashcard creation, each preserving source and owner scope. Reuse the existing approval, publication, retention and deletion boundaries; generated teaching material must not be mislabeled as the student's own-work submission export. None of these outputs is included in the first renderer increment. | 4, 12–14 |
| TUT-05 | **Graded-work policy.** Use Solve with me or Check my work for graded assignments: hints and feedback, not finished submission answers. Full worked explanations are appropriate for practice and concepts. Apply the course's sourced syllabus AI policy when unclear, ask when needed, and preserve STU-17's disclosed timed-exam restriction. A mode switch or uploaded prompt is not permission for graded ghostwriting. | 4, 13–14 |
| TUT-06 | **Validated lesson scripts.** The Python API's future lesson writer uses NavoX's qualified provider gateway; web/mobile code renders the result. A versioned structured script records lesson/course/mode/title, stable step and action IDs, narration, actions, checks and wait-for-student state. Validate types, bounds, unique IDs, references, cue markers and allowed content before rendering; reject invalid scripts and allow only bounded regeneration. Scripts are data, never executable markup, code or action authority. The first increment uses hand-authored scripts through the same local validation boundary. | 6–9, 14 |
| TUT-07 | **Initial renderer and placement.** Build a custom SVG renderer in apps/web for write (handwriting-style text/animated paths), math (KaTeX equations revealed left to right), shape (boxes/circles), arrow (connectors), highlight (emphasis on an existing element) and plot (function/data graphs). Code owns coordinates and overlap avoidance; scripts request relative placement such as top-left, below:action-id, right-of:action-id or next-row. Reject invalid/forward references as required by the schema, keep labels legible and expand the board without hiding prior steps. Model-provided raw coordinates are not trusted. | 8, 10, 12, 14 |
| TUT-08 | **Expanded board vocabulary.** Add line/connectors and pins; timing diagrams; circuit/logic gates; molecule structures; small tables/truth tables; circle/underline emphasis; scoped cropped slide/student images; and erase/fade of existing elements. All are editable structured elements with validated references, not generated raster math. Subject packs add ECE timing/circuits, chemistry, physics diagrams and statistics plots. Unsupported actions must fail clearly rather than silently disappear. | 7–8, 10–11, 13 |
| TUT-09 | **Math correctness.** Check every equation with KaTeX before display; verify transformations with SymPy where applicable and disclose unsupported verification rather than implying a proof. Include mathematical and visual review of the evaluation corpus. Provide Report a mistake on every step and an owner-safe review path; a local-only report must say whether it was actually submitted. Correct rendering alone is not evidence of correct math. | 8–9, 13 |
| TUT-10 | **Fast and bounded generation.** Generate and stream step 1 first, targeting the first visible stroke in under two seconds. Cache common lesson structure with source/revision checks and re-personalize wording; never reuse another student's private content. Use one streamed lesson script rather than image generation, a qualified strong model for new lessons and a qualified smaller model for quick checks/in-lesson answers when suitable. Track attributable cost and enforce existing route, sensitivity, budget and failure controls before activation. | 9, 11–13 |
| TUT-11 | **Narration/action cues.** Map [[action-id]] positions in narration to TTS word/character timestamps, or qualified forced alignment when needed; remove cue tokens from spoken text. Fire actions at the matching words, with measured timing error within 150 ms on supported devices. Inside Vox use the same realtime speech/cue stream and call ownership, with captions/text alternatives. A timer-driven script without speech is not voice-sync evidence. | 7–9, 11–12 |
| TUT-12 | **Interrupt and resume in place.** On student speech, stop voice and pause at the current action using Vox's barge-in boundary. Send only the authorized question and current step IDs/content to the model. Return a short spoken answer or insert new validated steps directly below the current step. Continue/Okay resumes from the interruption point without duplicating completed actions. Test interruption during drawing, speech, generated insertion, reconnection and account/source changes; a stale reply must not restore cleared content. | 6, 9, 13–14 |
| TUT-13 | **Read work, then confirm.** Photo/handwriting extraction returns text/LaTeX plus each line's image location. Show the extracted work and let the student correct misreads before checking it or circling an error. Evaluate a dedicated math-OCR service only if needed and authorized. Uploads require size/type limits, private source access and explicit provider scope; no OCR or vision service is activated by the renderer increment. | 3–4, 9, 11, 13 |
| TUT-14 | **Course-aware context.** Use the selected course, level and authorized materials, with the professor's notation/examples and links to the actual source slide or note. Retrieve known prerequisites, learning preferences and confirmed mistakes only from permitted owner/course history. Fit lesson length to available time and deadlines read through existing calendar/plan permissions; do not invent missed attendance, mastery, urgency or source material. | 2, 4–6 |
| TUT-15 | **Per-course topic map.** Show a readable topic list with labeled/color-coded not started, shaky, getting it and mastered states. Under a disclosed learning-data contract, update from actual lessons, quick-check/quiz results and Check-my-work evidence, preserving event provenance, uncertainty and course scope. Let the student correct the map or say I already know this to skip; finishing a lesson alone must not falsely establish mastery. Known mistakes and preferences are visible, editable and forgettable under MEM-01–06 before any durable learning-profile or memory writes begin. | 4–6 |
| TUT-16 | **Learning loop.** End every lesson with 1–3 spoken or tapped quick questions and explanatory feedback. Solve with me waits for the next student step before showing it. Offer short spaced reviews of shaky topics over subsequent days and exam-mode lessons from shaky prerequisites/topics. Proposed review sessions, daily-plan changes and calendar events go through the existing reviewed write boundary; a quiz result is not approval to schedule. | 3, 5–6, 13 |
| TUT-17 | **Supportive adaptation.** Prefer the student's current self-report and available time when offering shorter, gentler lessons late at night or before an exam. Any voice-tone-based adaptation needs explicit consent, clear controls and qualified evidence; keep it transient and non-diagnostic, without inferring mental-health conditions or building a sensitive voice profile. Durable sensitive facts/check-ins require MEM-05 agreement. Do not treat time of day or a hesitant answer as proof of mood, ability or mastery. | 5; STU-23, VOI-06, MEM-05 |
| TUT-18 | **Private lesson lifecycle.** Define owned lesson/script revisions, source references, learner contributions, checks, topic-state events and saved outputs before persistence. Apply MEM-01–06 disclosure, off-the-record and forgetting rules to conversational/derived learning context; do not silently expand today's one-question provider grant. Account change, logout, reset, source deletion/revocation and course changes clear unauthorized board/draft/context state and invalidate in-flight publications. Recheck ownership and source freshness at every read/write; course materials, voice or scripts never grant action authority. Keep student photos, work, narration and topic histories out of logs, analytics payloads and repository fixtures. | 4–6, 9, 11; existing ARC-01–06, MEM-01–06 |
| TUT-19 | **Open building blocks and original design.** Prefer custom SVG; evaluate rough.js for shapes/arrows, KaTeX for equations, perfect-freehand for pen strokes, opentype.js plus an appropriately licensed handwriting font for animated text paths, Mafs/function-plot for plots, WaveDrom for timing, SmilesDrawer/RDKit.js for chemistry, and SymPy for math checks. Verify actual dependency and font licenses before shipment; candidate names are not permission or a required installation. Excalidraw is an optional canvas reference; tldraw public deployment needs its applicable license. Use only authorized public/open references and NavoX's own builds for analysis; do not extract proprietary code/prompts/assets or bypass private APIs, logins, paywalls or limits. | 1–2, 10–12, 14 |
| TUT-20 | **Behavior evidence and safe review.** Measure first stroke, completion, check outcomes, topic transitions, repeat use and sampled error rate using the defined denominators below; never ask for a tutor/naturalness rating. Record source revision, device/browser, failures and the observation window. Step mistake reports are explicit issue reports, not rating prompts; private lesson review needs separate permission and redaction. Synthetic checks and five sample lessons are engineering evidence, not student learning outcomes. | 12–14; Update 10–11 |

### Public study-tool reference

The supplied screenshot identifies **Solvely**. Its visible
Study / Exam Prep / Writing navigation and Whiteboard Tutor entry are interface
references. Its public [study tools](https://solvely.ai/) and
[labs](https://labs.solvely.ai/) pages were reviewed on October 18, 2026 (New York).
They describe topic breakdowns, visual explanations, quizzes with feedback,
material-derived study tools and tutoring through hints/questions. These are
public product descriptions; no claim is made about internal code, rendering,
voice synchronization or measured effectiveness. The guide's separate Vercel
demo has no verified official provenance.

| Useful pattern | NavoX application and delivery boundary |
| --- | --- |
| A short, clear topic at the start | Five authored practice lessons expose the subject, topic, description and estimated time before starting. Own wording and board design; no copied assets or prompts. |
| Work through an explanation, then participate | Cue-by-cue reveals, adjustable reading pace, replay, quick-check pauses and answer explanations are in the first renderer. Free-form hints and in-place generated answers remain TUT-02 / TUT-12. |
| One source supports several study activities | Plan a course-source → whiteboard lesson → quiz / flashcards / recap flow under TUT-04 / TUT-14 / TUT-16. Keep actual source citations and revisions; no placeholder upload or generation controls in the authored renderer. |
| Review what needs another look | The first renderer has temporary step markers visible on its timeline. Durable topic history and review scheduling remain TUT-15 / TUT-16, with disclosed learning-data controls and reviewed calendar writes. |

### Tutor delivery phases

These are the guide's seven engineering phases, separate from the student
product's five launch phases. Its durations are estimates, not dates or evidence.
Adding a sample renderer does not enable a model route or advance a student gate.

| Tutor phase | Scope and estimate | Completion evidence |
| --- | --- | --- |
| 1 — Renderer | Custom SVG, hand-authored scripts, six initial actions, layout, play/pause, step replay, timeline and pan/zoom; 1–2 weeks. **First increment implemented and automatically checked**, with five practice lessons, two quick checks per lesson and temporary pen notes. Real-device qualification and animated handwriting remain open. | Five hand-authored lessons play smoothly on actual phones, with recorded device/browser and failures. Automated schema/layout/playback checks are necessary engineering evidence but do not replace this device gate. |
| 2 — Lesson writer | Question/photo to structured script through the Python gateway; schema, KaTeX and applicable SymPy checks; 1–2 weeks. Photo extraction also needs its confirmed-work boundary. | At least 90% of scripts in a predefined 100-question corpus render without errors and have correct math. Record every failed case, denominators and reviewer/check method; no live student data is implied by a synthetic corpus. |
| 3 — Voice sync | Timestamped TTS and cue-driven actions in Vox; about one week. | Drawing/word timing error within 150 ms on supported real devices, with the measurement method and coverage recorded; retain Vox's own latency/privacy gates. |
| 4 — Interruptions / Solve with me | Barge-in, in-place inserted answers, student typed/spoken/drawn next steps and feedback; 1–2 weeks. | Pause/resume preserves the exact current action; answers stay in place; hints wait for student participation. Verify all input forms actually supported, stale-result clearing and native call/device interruptions. |
| 5 — Check my work | Confirmed photo/pen-to-LaTeX transcription and exact error marking; about one week. | Student-correctable extraction, line-location correspondence and sampled mathematical-feedback correctness; never declare OCR accurate from an unconfirmed sample. |
| 6 — Personalization | Authorized materials, per-course topic map, known mistakes, spaced-review proposals and exam mode; about two weeks. | Provenance, student correction/forgetting and approved scheduling pass with account/course isolation. Real learning outcomes remain a separate observation gate. |
| 7 — Subject packs | ECE timing/circuits, chemistry structures, physics diagrams and statistics plots; ongoing. | Each pack validates its structured actions and has reviewed subject examples, accessible controls and device/rendering evidence before being advertised. |

## Architecture and data boundaries

The student request path remains web/voice/notification → TypeScript assistant
runtime → Python tools and connectors → PostgreSQL and Temporal. The change is
from a closed intent router to bounded tool selection and chaining, not a second
independent voice brain or an approval bypass.

| ID | Acceptance criteria | Stage / plan pages |
| --- | --- | --- |
| ARC-01 | A server-owned registry defines tools, schemas, budgets, permissions and approval requirements. Multi-step plans preserve user/workspace/course scope, fail honestly and do not execute arbitrary model-named endpoints. | Planned / 26–27 |
| ARC-02 | Model schools, owned courses/meetings, fixed commitments, tasks/effort, plan outcomes, goals, attendance and notification outcomes. Add owned Vox sessions, personal calendars/busy intervals, selected write targets and exact pending calendar actions, plus conversations, conversation_summaries, user_facts and open_threads with provenance. Tutor extensions need versioned owned lessons/actions, source references, student contributions/check outcomes and course topic-state events under TUT-18. Existing execution plans and verification goals are not substitutes for these domains. | Planned / 27–28; Update 4–10; Tutor 6–9 |
| ARC-03 | Reuse source provenance, exact approvals, encrypted credentials and durable Temporal work. Recheck authority at read/write publication; use revisions/idempotency and bounded retries so deletion, cancellation or revocation wins over stale work. | Implemented foundation; student extensions planned / 1–2, 27, 34 |
| ARC-04 | Update 1 requires disclosed text history and derived memory under MEM-01–06, replacing transcripts-off-by-default as the product target. Make all layers visible, correctable, deletable and source-linked; require separate sensitive-fact agreement and exclude crisis auto-learning. Load only the owner's authorized context under a dedicated session/provider contract. A one-question grant does not authorize persistent-context transmission or unrelated files. | Existing explicit-memory foundation; new memory planned / 9, 28, 36; Update 8–10 |
| ARC-05 | Keep account/course isolation, data minimization, encryption, retention/deletion controls and a privacy policy covering student records and conversational memory. Memory is never sold, used for ads or shared with schools/professors. Legal/privacy review is separate from correctness tests; do not claim certification from a pass. | Implemented foundation; student/memory review planned / 1, 3, 11, 24–25, 37; Update 10 |
| ARC-06 | Preserve gateway qualification, sensitivity and cost controls; add per-student usage/cost measurement before wider voice rollout. Adapter availability does not enable a provider or authorize a new personal-data route. | Implemented foundation; student budget planned / 14, 37–38 |
| ARC-07 | Keep News and subscription tracking behind student-product flags. Preserve existing accounts/data while removing unrelated paths from the student launch. Payments, browser automation, social features, school/professor sharing and graded submissions are out of launch scope. | Planned / 38 |

The older generic source-import `.ics` endpoint is an encrypted immutable import;
its recurrence limitations are distinct from the newer student timetable and
coursework-feed paths. Its preview/digest pattern does not prove STU-05 or LMS-02,
and no `.ics` import proves personal-calendar write support.
Likewise, proactive signals and briefing snapshots are not proof of delivered
web push, and a native alarm cannot be simulated by a browser timer.

## Beta evidence

Recruitment is a separate, explicit activity. No outreach or invitations are
sent automatically. Seek 20–30 consenting students from several colleges, record
the platform and broad device/browser mix, and collect only what is needed for
the relevant check. Synthetic fixtures stay clearly separate from student data.

| Measure | Collection and denominator | Target |
| --- | --- | --- |
| Class accuracy | Student-confirmed timetable/exception ground truth versus recorded now/next/at/day answers at known server times and timezones; at least 20 real students. Record unanswered/incorrect cases, not just successful queries. | 99%+ |
| Setup | Elapsed setup time and completion among consenting sign-ups, including skipped steps and failures. | 80% under five minutes |
| Plan completion | Done blocks divided by scheduled eligible blocks over a stated observation window; retain skipped/longer outcomes. | 60% |
| Draft usefulness | Drafts approved with light edits divided by generated drafts over a stated window. Define light edits before collecting results; exclude neither discarded drafts nor failures silently. | 70% |
| Notification usefulness | Explicit tap or Done outcomes divided by delivered notifications; distinguish suppressed, failed and unsupported delivery. | 35% |
| Weekly use | Students active on at least five distinct local days divided by sign-ups for the measured cohort. Define a useful activity event before rollout. | 40% |
| Retention | Original cohort still active after four weeks, with a predefined active window and exclusions. Do not substitute a four-week forecast or automated session. | 50% |
| Vox habit | Active students who call on at least three distinct local days/week divided by active students in the stated cohort. Keep the original voice-request share measure separately. | At least 50%; original voice-request share 40%+ / Update 11 |
| Voice correctness and latency | First audible response and interruption-stop latency on real devices, plus correct requested action and clean interruptions in the consented corpus. Record call-start and turn-response latency separately from backend task duration; include failures and unavailable audio in the report. No in-app naturalness rating. | Median first audio <800 ms; 95%+ correct/clean; stop <200 ms / Update 11 and original voice gates |
| Call length | Total call duration divided by eligible calls, with sample size, duration distribution and active-speech/standby split. Compare comparable weekly cohorts; retain short and failed calls. Length is a diagnostic proxy, not proof of satisfaction or a reason to prolong a call. | Rising week over week / Update 11 |
| Early hang-up | Calls ended within 10 seconds divided by eligible calls. Record connection failures separately and disclose exclusions instead of silently dropping them. | Under 5% / Update 11 |
| Talk-overs | Measured NavoX talk-over incidents per observed conversation minute; identify the measurement method and distinguish speech from idle standby. | Under 1 per 10 minutes / Update 11 |
| Student repeats | Repetition incidents after misunderstanding per observed conversation minute, with measurement method and coverage recorded. | Under 1 per 10 minutes / Update 11 |
| Corrections | What? / No, I said… or equivalent misunderstanding corrections per comparable observed conversation time, with counts and weekly cohort coverage. | Falling week over week / Update 11 |
| Voice cost | Actual attributable speech/model cost per active student per month, including failures/fallbacks. Establish a budget before opening access. | Plan example: <$1 |
| Tutor start | Elapsed time from an accepted lesson request to the first visible board stroke on supported devices; include timeouts and failed starts. Distinguish cached, hand-authored and generated lessons rather than pooling unlike paths. | Under 2 seconds / Tutor 9, 13 |
| Tutor completion | Completed lessons divided by eligible started lessons in a stated cohort/window, with a predefined completion event. Retain abandonment, failure and short sessions; avoid double-counting replay or resume. | 70%+ / Tutor 13 |
| Tutor checks | Correct first responses divided by answered quick-check questions, with question/topic/course and answer coverage recorded. Keep unanswered checks visible separately; compare comparable course cohorts instead of improving the rate by dropping hard questions. | Rising over a course / Tutor 13 |
| Tutor topic progress | Evidence-linked transitions from shaky to mastered per course over a stated interval; retain reversals, student corrections and topics with unknown evidence. A model label is not a demonstrated learning gain. | Tracked per course / Tutor 13 |
| Tutor repeat use | Active students opening a whiteboard for at least two eligible learning sessions in a week divided by active students in the stated cohort. Define active and eligible session before collection; exclude automatic reopen/replay only by an explicit recorded rule. | 30%+ / Tutor 13 |
| Tutor errors | Lessons with a mathematical or rendering error divided by sampled reviewed lessons, with the sample selection, review coverage, error classes and severity recorded. Count failures and flagged lessons; do not substitute schema-only success. | Under 2% / Tutor 13 |

Collect behavior analytics as minimal events/counts, not transcript or audio
payloads. Keep metrics permission and separately allowed anonymized transcript
review explicit; off-the-record conversations must not leak text through an
analytics path. Team daily use and blind listening comparisons prioritize
interruptions, long pauses, tone and pronunciation. These checks replace asking
students whether a call felt natural; no such survey is required or permitted
in the product.

For each release decision, keep the tested revision/configuration, consent
method, collection dates, cohort size, numerator/denominator, known failures and
decision together. Use aggregate results in repository evidence; private
student schedules, feed URLs, audio, transcripts, email and grades do not belong
in git. A gate remains unmet while its evidence is missing or its target fails.
