# Milestone 2: app shell and Today

Status: source review and implementation preparation; no M2 acceptance claimed.
Source of truth: Michael's `NavoX-UI-Redesign-Brief.pdf`, dated October 22, 2026,
14 pages (Library file `libfile_6ad06fb9f7e48191adef16516adf16a5`). Full extracted
text reviewed on October 24 UTC. Visually inspect the page-7 wireframe and the
supplied prototype/screenshots before implementing and comparing the interface;
text extraction alone does not establish a visual match. Michael's later explicit
decisions override the brief (including prototype styling inside Vox, Apple/iCloud
in Connections, and phone tests supplied by Michael).

Picovoice approval remains a separate M1 gate and must not block this milestone.
Preserve the authenticated app-scoped call controller and provider: navigation must
not remount the call or lose microphone/speaker state. Persistent saved memory is
milestone 4; do not represent the temporary active-call checkpoint as that feature.

## Build and acceptance checklist

### Shared styles and components (brief pages 3–5)

- [ ] Put named dark/light colors, type, spacing and radii in `packages/ui`.
  Dark: bg #0B0D10, surface #14171C, raised #1B1F26, border #262B33,
  text #F2F4F7, muted #A3ACB8, accent #4FD1B5, warning #F5B14C,
  danger #F07178, success #5BD38C. Light equivalents follow the brief.
- [ ] One Inter or Geist family; title 28/600, section 18/600, item 15/600,
  body 15/400, small 13/400. No text below 13 px or small-caps labels.
- [ ] Spacing 4/8/12/16/24/32/48 px; content max 1080 px; desktop side
  padding 32 px, phone 16 px; sections 24 px, list rows 12 px apart.
  Card radius 12, controls 8, round chips/Vox. Flat surfaces, no card shadows.
- [ ] Shared primary/secondary/quiet button, list row, grouping card, chip,
  empty state and confirmation toast with Undo where the action supports it.
  One filled primary action per screen; minimum 44 px tap targets on phones.
- [ ] Apply the eight user-selected course colors consistently by course.
- [ ] Verify normal text contrast >=4.5:1, keyboard focus, dark/light behavior,
  reduced motion, and no horizontal overflow at 375 px. Report any necessary
  deviation from supplied visual values instead of silently substituting.

### App shell (pages 5–6)

- [ ] Desktop sidebar 220 px: Today, Classes, Board, Planner, Inbox; profile
  photo/name at bottom, menu with Connections, Settings, Help, Sign out.
- [ ] Remove Assistant navigation and the workspace subtitle; preserve access
  to existing typed conversations through the Vox panel during migration.
- [ ] Flat 56 px header: page title left; search/Cmd+K and local date/time right.
  Remove duplicate Ask NavoX entry and separate clock card.
- [ ] Desktop 64 px round accent Vox button, fixed bottom-right; listening pulse.
  Open a persistent 400 px right panel with transcript and typed input;
  Cmd/Ctrl+J opens it; first-visit tooltip uses the brief's exact copy.
- [ ] Under 768 px: Today, Classes, large central Vox, Board, Planner bottom tabs;
  profile top-left, Inbox with genuine reply count top-right. Vox opens full
  screen with the approved visual, mute and hang-up controls.
- [ ] Keep the live call across route changes. Preserve ownership/logout gates,
  immediate capture stop, recovery, explicit approvals, usage notices and cleanup.
  All existing working actions remain reachable unless the brief removes them.
- [ ] Panel conversation: newest text at bottom, input/mic below; four exact
  empty-state suggestions; voice/privacy/preferences controls behind settings.
  Transcript rendering must respect off-record retention and clear on terminal
  cleanup; it must not manufacture cross-call history.

### Today (pages 6–8)

- [ ] One-line time-of-day greeting with the student's name, no subtitle.
- [ ] Next-up accent strip: next class, shift or meeting, countdown, supported
  leave-by time, details on tap; hide when no remaining event today. Do not invent
  travel estimates when data is absent. Follow the wireframe's done-for-today state.
- [ ] Wide chronological plan: classes, study blocks, meetings, shifts; course
  dots, past events muted, current event bold. Study blocks expose Done / Took
  longer / Skip using real existing actions and approval rules.
- [ ] Continue learning only for a real unfinished Board lesson.
- [ ] Narrow column: at most 3 due items within 3 days with Planner link;
  at most 3 emails needing action when connected; at most 3 real goal chips.
  Hide empty sections instead of placeholder cards.
- [ ] New-student setup: Add classes/import, Connect calendar, Add task,
  Try Vox. Derive completion from real state, show count/checkmarks, allow
  dismissal, disappear when complete; never mark a step complete from a click alone.
- [ ] Remove clock, renewals, daily focus, duplicate shortcuts, Activity,
  checked-at text and manual refresh buttons from Today. Auto-refresh and use
  one friendly retry line when needed. Do not erase their underlying user data.
- [ ] All display times use the student's timezone and the brief's relative
  wording. Test midnight/date boundaries and daylight saving, not host UTC.

## Boundaries and verification

Classes/Planner/Inbox/Connections/Settings redesigns remain milestone 5, calendar
integration and scheduling milestone 3, memory milestone 4, Noa part 2, Board part 3.
Their navigation moves with the M2 shell; this is not acceptance of those later
screens. Message limits (8,000 typed/4,000 spoken, shared contract/API match,
near-limit counter and file alternative) remain an explicit redesign requirement
and must be audited when moving typed chat into Vox. Do not drop it during the
shell migration.

Existing integration points: `workspace-shell.tsx`, `today-workspace.tsx`,
`student-plan-today.tsx`, `vox-call-provider.tsx`, `vox-call-controls.tsx`.
`packages/ui/src/index.ts` is currently a placeholder. Inspect the existing Today
API and action contracts before restructuring; reuse verified data and approvals.

Deliver a test link, desktop and mobile-emulation screenshots, acceptance checklist,
and deviations/spacing changes. Physical-phone testing and recording remain
waiting on Michael; student targets remain waiting on real usage. No merging on
a visual approximation or a test-only data fixture.
