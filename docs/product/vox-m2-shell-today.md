# Milestone 2: app shell and Today

Status: M2 implementation deployed to the test site and browser-verified.
Final acceptance remains open for Michael’s physical-phone test and recording.
Checks below cover the M2 shell, Today and call panel, not the later page redesigns.
Source of truth: Michael's `NavoX-UI-Redesign-Brief.pdf`, dated October 22, 2026,
14 pages (Library file `libfile_6ad06fb9f7e48191adef16516adf16a5`). Full extracted
text and rendered page-7 Today wireframe reviewed on October 24 UTC. Noa artwork
acceptance remains separate; text extraction alone does not establish a visual match. Michael's later explicit
decisions override the brief (including prototype styling inside Vox, Apple/iCloud
in Connections, and phone tests supplied by Michael).

Picovoice approval remains a separate M1 gate and must not block this milestone.
Preserve the authenticated app-scoped call controller and provider: navigation must
not remount the call or lose microphone/speaker state. Persistent saved memory is
milestone 4; do not represent the temporary active-call checkpoint as that feature.

## Build and acceptance checklist

### Shared styles and components (brief pages 3–5)

- [x] Put named dark/light colors, type, spacing and radii in `packages/ui`.
  Dark: bg #0B0D10, surface #14171C, raised #1B1F26, border #262B33,
  text #F2F4F7, muted #A3ACB8, accent #4FD1B5, warning #F5B14C,
  danger #F07178, success #5BD38C. Light equivalents follow the brief.
- [x] One Inter or Geist family; title 28/600, section 18/600, item 15/600,
  body 15/400, small 13/400. No text below 13 px or small-caps labels.
- [x] Spacing 4/8/12/16/24/32/48 px; content max 1080 px; desktop side
  padding 32 px, phone 16 px; sections 24 px, list rows 12 px apart.
  Card radius 12, controls 8, round chips/Vox. Flat surfaces, no card shadows.
- [x] Shared primary/secondary/quiet button, list row, grouping card, chip,
  empty state and confirmation toast with Undo where the action supports it.
  One filled primary action per screen; minimum 44 px tap targets on phones.
- [x] Apply the eight user-selected course colors consistently by course.
- [x] Verify normal text contrast >=4.5:1, keyboard focus, dark/light behavior,
  reduced motion, and no horizontal overflow at 375 px. Report any necessary
  deviation from supplied visual values instead of silently substituting.

### App shell (pages 5–6)

- [x] Desktop sidebar 220 px: Today, Classes, Board, Planner, Inbox; profile
  photo/name at bottom, menu with Connections, Settings, Help, Sign out.
- [x] Remove Assistant navigation and the workspace subtitle; preserve access
  to existing typed conversations through the Vox panel during migration.
- [x] Flat 56 px header: page title left; search/Cmd+K and local date/time right.
  Remove duplicate Ask NavoX entry and separate clock card.
- [x] Desktop 64 px round accent Vox button, fixed bottom-right; listening pulse.
  Open a persistent 400 px right panel with transcript and typed input;
  Cmd/Ctrl+J opens it; first-visit tooltip uses the brief's exact copy.
- [x] Under 768 px: Today, Classes, large central Vox, Board, Planner bottom tabs;
  profile top-left, Inbox with genuine reply count top-right. Vox opens full
  screen with the approved visual, mute and hang-up controls.
- [x] Keep the live call across route changes. Preserve ownership/logout gates,
  immediate capture stop, recovery, explicit approvals, usage notices and cleanup.
  All existing working actions remain reachable unless the brief removes them.
- [x] Panel conversation: newest text at bottom, input/mic below; four exact
  empty-state suggestions; voice/privacy/preferences controls behind settings.
  Transcript rendering must respect off-record retention and clear on terminal
  cleanup; it must not manufacture cross-call history.

### Today (pages 6–8)

- [x] One-line time-of-day greeting with the student's name, no subtitle.
- [x] Next-up accent strip: next class, shift or meeting, countdown, supported
  leave-by time, details on tap; hide when no remaining event today. Do not invent
  travel estimates when data is absent. Follow the wireframe's done-for-today state.
- [x] Wide chronological plan: classes, study blocks, meetings, shifts; course
  dots, past events muted, current event bold. Study blocks expose Done / Took
  longer / Skip using real existing actions and approval rules.
- [x] Continue learning only for a real unfinished Board lesson.
- [x] Narrow column: at most 3 due items within 3 days with Planner link;
  at most 3 emails needing action when connected; at most 3 real goal chips.
  Hide empty sections instead of placeholder cards.
- [x] New-student setup: Add classes/import, Connect calendar, Add task,
  Try Vox. Derive completion from real state, show count/checkmarks, allow
  dismissal, disappear when complete; never mark a step complete from a click alone.
- [x] Remove clock, renewals, daily focus, duplicate shortcuts, Activity,
  checked-at text and manual refresh buttons from Today. Auto-refresh and use
  one friendly retry line when needed. Do not erase their underlying user data.
- [x] All display times use the student's timezone and the brief's relative
  wording. Test midnight/date boundaries and daylight saving, not host UTC.

## Boundaries and verification

Classes/Planner/Inbox/Connections/Settings redesigns remain milestone 5, calendar
integration and scheduling milestone 3, memory milestone 4, Noa part 2, Board part 3.
Their navigation moves with the M2 shell; this is not acceptance of those later
screens. Message limits (8,000 typed/4,000 spoken, shared contract/API match,
near-limit counter and file alternative) are implemented in the active-call
panel. Legacy standalone forms are listed explicitly under later migrations.

Existing integration points: `workspace-shell.tsx`, `today-workspace.tsx`,
`student-plan-today.tsx`, `vox-call-provider.tsx`, `vox-call-controls.tsx`.
The shared `packages/ui` now provides tokens and basic components. Today reuses
existing schedule, planning, goals, saved Board and Today APIs.

Deliver a test link, desktop and mobile-emulation screenshots, acceptance checklist,
and deviations/spacing changes. Physical-phone testing and recording remain
waiting on Michael; student targets remain waiting on real usage. No merging on
a visual approximation or a test-only data fixture.

## Verified implementation and acceptance boundary

Test deployment: https://vox-test.66-175-235-169.nip.io/ (application `c0bc9c1`).
Production and main have not been changed. The stacked draft is PR #57.

The panel reads only the authenticated session’s current encrypted call checkpoint.
Spoken and typed turns share one worker, context, provider route and usage ledger.
A new sign-in, different owner, expired generation and ended call cannot read or
write that conversation. The browser clears transcript and draft on terminal state.
A compacted old conversation is explicitly labeled as a summary.

Typed messages accept 8,000 Unicode characters; active voice input stays at 4,000.
The composer shows a counter above 7,000, grows to eight lines and preserves long
pastes. An over-limit paste can be downloaded intact or saved through the existing
private course-material upload pipeline, with an explicit class selection. The UI
explains that saved files persist after hang-up. File saving is not automatic and
does not silently submit the document as a spoken question. Older standalone
assistant forms retain their existing limits until their later migration; they are
not evidence that every legacy surface has been redesigned.

Done / Took longer / Skip now work inside Today. Preview runs the exact planner
inside a transaction that is rolled back, lists every replaced and proposed study
block, reports remaining work and leaves calendar events/alarms alone. Approval
recomputes a fingerprint of the reviewed inputs and source state under the owner
lock. A changed plan requires another preview; retries use the same command ID.
The saved-progress toast offers Undo for the existing five-minute window. Undo
also lists every block it removes/restores and needs explicit approval; changed
plans are rejected by the existing revision/source checks. Existing Planner
behavior is preserved. No calendar write was added in M2.

The reply badge uses the same connected-Gmail action projection on every shell
screen, respects the student’s timezone, and ignores late responses from another
account. Appearance is account-scoped; dark/light selection is in Settings.
Next-event details expand in place. Relative evening deadlines say “tonight”.

See [verification, screenshots and phone checklist](evidence/m2-shell-today/README.md).
Automated/browser checks do not substitute for physical-phone or real-usage checks.

## Explicit differences and later-stage checks

- Light active navigation text is #087B68 instead of #0F9F86: 4.97:1 contrast
  against #FAFAF9 instead of 3.18:1. The accent fill remains #0F9F86.
- Small controls retain at least 44 px touch targets. The page title now uses the
  specified 28 px on desktop and phone; the 56 px phone header reserves a second
  line for date/time. The Vox button stays 64 px.
- An account without an available profile photo uses initials. A photo upload UI
  is not part of this milestone’s Settings addition.
- Vox retains the existing call artwork/palette. Noa’s exact prototype, seven
  states and rewards remain Part 2, in the agreed build order.
- Board navigation/resume respects its existing release flag. A live saved-lesson
  resume check remains with the enabled Board release in Part 3.
- Classes/Planner/Inbox/Connections/Settings content keeps its working behavior;
  their complete redesign and cross-screen course-color migration remain M5.
- Completing all four setup steps against the real calendar connection remains
  an integration acceptance check for M3. A disconnected step is never marked done.
- A real connected personal mailbox was not used for these screenshots. The badge
  is covered by projection/account-isolation tests; live mailbox acceptance remains
  with Connections/Inbox integration testing.

## Final sign-off still required

- [ ] Michael’s physical-phone browser test and recording.
- [ ] Michael’s visual review against the supplied references.
- [ ] Real-student usability/usage targets: waiting on real usage.
- [ ] Full M2 acceptance: do not label the milestone accepted before phone and visual acceptance.

Picovoice licensing is still a separate M1 gate and does not hold up this preview.
