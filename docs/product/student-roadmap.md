# NavoX for students

NavoX is moving toward one job: help a student know their day, make a workable
plan, and follow through. The target student has several courses, a job or clubs,
and deadlines scattered across a timetable, learning system, and inbox. Students
at any college must be able to start with their own schedule, without waiting for
their school to approve an integration.

This roadmap tracks the **NavoX for Students — Product Plan, October 17, 2026**
(38 pages). Page references identify its requirements; they are not claims that
the feature has shipped. The student build is in progress. The existing product
already has connected accounts, daily priorities, voice questions, reviewed email
actions, reply watches, previewable routines, and editable saved preferences.

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

The tables below record the student requirements as **Planned**, with active
Phase 1 work identified separately. Later engineering can be prepared behind
disabled flags; a launch phase advances only after its preceding student gate.
No student gate has been claimed as met.

## Delivery order

| Phase | Student outcome and scope | Launch gate |
| --- | --- | --- |
| 1 — Know the day | Any-school onboarding; manual, `.ics`, and reviewed screenshot timetable import; now/next/at-time class lookup; next-class and leave-now alerts; installable web app; push-to-talk quick asks. **In progress.** | At least 99% correct class answers with 20 real test students. Recruit 20–30 students across several colleges. |
| 2 — Plan the day | Shared tool-calling core; streaming voice replies; daily time blocks with Done, Took longer, and Skip; learning-system calendar feeds and course workspaces. | At least 60% of plan blocks marked Done. |
| 3 — Act for you | Reviewed email-to-action; deadlines in the plan; recovery plans and extension drafts; realtime conversation and interruptions; student memory. | At least 70% of email drafts approved with light edits. |
| 4 — Coach | Semester goals; morning, evening, and weekly check-ins; assignment-help tools and PDF export; course AI policies; opt-in attendance; study, practice, and wind-down voice modes. | At least 50% of students retained after four weeks. |
| 5 — Grow | Degree consequences, campus guide, more school profiles, native mobile app, verified public Gmail access, and school-approved learning-system APIs. | Expand only with the preceding gates and the permissions each integration requires. |

The source plan labels these roughly weeks 1–2, 3–5, 6–8, 9–11, and week 12
onward. These are planning estimates, not promised dates or substitutes for gates.

## Product requirements

All rows below are **Planned**. Phase 1 rows are currently being implemented; the
table does not mark unfinished work as verified.

| ID | Requirement and acceptance criteria | Phase / plan pages |
| --- | --- | --- |
| STU-01 | **Skippable setup.** Ask preferred name, school and learning system, timetable, optional connections, shifts, routine, 1–3 goals, notifications, and optional attendance. Every step can be skipped and revisited. Target about three minutes; measure 80% finishing in under five minutes. | 1, extending with later features / 1–3 |
| STU-02 | **Any-school start.** An unknown school works with a student-owned timetable. School profiles carry calendar, buildings, services and learning-system type; unknown facts remain unknown. Publicly drafted profiles require review. | 1 foundation; 5 expansion / 2, 25–26 |
| STU-03 | **Plain permissions.** Each connection explains exactly what is read, why, how to disconnect and how to delete it. Learning-system access never submits work. Gmail and attendance are optional; attendance starts off. | All / 1–3, 10–11, 24–25 |
| STU-04 | **Manual timetable.** Save course code/name, meeting type, weekdays, start/end, building/room, instructor and term dates. Multiple meeting sections belong to one course. Review the exact values before saving; warn about class/shift overlaps. | 1 / 16–17 |
| STU-05 | **Schedule import.** Import recurring `.ics` schedules and extract screenshot/photo schedules into editable previews. Do not save uncertain extracted times or rooms without confirmation. Preserve dates, timezones, sections, locations and recurrence exceptions; refuse unsupported input honestly. | 1 / 2, 16–17 |
| STU-06 | **Timetable changes.** Edit a recurring meeting or one occurrence; change room/time, mark online, or cancel. Add/drop archives the course workspace instead of deleting its material. Resolve exceptions before answering or scheduling alerts. | 1 / 16–18 |
| STU-07 | **Trusted clock and class answers.** Server time plus a confirmed IANA timezone determines now, next, an explicit time, and a named day. Now uses start ≤ now < end. Respect term bounds, breaks, holidays and confirmed cancellations; distinguish no class from incomplete data. | 1 / 4–5, 16–18 |
| STU-08 | **Walking context.** Use reviewed building coordinates and a known starting building for travel estimates. State the estimate and unknowns. Do not imply current device location without consent. Next-class and leave-now alerts link to the correct occurrence. | 1 / 5, 16–18, 21, 25–26 |
| STU-09 | **Fixed commitments and study preferences.** Record recurring shifts, meals, sleep, focus hours, maximum study-block length (default 90 minutes), breaks and never-schedule hours. These constrain planning rather than becoming casual model suggestions. | 1 setup; 2 planning / 2, 14–16 |
| STU-10 | **Feasible daily plan.** Combine confirmed deadlines and remaining effort with fixed blocks and travel. Prioritize due time, remaining work and course weight; use focus hours, split large tasks across days and reserve 15% buffer. Show what cannot fit and offer a concrete move. Calendar writes require separate approval. | 2 / 14–16 |
| STU-11 | **Replanning.** Every plan block has Done, Took longer and Skip. Rebuild the remaining plan without moving fixed classes/shifts or claiming impossible capacity. Retain actual effort and student corrections for later estimates. | 2 / 15–16 |
| STU-12 | **Course workspace.** Keep overview, assignments, notes/files, linked emails, syllabus and course chat together. Course chat retrieves only authorized material from the selected course. Show meeting times, staff, office hours and grade breakdown when sourced. | 2 foundation; 4 learning tools / 20–21 |
| STU-13 | **Email to action.** With read permission, label Needs reply, Has deadline, FYI or Ignore; show why, link the course when confirmed, and propose deadlines for the plan. Support a student correction such as Not important. Never automatically reply to unknown senders. | 3 / 18–19 |
| STU-14 | **Exact reviewed actions.** Display recipient, subject and full body. Editing invalidates approval. Verify the result before saying Sent. Voice changes receive a new read-back and approval; a generic acknowledgment is not permission. Long drafts use on-screen review. | Existing foundation; 3 student flow / 5, 10–11, 18–20 |
| STU-15 | **Fall-behind recovery.** Accept a reported absence, overdue/underestimated task, or three skipped blocks. Retrieve only actual missed material, changed assignments and sourced late/extension policies. Offer a manageable multi-day catch-up plan and an editable extension draft. Never invent reasons or documentation. | 3 / 6, 19–20 |
| STU-16 | **Assignment help.** Explain prompts/rubrics, break work into timed steps, teach concepts and similar examples, quiz, brainstorm, outline and review student drafts. Check citations/formatting and export the student's own work as a clean PDF. | 4 / 20–21 |
| STU-17 | **Academic integrity.** Tutor/outline/review/format by default. Show the course's sourced AI policy; only follow a more permissive policy when explicitly supported. Decline assistance during a disclosed timed exam. No graded ghostwriting for submission as the student's own work. | All learning flows / 1, 11, 21, 37–38 |
| STU-18 | **Installable web app.** Provide an installable mobile web experience, relevant deep links and clear unsupported-browser behavior. Installation must not silently request notification or microphone permission. | 1 / 4, 21–23, 36 |
| STU-19 | **Useful alerts.** Deliver one-glance next-class (default 15 minutes), leave-now (walk + 3 minutes), due-soon (morning + 3 hours), shift (20 minutes), plan-block, email-action, missed-class, morning and evening alerts. Each opens the right screen; unsupported channels are not shown as delivered. | 1 class alerts; later phases for dependent types / 21–23 |
| STU-20 | **Notification control.** Independent type switches, quiet hours default 11 pm–7 am, daily cap default 12 and five-minute bundling. Respect timezone changes, pause, disabled types and deleted source events. Record delivery and explicit tap/Done outcomes without sensitive lock-screen detail. | 1 foundation; extend with types / 21–22 |
| STU-21 | **Wake-up alarm.** Support daily/per-day schedules, vibration/sound where supported, gradual volume, nine-minute snooze up to three times and dismissal into morning briefing. Suggest changes for early/cancelled classes, but let the student decide. A web reminder is not a reliable native alarm; use supported Clock shortcuts until native support exists. | 5 native dependency / 22–23 |
| STU-22 | **Semester goals and coach.** Save 1–3 editable academic, habit or personal goals. Ground morning encouragement in the actual day; evening celebrates real wins or resets tomorrow; weekly check-in reviews actual progress and asks how the student feels. Unknown progress needs a question, not a fabricated success. | 4 / 2, 23–24 |
| STU-23 | **Support without dependency.** Use specific, supportive language, no guilt/comparisons or diagnosis. Offer breaks and reviewed campus support. Crisis signals interrupt coaching and show verified country-appropriate help plus campus resources. Remain clear that NavoX is AI; no romantic-partner roleplay or encouragement to replace people. | All conversation; 4 coach / 7–11, 24, 37 |
| STU-24 | **Optional attendance.** Location processing stays on device and runs only during class. Ten minutes in the first half can mark attendance; send only attended/missed. Offer manual I'm here, let I was there override an inference, allow history deletion and never share with a school or professor. | 4; native geofences dependent on 5 / 3, 24–25 |
| STU-25 | **Degree consequences.** Read an uploaded audit or requirement list; distinguish done/in progress/remaining. Explain prerequisite, term, deadline and credit consequences with sources and uncertainty. Always refer final decisions, including aid/status implications, to the school/advisor. | 5 / 25–26 |
| STU-26 | **Campus guide.** Answer service/location/opening-time questions from a reviewed school profile. Show hours and source links, account for exceptions, and offer a plan addition for review. | 5 / 25–26 |

## Voice requirements

Voice and text use the same authorized student tools. The existing browser
capture, speech gateway, local wake detector and saved-answer playback are useful
foundations; they do not yet establish the following targets. All rows remain
**Planned** for the complete student behavior.

| ID | Acceptance criteria | Phase / plan pages |
| --- | --- | --- |
| VOI-01 | Quick asks work from a visible mic on every screen and laptop hold-space, with a typed alternative. A quick ask answers and closes listening. App-open Hey NavoX is explicit opt-in with a visible indicator; unsupported browsers have an honest fallback. Target under 1.5 seconds for the initial pipeline. | 1 / 3–5, 14 |
| VOI-02 | Handle general chat, learning, brainstorming, practice and stress as well as schedule questions. Offer a relevant planning action without forcing a school-command flow. Keep a warm, consistent personality, short answers, natural numbers, at most three spoken list items, and one question at a time. | 2–4 / 3–10 |
| VOI-03 | Stream transcription and speech for quick asks; add LiveKit Agents with a speech-to-speech provider for conversation, using the same tool boundary. Keep a cheaper pipeline fallback. Measure first audio under one second for streaming, median under 800 ms for conversation, with under 500 ms a goal. | 2–3 / 6, 12–14 |
| VOI-04 | Detect thought completion rather than every pause. Stop within 200 ms for an interruption, distinguish backchannels/background noise, and preserve only what was actually heard. Allow 10–20 seconds to think in study mode; use a gentle chat nudge around eight seconds and close a quiet conversation around ten seconds. | 3 / 6–9, 14 |
| VOI-05 | Read consequential actions back and require a clear, bound yes or tap. Re-read changed drafts; show long drafts for screen approval. Offer Undo where a reversal is actually supported, without claiming email can always be recalled. | 3 / 5–6, 10–11 |
| VOI-06 | Audio is processed and discarded; transcripts are off by default, opt-in and deletable. Warn before requested private information is read on speaker. Never identify/profile the student from voice. Opted-in transcript reviews are anonymized and revocable. | 1 privacy foundation; all voice / 10–11, 14 |
| VOI-07 | Offer quick ask, conversation, study/quiz, practice, wind-down and walking/driving behavior. Add 4–6 sampled voices, speed controls, multilingual practice and editable pronunciation for names/course codes/buildings. Mode changes must not expand permissions. | 4; native access in 5 / 7–10, 14 |
| VOI-08 | Test dining-hall/bus/wind noise, mumbling, accents, Bluetooth delay, echo cancellation and safe loudness. Use 200 consented recorded questions, randomized interruption tests and blind listening reviews; correct actions and clean interruptions each reach at least 95%. | All voice releases / 9–10, 14 |
| VOI-09 | Native earbud controls, lock-screen entry, alarm-to-briefing and background wake word respect OS limits and explicit microphone consent. Do not advertise these as web capabilities. | 5 / 4, 14, 22–23 |

## Learning-system connections

The default is a private calendar feed plus reviewed schedule/syllabus uploads.
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

## Architecture and data boundaries

The student request path remains web/voice/notification → TypeScript assistant
runtime → Python tools and connectors → PostgreSQL and Temporal. The change is
from a closed intent router to bounded tool selection and chaining, not a second
independent voice brain or an approval bypass.

| ID | Acceptance criteria | Stage / plan pages |
| --- | --- | --- |
| ARC-01 | A server-owned registry defines tools, schemas, budgets, permissions and approval requirements. Multi-step plans preserve user/workspace/course scope, fail honestly and do not execute arbitrary model-named endpoints. | Planned / 26–27 |
| ARC-02 | Model schools, owned courses and recurring meetings, fixed commitments, tasks/effort, plan-block outcomes, semester goals, editable facts, attendance results and notification outcomes. Existing execution plans and verification goals are not substitutes for these domains. | Planned / 27–28 |
| ARC-03 | Reuse source provenance, exact approvals, encrypted credentials and durable Temporal work. Recheck authority at read/write publication; use revisions/idempotency and bounded retries so deletion, cancellation or revocation wins over stale work. | Implemented foundation; student extensions planned / 1–2, 27, 34 |
| ARC-04 | Student memory is visible, editable and deletable, with explicit provenance and consent. The current saved-preference picker remains explicit; expanding automatic context needs an appropriate consent and provider contract. Do not silently send files or persistent personal context under a short-question conversation grant. | Implemented explicit-memory foundation; student extensions planned / 9, 28, 36 |
| ARC-05 | Keep least-privilege account/course isolation, data minimization, encryption, clear retention/deletion controls and a privacy policy covering student records. Legal/privacy review is separate from automated correctness tests; do not claim certification from a test pass. | Implemented foundation; student review planned / 1, 3, 11, 24–25, 37 |
| ARC-06 | Preserve gateway qualification, sensitivity and cost controls; add per-student usage/cost measurement before wider voice rollout. Adapter availability does not enable a provider or authorize a new personal-data route. | Implemented foundation; student budget planned / 14, 37–38 |
| ARC-07 | Keep News and subscription tracking behind student-product flags. Preserve existing accounts/data while removing unrelated paths from the student launch. Payments, browser automation, social features, school/professor sharing and graded submissions are out of launch scope. | Planned / 38 |

The current generic `.ics` upload is an encrypted immutable source import. It
rejects recurring events and does not create owned timetable meetings. Its
preview/digest pattern is reusable, but it is not proof of STU-05 or LMS-02.
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
| Voice use | Consenting active students using voice on at least three days/week; voice requests as a share of requests. | 50%; 40%+ |
| Voice quality | First-audio and stop latency on real devices; correct requested action and clean interruptions in the consented corpus; naturalness rating and conversations lasting more than two minutes. | Median <800 ms; 95%+ correct/clean |
| Voice cost | Actual attributable speech/model cost per active student per month, including failures/fallbacks. Establish a budget before opening access. | Plan example: <$1 |

For each release decision, keep the tested revision/configuration, consent
method, collection dates, cohort size, numerator/denominator, known failures and
decision together. Use aggregate results in repository evidence; private
student schedules, feed URLs, audio, transcripts, email and grades do not belong
in git. A gate remains unmet while its evidence is missing or its target fails.
