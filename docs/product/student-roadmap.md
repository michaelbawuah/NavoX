# NavoX for students

NavoX is moving toward one job: help a student know their day, make a workable
plan, and follow through. The target student has several courses, a job or clubs,
and deadlines scattered across a timetable, learning system, and inbox. Students
at any college must be able to start with their own schedule, without waiting for
their school to approve an integration.

This is the canonical build specification for the **NavoX for Students — Product
Plan, October 17, 2026** (38 pages), amended by **NavoX Plan Update 1 — Builder
Changes, October 17, 2026** (13 pages), and extended by **NavoX Whiteboard Tutor —
Research & Build Guide, October 17, 2026** (14 pages) and **NavoX Whiteboard Update
1 — Builder Spec, October 18, 2026** (22 pages). **Plan Update 1 wins over the
original plan; the Board builder spec wins over the tutor guide where they
conflict.** Board additions preserve Plan Update 1's Vox, privacy, memory and
exact-action approvals. References labeled `Update` point to the plan amendment,
`Tutor` to the 14-page guide, `Board` to the 22-page builder spec, and other page
references to the original plan. Requirements and priorities describe the
intended product, not a claim that it has shipped.

The supplied amendment is `NavoX-Plan-Update-1.pdf`, SHA-256
`76f742d76c022dd80125889e88cdd4aaceacf85988d34ea7b9bf269209c9cca8`.
The supplied tutor guide is `NavoX-Whiteboard-Tutor-Guide.pdf`, SHA-256
`ecf28e86306ec7353a4a29a663f157a83f55458e21346c6ecb2f0cdc92cfa17c`.
The supplied Board builder spec is `NavoX-Board-Builder-Spec.pdf`, SHA-256
`7acaf3e6d46a100def09fa7426d1154206126afde0678abd736b4e886dd59174`.
The optional six-page `NavoX-Builder-Resource-Pack.pdf` (October 18, 2026), SHA-256
`24d09bef071daf94b33ddc30f89d7fafc26ced94865c3d092f2d7f5e8de5b359`,
is a reference catalog, not a requirements amendment or a change in precedence.
The source PDFs are retained separately; this specification records their
builder requirements without placing private conversations or test operations
in git.

The student build is in progress. Existing connected accounts, daily priorities,
voice questions, reviewed email actions, reply watches, previewable routines and
editable saved preferences are foundations for the new experience.

The [delivery plan](delivery-plan.md) records the next source increments,
parallel dependencies and release evidence. It does not replace the requirements
or mark an unmet acceptance gate complete.

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
| Vox ownership foundation | A root provider behind `NEXT_PUBLIC_NAVOX_STUDENT_VOX_ENABLED` holds the transport-injected controller across client-route children. Fresh account checks suspend audio/wake authority while preserving the same call; matched login/logout/reset holds end the old call before network I/O. Page exit and account loss terminate ownership. Sign-in timeouts offer reload recovery, fence late proof and keep interrupted mutations blocked; ordinary hidden-tab suspension does not expire the call. The controller handles independent controls, minimized state, reconnect, quiet standby and pending-join cleanup. Internal search/reply/quick-ask handoffs use client navigation; the page-owned quick mic stops before handoff. | The flag defaults off in the example, Dockerfile and both Compose builds. The default transport is unavailable; no microphone, provider or history is activated. The original ownership increment passed 124 focused checks and the 1,916-test web suite. Native reservations and the later browser UI/adapter are described below; server media authority, memory disclosure/storage, browser navigation and supported-device qualification remain open. |
| Vox server reservations | Native call metadata is bound to the exact web login, owner and personal workspace behind `STUDENT_VOX_ENABLED=false`. Short renewable reservations, one-call admission, immutable lifetime generations, end-before-start fences and shared reset/logout transactions prevent stale work from reopening calls. Successful account replacement revokes only its presented old cookie. See the [server contract](vox-session-contract.md). | Responses explicitly disable media; no room/token, audio, transcript or provider is created. LiveKit media authority, connection epochs, encrypted conversation history and real-device qualification remain open. Terminal metadata cleanup is access-triggered after 24 hours; this is not a timed history-retention service. |
| Vox browser source | The opt-in root-owned call dialog and minimized dock expose a timer, independent microphone/speaker controls, typed navigation and Hang up. A bounded cookie-authenticated reservation client precedes an independently admitted LiveKit 2.23 adapter. Local VAD produces a processed track; agent-only playback has explicit browser sound recovery. Synthetic SDK/Web Audio tests cover late work and cleanup. | The default admission and capture remain unavailable, with no microphone, provider or room activated. Captions are unavailable; the large Home entry is still pending. The client does not yet renew native reservation leases. Deprecated ScriptProcessor capture needs replacement or supported-device qualification. Server media issuance, durable Cloud cleanup, a qualified named worker, exact speech-route qualification and real browser/device evidence are required before calls work. |
| Availability interval core | A pure deterministic calculation separates confirmed openings from provisional candidates using explicit owned source coverage, confirmed routine, hard blockers, known travel, meeting buffers and disclosed movable study conflicts. Unconfirmed routine, stale/missing sources, projection uncertainty and unknown travel prevent an affected opening being called confirmed. | The caller must authenticate and establish a complete fresh source inventory. This core has no endpoint, connector, database or calendar write. Multi-calendar reads, suitable two-option suggestions, feasible displaced-work plans, reviewed writes, Undo and device/student qualification remain planned. |
| NavoX Board foundation | **The authored renderer increment is implemented and automatically checked:** a custom SVG board with validated hand-authored scripts, five practice lessons, the initial six action types, reading-paced play/pause, step replay, timeline, pan/zoom, pen notes and two quick checks per lesson. The public name is NavoX Board; `/board` is canonical and `/whiteboard` is a flag-protected redirect. Authored-lesson v1 types now live in `packages/contracts/src/board.ts`, exported by `@navox/contracts`; this does not add new runtime actions or generated lessons. | Source: `whiteboard-tutor.ts`, `whiteboard-lessons.ts`, `whiteboard-playback.ts`, `whiteboard-scene.tsx` and `student-whiteboard.tsx` in apps/web, with schema, renderer, reducer, DOM-interaction, account-lifecycle and route tests. Deployment requires `NEXT_PUBLIC_NAVOX_STUDENT_WHITEBOARD_ENABLED=true`, default off; the existing internal component/flag names remain. Name clearance is pending. Home suggestions, arbitrary question generation, document/page processing, numbered verified step cards, illustration/update actions, exam prep, topic memory, actual OCR, live Vox/narration sync, board export and flashcards remain planned. Real-phone playback and the new acceptance corpora are not qualified by desktop or simulated tests. |
| Progressive Board reveals | A single bounded presentation clock reveals the next authored element after its reading cue: wrapped text line by line, equations left to right, and seeded sketch outlines/plot curves along their paths. Pause/resume and pace changes preserve partial progress; timeline jumps and reopened snapshots show completed elements immediately. Quick checks wait until the final reveal completes. Reduced motion skips the drawing duration while preserving cue pacing. | The clock controls presentation timing; speech synchronization remains planned, and letter-contour tracing is recorded below. Completed prefixes remain the saved v1 contract; an unfinished reveal is temporary and starts again on reopening. Browser/Safari/real-phone rendering and the required two-minute phone video remain open. |
| Board letter contours | WRITE actions can trace locally compiled handwriting-shaped glyph contours, then fill completed letters. The pinned 200-glyph OFL subset covers the authored practice writing; unknown scripts/combining sequences use the existing full-text fallback. Geometry preserves the authored row breaks and board boxes; one completed-prefix path and one active glyph bound the SVG node count, with static geometry cached. Reduced motion immediately shows the full aggregate path. | This closes the source-level letter-path portion of the authored renderer, with font/source hashes, included license, deterministic compilation, bounds and lifecycle tests. Font contour tracing is not natural pen-stroke order or real handwriting-photo evidence. Supported browser/Safari/phone rendering remains unqualified. |
| Authored Board saves | Explicit named encrypted snapshots preserve the pinned authored lesson edition, playback position, answers, revisit markers and drawings. Reopen pauses playback; recent entries can be linked to an owned course. The new `/api/v1/board/saved` routes support listing, opening, creating, revision-checked updates and erasure. Owner/workspace checks, course checks, opaque retry fences and reset generations protect saved work; no lesson content or private drawings are placed in plaintext SQL columns. | This bounded save increment is separate from generated lessons and topic mastery. `STUDENT_BOARD_ENABLED` defaults off, independently of the renderer flag. Migration `0047_saved_boards` is additive and has a data-preserving downgrade guard. Automated and real-PostgreSQL verification are recorded for the final source revision; real-phone and student validation remain open. |
| Board test-corpus foundation | `evals/board/lesson-questions.json` contains 100 synthetic questions, 20 each in calculus, physics, ECE, chemistry and statistics, spanning 25 question families. `evals/board/student-profiles.json` contains ten synthetic student profiles. The question-pack targeted run passed 102 integrity checks and typecheck; an independent audit of all 100 answer keys found zero discrepancies. The profile-pack run passed 43 targeted checks and typecheck. | Subject-specialist review remains pending. These are fixture/integrity/answer-key results, not live lesson-writer correctness, home-API ranking/latency, model, phone or student qualification. The thirty-document and forty human-work-photo sets still require authorized curation; no handwriting-photo evidence is fabricated. |

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
| 4 — Coach | Semester goals; morning, evening, and weekly check-ins; assignment-help tools and PDF export; NavoX Board under TUT-01–32; course AI policies; opt-in attendance; study, practice, and wind-down voice modes. | At least 50% of students retained after four weeks. Board capabilities also need their own acceptance checks and real-device evidence below. |
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
5. Extend the authored renderer into NavoX Board in parallel, following the
   eight-sprint builder plan below. Add generation and Vox integration only
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
| STU-31 | **NavoX Board.** Explain aloud while writing and drawing step by step on one board; allow interruptions answered in place, student contributions and course-aware practice. Deliver Explain, Solve with me, Check my work, Recap and college Exam prep through the Board home, source pages and reviewed learning flow under TUT-01–32 and the existing academic-integrity boundary. | Authored renderer foundation; full learning flow in 4 / Tutor 2–14; Board 1–22 |

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
by the update. The delivery decision is to require an explicit 30-day, 90-day or
never-auto-delete choice with no option preselected before enabling retention.
Existing accounts need the same clear disclosure before this new behavior starts.

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

## NavoX Board

NavoX teaches on one persistent, editable board: narration and drawing progress
together, and the student's question is answered beside the current explanation
rather than buried in a separate chat thread. This is NavoX's own implementation
from the supplied guide and builder update. The app calls this **NavoX Board**;
whiteboard tutor is internal descriptive language. Competitor descriptions are research
references, not verified claims about their private implementation.

The first renderer increment is **implemented and automatically checked**, limited to validated
hand-authored practice lessons and the six initial actions. Scripts use narration
cues for reading-paced reveals, not speech. The presentation clock now draws
sketch outlines progressively, reveals wrapped text in line order and wipes
KaTeX equations from left to right. Supported WRITE text now uses precompiled
handwriting-shaped font contours, progressively traced and then filled. Unsupported
scripts retain the accessible line-reveal fallback. These are font outlines, not
a person's pen-stroke order or TTS word synchronization. Only fully revealed
elements advance the saved playback position; a partial reveal stays temporary.
Unsaved pen notes, check answers and review markers remain temporary. The separate explicit-save increment stores
bounded encrypted snapshots tied to an immutable authored lesson hash; reopening
restores practice state paused. These answers are practice records, not evidence
of topic mastery. Account session changes clear private in-memory state. All complete tutor
requirements below, including every addition in the 22-page builder spec, remain
**Planned** until their full behavior and error paths
are implemented and verified. Selecting a mode or displaying a sample script
does not establish generated tutoring, grading, voice, OCR or personalization.
In this requirements table, unprefixed page numbers refer to the Tutor guide;
`Board` identifies the newer builder spec and `Update` the plan amendment.

| ID | Requirement and acceptance criteria | Tutor / Board pages |
| --- | --- | --- |
| TUT-01 | **Open in context.** Open from Vox with Can you show me? / Draw it out without ending the call; from a problem photo; from Explain this on an authorized course slide, prompt or note; from a reviewed missed-lecture recap; and from Show me why that's wrong after a quiz mistake. Preserve selected course/source provenance and disclose unavailable entry paths. These entry points do not grant microphone, photo, model or course access by themselves. | 2–3 |
| TUT-02 | **Four baseline modes plus exam prep.** Explain teaches a concept step by step with a final check. Solve with me asks for the student's next step and offers hints before revealing it. Check my work reproduces confirmed student work and identifies the exact erroneous line with feedback. Recap builds a short lesson from the selected course's actual slides/notes. College Exam prep composes these activities under TUT-27. Do not present generic practice samples as source-grounded Recap or completed Check my work. | Tutor 3–4; Board 7–8, 11, 13–14 |
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
| TUT-15 | **Per-course topic map.** Show labeled/color-coded not started, shaky, getting it and mastered states. Under a disclosed learning-data contract, preserve actual check evidence, first-try status, provenance, uncertainty and course scope, using the Board transition rules below. Let the student correct the map; I already know this explicitly marks mastered as a student assertion, distinguishable from observed check evidence. Completing a lesson alone does not establish mastery. Known mistakes and preferences remain visible, editable and forgettable under MEM-01–06 before durable learning-profile or memory writes begin. | Tutor 4–6; Board 12–13 |
| TUT-16 | **Learning loop.** End every lesson with 1–3 spoken or tapped quick questions and explanatory feedback. Solve with me waits for the next student step before showing it. Offer short spaced reviews of shaky topics over subsequent days and exam-mode lessons from shaky prerequisites/topics. Proposed review sessions, daily-plan changes and calendar events go through the existing reviewed write boundary; a quiz result is not approval to schedule. | 3, 5–6, 13 |
| TUT-17 | **Supportive adaptation.** Prefer the student's current self-report and available time when offering shorter, gentler lessons late at night or before an exam. Any voice-tone-based adaptation needs explicit consent, clear controls and qualified evidence; keep it transient and non-diagnostic, without inferring mental-health conditions or building a sensitive voice profile. Durable sensitive facts/check-ins require MEM-05 agreement. Do not treat time of day or a hesitant answer as proof of mood, ability or mastery. | 5; STU-23, VOI-06, MEM-05 |
| TUT-18 | **Private lesson lifecycle.** Define owned lesson/script revisions, source references, learner contributions, checks, topic-state events and saved outputs before persistence. Apply MEM-01–06 disclosure, off-the-record and forgetting rules to conversational/derived learning context; do not silently expand today's one-question provider grant. Account change, logout, reset, source deletion/revocation and course changes clear unauthorized board/draft/context state and invalidate in-flight publications. Recheck ownership and source freshness at every read/write; course materials, voice or scripts never grant action authority. Keep student photos, work, narration and topic histories out of logs, analytics payloads and repository fixtures. | 4–6, 9, 11; existing ARC-01–06, MEM-01–06 |
| TUT-19 | **Open building blocks and original design.** Prefer custom SVG; evaluate rough.js for shapes/arrows, KaTeX for equations, perfect-freehand for pen strokes, opentype.js plus an appropriately licensed handwriting font for animated text paths, Mafs/function-plot for plots, WaveDrom for timing, SmilesDrawer/RDKit.js for chemistry, and SymPy for math checks. Verify actual dependency and font licenses before shipment; candidate names are not permission or a required installation. Excalidraw is an optional canvas reference; tldraw public deployment needs its applicable license. Use only authorized public/open references and NavoX's own builds for analysis; do not extract proprietary code/prompts/assets or bypass private APIs, logins, paywalls or limits. | 1–2, 10–12, 14 |
| TUT-20 | **Behavior evidence and safe review.** Measure first stroke, completion, check outcomes, topic transitions, repeat use and sampled error rate using the defined denominators below; never ask for a tutor/naturalness rating. Record source revision, device/browser, failures and the observation window. Step mistake reports are explicit issue reports, not rating prompts; private lesson review needs separate permission and redaction. Synthetic checks and five sample lessons are engineering evidence, not student learning outcomes. | 12–14; Update 10–11 |
| TUT-21 | **Board home and naming — High.** Use NavoX Board, Open the Board and Show me on the Board, with an original NavoX layout and a name-availability check before launch. `/board` is the home and `/board/[boardId]` an owned saved board. Greet by the confirmed name and local time of day. One question/topic input starts on Enter or Start. Upload accepts supported documents/images into the page flow; Snap opens a permitted camera into Solve with me or Check my work; Talk starts the qualified Vox call with the Board open, rather than a mic per card. Keep typed/accessibility alternatives and honest unsupported states; do not display nonworking generation, upload or voice controls as ready. | Board 1–3, 9, 20–22 |
| TUT-22 | **Suggestions and recent boards — High.** Show four useful cards, ordered by exam within seven days, missed class, shaky topic, then assignment due within three days. Each identifies an actual course and a specific outcome/duration and opens its exact mode. Use authorized calendar/deadline/recovery/topic evidence; fill missing context with appropriate starters rather than inventing an exam or weakness. A new student sees Upload your slides, Snap a problem, Explain a concept and Quiz me. Dismissal suppresses that card for the rest of the student's local day. List saved recent boards grouped by course and reopen at the saved position; neither sample lesson selection nor a temporary marker is recent-board persistence. | Board 1–3, 10, 19–21 |
| TUT-23 | **Page-based document processing — High.** Upload from Board home, an owned course workspace or supported phone file-sharing. Target PDF, PowerPoint, Word and images, up to 200 pages and 50 MB per file. Convert PowerPoint/Word to PDF with bounded headless LibreOffice; split page images/text with PDF.js or PyMuPDF, extract text/headings/LaTeX/images and use an authorized vision route for scans/diagrams. Index each page with its document/course/page identity and pgvector embedding, using Temporal for slow work. Show Reading your file… and processing progress; page 1 can be queried while later pages process. Confirm the matching course before course saving. Encrypt originals and derived content and allow deletion at any time. This new Board pipeline does not silently raise limits on existing course-upload endpoints. | Board 3–4, 9–12, 20–22 |
| TUT-24 | **Find and teach on any page — High.** Resolve explicit slide/page numbers directly; answer descriptive requests through the authorized document's per-page semantic index, returning up to three candidates. When the best two are close, show both thumbnails for the student's choice. Explain missing/out-of-range or not-yet-processed pages instead of silently substituting an unrelated page. Place the chosen page as a source-linked image action and draw highlights, circles and arrows over it; tapping/circling a region scopes the question. Retain document ID, page number and revision in citations; a chapter recap identifies its actual processed source coverage. | Board 3–4, 10–11, 13, 16–17, 19 |
| TUT-25 | **Numbered step cards — High.** Add `step_card` with stable ID, number, short verb name, work items, highlighted result, status and relative placement. Show pending as a dot, correct as a green check and review as an amber circle plus note, with text equivalents and an animated status change. One idea per card; split work longer than about three lines. The final card is Check the answer, including units/sign/plausibility. Explain checks mean the narrated step finished; they must not imply independent math verification. Solve with me initially shows the step name, waits for the student's work and checks only after verification; offer a hint on request or after one wrong try, without writing the next answer. Check my work copies confirmed student lines, checks correct ones and marks the first wrong one review with feedback. Unknown checks stay explicitly unverified. | Board 5–6, 13–15, 19–22 |
| TUT-26 | **Original picture metaphors — Medium.** Add validated `illustration` and `update` actions. Each of the 12 starter SVGs below has named parts, bounded typed parameters and original artwork in a consistent rough.js/small-palette style. Models choose and label an existing illustration; they never generate or replace its pixels/code. A later update targets an existing compatible illustration and animates only allowed parameter changes; mathematical labels render through safe KaTeX. Add 3–5 original illustrations per later subject pack after review. | Board 6–7, 15–16, 19–22 |
| TUT-27 | **College exam prep — Medium.** Offer prep when an actual exam is sourced from the calendar/syllabus, by voice or from its home card. Build scope from the syllabus, slides since the previous exam and permitted professor emails/announcements; the student confirms/edits it first. Run an approximately ten-minute diagnostic with 1–2 questions per topic. Propose 15–25-minute sessions on shaky topics in available waking time before the exam, harder topics earlier, plus adaptive Solve-with-me practice and authorized uploaded past exams. Propose a 20-minute review of the hardest topics the night before. Encouragement supports rest without promising an exam outcome. Read back and approve exact plan/calendar changes under STU-14 / CAL-04 / AVL-04 before writing, then verify; automatic discovery is an offer, not automatic scheduling. MCAT/GRE/LSAT packs remain later and out of launch. | Board 7–8, 10–12, 19–22 |
| TUT-28 | **Shared contracts and streamed lessons.** Extend the shared TypeScript lesson/action types in `packages/contracts/src/board.ts` with matching Python Pydantic validation. Only the six-action authored v1 types are currently implemented; the extended writer contract remains planned. The writer receives only permitted name/course/level, topic evidence, known mistakes/preferences, available minutes and retrieved source pages. Use 1–3 short plain spoken sentences per step, exact action cues, relative placement, page citations and a 1-line wrap-up after 1–3 checks. Stream one validated step record per JSON Line with no prose outside JSON; expose checked records over the owned SSE stream with stable board/step IDs and revision ordering. The planned writer allow-list is write, math, shape, arrow, line, highlight, circle, underline, erase, step_card, plot, timing, table, image, illustration and update; later subject actions require a versioned extension. Mid-lesson answers use 1–2 spoken sentences and at most three validated inserted steps, then ask how to continue. | Board 9–14 |
| TUT-29 | **Checked steps and quality.** Adapt the guide's checker sketches with explicit mathematical domains/variables/assumptions and bounded SymPy work; equation solution-set checks and expression equivalence do not prove every word problem, proof or units argument. Parsing/verification uncertainty returns unknown. An authorized model fallback is recorded as checked by AI in review metadata, distinct from CAS verification. Operational logs carry only checking method, status and scoped IDs; never equations, prompts or student work. Meet the new separate 95% valid-script, 95% correct-final-answer and 100% KaTeX-rendering targets; these supersede the older 90% writer gate. | Board 13–15, 17–20 |
| TUT-30 | **Owned persistence, APIs and deletion.** Add the six PostgreSQL/Alembic domains and signed-in API surface below. Every table, including steps/pages, carries user_id; enforce user/workspace/course ownership on every read, stream, search, check and write. Source files marked private go only to controlled encrypted storage and the explicitly authorized processing AI provider, never unrelated third parties; a private label is not provider consent. Delete board/document content, derived steps/pages/images/embeddings, references and affected caches, and make deletion win over queued processing, streams and topic extraction. Apply TUT-18 and MEM-01–06 to retained learning context. The bounded authored snapshot API below is implemented separately; generated steps, document derivatives and background processing remain planned. | Board 4, 10–12, 19–22 |
| TUT-31 | **Acceptance corpora first.** Create all four fixed corpora below before qualifying generation, uploads, work checking or personalized cards. Record revision, configuration, denominators, failed cases, method and real-device coverage for every acceptance target. Synthetic profiles, fixture photos and successful local rendering do not establish real student benefit, privacy compliance or phone qualification. | Board 17–20 |
| TUT-32 | **Eight sprints and original delivery.** Follow the eight-sprint plan and its explicit cut order below, retaining the existing privacy/approval/student gates. End each sprint with a two-minute real-phone screen video of the new feature shared with Michael before the next sprint; no simulated browser capture substitutes for it. Use NavoX's own wording, layout, icons, card/illustration art and any future character. Design review may compare supplied competitor references, but never trace/reuse their screens, code, prompts, assets, product names or marketing art. | Board 8–9, 19–22 |

### Board illustrations and topic evidence

All illustration components and durable topic rules are **Planned**. The twelve
starter illustrations and parameter contracts are (Board 6–7):

| Original SVG | Teaches | Validated parameters |
| --- | --- | --- |
| Balance scale | Equivalent operations on equations | Left/right items; tilt |
| Number line | Integers, intervals, inequalities | Range; points; shaded regions |
| Container / tank | Rates, concentration, capacitors | Fill level; inflow/outflow |
| Gears | Ratios, mechanical advantage | Teeth counts; rotation direction |
| Pipes and water | Current/voltage/resistance analogy | Pressure; pipe width; flow |
| Spring and mass | Oscillation, forces, energy | Stretch; mass; motion |
| Ramp and block | Inclines, friction, forces | Angle; mass; force arrows |
| Cell | Structures and mitosis | Stage; labels |
| Magnet and field lines | Magnetism and fields | Pole positions; line density |
| Stacked blocks | Place value, factoring, area | Rows; columns; labels |
| Timeline | History, processes, algorithms | Events; positions |
| Funnel / sieve | Filtering, sorting, probability | Inputs; items passing through |

Use the explicit initial topic transitions from Board 12, preserving whether a
state came from a check, correction or self-report. A state is a planning signal,
not an inferred grade or diagnosis; wrong answers and absence are not moral
judgments. Student correction/forgetting controls remain available.

| Evidence event | Topic update |
| --- | --- |
| Correct quick check on the first try | not_started → getting_it; shaky → getting_it; getting_it → mastered; mastered stays mastered |
| Wrong quick check or a verified step marked review | One level down, with shaky as the floor: mastered → getting_it; getting_it → shaky; shaky stays shaky; not_started becomes shaky |
| Student explicitly says I already know this | mastered, labeled student-reported rather than independently demonstrated |
| No evidence seen for 21 days and topic is not mastered | shaky, with a spaced-review proposal; this does not authorize a plan/calendar write |

### Board data and API contract

The new table names are domain requirements, not permission to duplicate an
existing document store. Reuse compatible owned storage with explicit migrations
and provenance. The current authored snapshots below do not implement generated steps or source-document storage.
Every row below requires user_id, including child rows, plus enforcement of its
parent's owner/course/workspace (Board 11–12).

| PostgreSQL domain | Required fields and behavior |
| --- | --- |
| boards | id, user_id, course_id, title, mode (explain/solve/check/recap/exam), source (text/photo/document_page/card/vox), created_at, last_opened_at; saved playback position/revision for safe continue |
| board_steps | id, user_id, board_id, position, script JSON, status (pending/correct/review), student_work JSON, inserted_from_question; preserve insertion order/revisions |
| documents | id, user_id, course_id, filename, kind (pdf/pptx/docx/image), page_count, status (uploading/processing/ready/failed), storage_key; encrypted original in private object storage |
| document_pages | id, user_id, document_id, page_number, image_key, text, headings, latex, pgvector embedding; stable page identity and revision |
| topics | id, user_id, course_id, name, status (not_started/shaky/getting_it/mastered), last_seen_at, evidence JSON with check provenance |
| exam_preps | id, user_id, course_id, exam_at, confirmed scope topic IDs, plan JSON sessions, per-topic readiness; exact created calendar-event references for verification/Undo |

The authored-only save increment uses `student_saved_boards`,
`student_board_states` and `student_board_commands`. The snapshot is one encrypted
payload, not a generated lesson or inferred topic record. It contains a pinned
lesson ID/hash, title, position, explicit answers, revisit markers and bounded
ink. The owner/workspace/course/revision are authenticated encryption bindings.
Retry commands retain only opaque IDs, keyed fingerprints and revision/status
fences after content erasure. These are not a copy of the erased student work.

Its implemented route surface is:

| Method | Route | Contract |
| --- | --- | --- |
| GET / POST | `/api/v1/board/saved` | Recent owned summaries and reset generation / explicit save of a supported authored edition |
| GET / PATCH | `/api/v1/board/saved/{id}` | Reopen / replace exact saved snapshot using its expected revision |
| DELETE | `/api/v1/board/saved/{id}` | Revision-checked erasure; available even when saves are disabled or the encryption key is unavailable |

Every Board request carries the expected account/workspace in `X-NavoX-Account`;
the API rejects a cookie-account mismatch before reading or writing private
content. Creates carry the collection generation; all mutations carry unique request IDs.
Exact retries cannot recreate erased work. Updates do not silently overwrite
another tab's revision. Reset erases snapshots and advances the generation in the
same locked transaction as existing student-data deletion. Course-linked rows
cascade when the course is deleted; the existing course UI supports archive,
not individual course deletion. Archived Boards remain readable and erasable;
changing them requires restoring the course. Limits are fifty Boards per owner,
eighty strokes per Board, 1,500 points per stroke, 20,000 total points and a 2 MiB
request body. Lists contain at most fifty summaries; there is no autosave,
provider call, upload processing or learned topic history in this increment.

The planned route surface from Board 10–11 is:

| Method | Route | Contract |
| --- | --- | --- |
| GET | `/api/v1/board/home` | Owner greeting, four ordered cards and course-grouped recent boards |
| POST | `/api/v1/board/lessons` | Start from text/photo/page/card, return board ID and start checked-step streaming |
| GET | `/api/v1/board/lessons/{id}/stream` | Owned SSE delivery of individually written/validated steps |
| POST | `/api/v1/board/lessons/{id}/ask` | Scoped interruption; insert validated steps after the current step |
| POST | `/api/v1/board/lessons/{id}/steps/{step}/check` | Student work → correct/review or explicit unknown, with verification method/note |
| POST | `/api/v1/board/documents` | Bounded upload → document ID and processing job |
| GET | `/api/v1/board/documents/{id}` | Processing status, available pages and failures |
| POST | `/api/v1/board/documents/{id}/find` | Explicit number or description → authorized page candidates |
| POST | `/api/v1/board/exam-prep` | Confirmed scope/readiness and a reviewed proposed prep plan; no unapproved scheduling |
| GET | `/api/v1/board/topics/{courseId}` | Owner's course topic map and evidence |
| DELETE | `/api/v1/board/{boardId}` | Delete the exact owned board and derived data |

Board 19 also requires deleting a document, although its endpoint table omits
that route. Add an owned document-delete contract, such as
`DELETE /api/v1/board/documents/{id}`, and prove derivative erasure before enabling
uploads. Define owner/day-scoped card dismissal and board-position saving in the
same versioned API contract. These are required completion details, not claims
that an endpoint already exists.

Use `apps/web/src/app/board`, reusable `components/board` canvas/player/layout,
typed action and illustration components, BoardHome/SuggestionCards/RecentBoards,
and a student-pen component. Put API routes, lesson writer, schema/checks,
documents/page finder, suggestions, exam prep and topic map under
`services/api/navox/board`; slow processing belongs in
`services/api/navox/temporal/board_workflows.py`. Match the shared contracts to
Python validation (Board 9–10), retaining the existing gateway and ownership
boundaries rather than creating a separate unrestricted tutor service.
Verify licenses and bounded conversion, parsing, embedding and storage budgets
before adding LibreOffice, PDF.js/PyMuPDF, pgvector or any external processor;
listing a building block here does not install it or authorize private data use.

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

### Board acceptance and delivery

The builder update replaces the tutor guide's seven-phase engineering sequence
with eight one-week sprints, estimated for one full-time builder. This is separate
from the student product's five launch phases; the authored renderer does not
complete sprint 1's full corpus or real-phone requirements. Explicit authored
snapshot persistence is implemented as recorded above. No new model
route, document processor, persistent learner history or calendar writer is
enabled by adding this specification.

Build all four fixed, appropriately authorized test corpora first (Board 17).
The 100-question and ten-profile fixture files are present with targeted
integrity checks as recorded above. Subject-specialist review and all actual
lesson-writer/home-API qualification remain pending. The thirty documents and
forty varied-handwriting work photos still require authorized curation and
human/consented provenance; synthetic font images are not real handwriting
evidence. Keep real private student files and work out of repository fixtures,
using permitted redacted/public test sources and separately protected curated
evidence as needed.

| Corpus | Required coverage |
| --- | --- |
| 100 questions | Calculus, physics, ECE, chemistry and statistics; each has reviewed correct final-answer ground truth |
| 30 uploaded files | Lecture PDFs, PowerPoints and Word handouts, including five scanned PDFs; three explicit-number/description page questions each, totaling 90 |
| 40 student-work photos | Twenty fully correct and twenty with one known wrong step, across different handwriting |
| 10 fake student profiles | Owned calendars, courses, topic maps and exams for suggestion ordering, no-data starters and prep plans |

All gates below remain **unmet for the new behavior** until recorded evidence
exists. Report script validity and final-answer correctness separately over the
full 100-question set, and all attempted LaTeX rendering; do not improve a rate
by omitting invalid scripts or failed questions. The 95% / 95% / 100% criteria
supersede the older combined 90% lesson-writer target (Board 19). These engineering
corpora do not replace the separate student-use/error metrics below.

| Feature | Required acceptance evidence | Target / Board pages |
| --- | --- | --- |
| Board home | All ten fake profiles produce four cards matching priority, source and dismissal rules; no-data profile has all four starters | Load under 1 second / 19 |
| Lesson writer | Full 100-question set, reviewed final answers and every LaTeX item | At least 95% valid scripts; at least 95% correct final answers; 100% LaTeX renders in KaTeX / 19–20 |
| First step | Request-to-first checked step displayed, including failures; separate cached/authored/generated paths | Under 2 seconds median / 19 |
| Step cards / checker | Twenty algebra problems with correct and wrong student lines, result method and explanatory notes | At least 95% correct check/review decisions / 19–20 |
| Check my work | All forty photos with confirmed transcription and known wrong-line ground truth | Find the wrong step in at least 85% of the twenty wrong examples; no false alarm in at least 90% of the twenty correct examples / 19 |
| Upload processing | All thirty files, including scanned/office formats, partial-page availability and failure paths | Fifty-page files ready under 60 seconds; first page askable under 10 seconds / 19 |
| Page finder | All ninety number/description page questions, retaining ambiguous and failed matches | Explicit slide/page number correct first 100%; descriptive page in top three at least 90% / 17, 19 |
| Illustrations | All twelve original SVGs with sample parameters and at least one update on an actual phone | No overlap; smooth update animation / 19 |
| Exam prep | Each exam-bearing fake profile with an exam seven days away; exact confirmed scope and approved publication checked | Only free time, no sessions after bedtime, harder topics first, final review the night before; no write before approval / 19 |
| Interruptions | Questions at ten random points in five lessons; record the exact selection/count and device coverage | Board pauses; answer inserts below the current step; resume at the same place; drawing/word sync within 150 ms / 19–20 |
| Privacy | Delete an owned board and document, including deletion during processing/streaming | All affected steps, pages, images and embeddings removed; no delayed recreation, cross-owner read or stale cache / 19 |
| Originality | Review NavoX against authorized competitor references without importing their assets into the product | No matching layout, icons, art, product names or text / 19 |

| Sprint | Scope and student outcome | Completion gate / Board pages |
| --- | --- | --- |
| 1 | Test corpora, owned board tables/migrations, shared lesson types, renderer and layout. Student can watch five hand-authored lessons. Existing authored renderer is reusable; remaining work is still required. | Five lessons on an actual phone without overlaps; corpus and persistence checks / 20 |
| 2 | Streamed lesson-writer endpoint with schema/KaTeX checks and Board home starter cards. Student types a question and receives a lesson. | 100-question writer acceptance targets and first-step latency; starter-card/home tests / 19–20 |
| 3 | step_card, bounded SymPy checker and Solve with me. Student contributes each next step. | Step-check tests pass, including unknown/incorrect lines and explanation of verification method / 20 |
| 4 | Cue-based voice sync, Talk → Vox with Board, and mid-lesson questions. Student talks and interrupts in place. | Interruption tests; drawings within 150 ms of corresponding words on supported devices / 20 |
| 5 | Document upload/conversion/page split/extraction/indexing as a Temporal job, page finding and page overlays. Student asks about slides/readings. | Upload and page-finder gates, owned publication and usable deletion / 20 |
| 6 | Snap, confirmed vision-to-LaTeX work checking and the first six original illustrations. Student identifies a wrong line. | Work-photo acceptance and first-six illustration rendering/update checks / 20 |
| 7 | Topic updates, personalized home cards and the remaining six illustrations. Student sees suggestions from their actual authorized week. | Home-card gates for all ten profiles, topic evidence/privacy controls and full illustration checks / 20 |
| 8 | Confirmed exam scope, diagnostic, reviewed scheduling and night-before review; complete deletion verification, originality review and polish. Student can approve and verify a full prelim prep plan. | All acceptance tests pass; exact approved calendar results verified / 21 |

Privacy, owner checks, disclosure and deletion apply from the first persistence
or upload; sprint 8 completes end-to-end verification rather than postponing
those boundaries. Before each next sprint, record a two-minute video of the new
feature on a real phone and share it with Michael (Board 21). Missing phone/demo
evidence stays explicit; automated desktop tests cannot be relabeled as that demo.

If time is short, cut in the specified order: illustrations 7–12 first; exam-prep
scheduling second while keeping the readiness check; Word support third while
keeping PDF and PowerPoint. Mark each cut unavailable and adjust its claimed
scope and acceptance report. Never cut privacy, mathematical honesty or exact
approval to satisfy a date. Later subject packs retain the tutor guide's timing,
circuit, molecule, physics and statistics work, plus 3–5 new original illustrations
per pack; they are not implicitly supported by the initial writer allow-list
(Board 7, 21; Tutor 13).

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

### Optional builder resources

The six-page resource pack is implementation advice, not another feature plan.
Keep the existing custom SVG renderer, KaTeX and rough.js; the latter two have
verified MIT licenses. No resource-pack dependency or provider is installed or
enabled by this update. Use primary documentation to assess the relevant
increment rather than replacing the established architecture (Resource Pack 1–6).

| Area | Focused adoption notes |
| --- | --- |
| Vox and wake detection | Use LiveKit's [frontend](https://docs.livekit.io/agents/start/frontend) and [turn/interruptions](https://docs.livekit.io/agents/build/turns) references for the planned Vox session. Alternative speech/TTS frameworks remain options, not new routes. For [openWakeWord](https://github.com/dscripka/openWakeWord#license), Apache 2.0 code and included CC BY-NC-SA 4.0 models have separate rights; review the actual model before commercial use. |
| Board and document processing | Keep canvas starters as references: [tldraw production use requires an active license](https://tldraw.dev/community/license), so a starter does not make it the chosen renderer. [PyMuPDF requires an AGPL/commercial-license decision](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) before adoption. SymPy, OCR, pen/plot/timing libraries, Temporal and pgvector remain candidates for their already-specified work, subject to bounds, privacy and license review. |
| Calendar availability | [Google freebusy](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query) can return per-calendar errors; those calendars remain unknown, never free. Microsoft [getSchedule](https://learn.microsoft.com/en-us/graph/api/calendar-getschedule?view=graph-rest-1.0) does not support personal Microsoft accounts. [calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0) supports delegated personal-account event reads, but deriving authorized busy intervals from it needs separate qualification. Neither reference proves the personal-calendar feature is ready. |
| Feeds and native delivery | Feed-copy/recurrence references can inform reviewed onboarding and fixtures without granting full learning-system access. Expo Audio and Apple AlarmKit guidance belongs to the later native implementation; verify platform permissions, entitlements and real-device behavior before promising background calls or alarms. A browser timer is still not a native alarm. |

The referenced [arXiv paper 2512.01234](https://arxiv.org/abs/2512.01234) explores
proactive agentic whiteboards; it does not establish classroom learning efficacy
or validate NavoX's renderer. Public product/blog examples are behavioral
references, not evidence of private internals or permission to reuse assets.
These notes record engineering adoption constraints, not legal clearance.

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
