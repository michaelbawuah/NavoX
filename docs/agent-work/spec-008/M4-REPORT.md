# SPEC-008 M4 — read-only meeting preparation

Status: local TypeScript slice on `spec-008-navoxbot`, uncommitted and unpushed.
The existing SPEC-002 Today service still decides whether the user's question
is meeting preparation. For that intent only, the assistant reads the existing
authenticated SPEC-002 Proactive meeting-prep endpoint and validates its
bounded structured result. The runtime requires the same meeting ID, title and
start time in both service responses before adding a `MEETING_BRIEFING` block.
An absent meeting keeps Today's no-meeting answer; changed or unavailable
source data becomes a qualified `UNAVAILABLE` turn. It does not generate
meeting facts, call an AI provider, or perform an action.

The shared TypeScript contract and presentation validator now carry title,
start time, minutes until start, description, prep points and related saved
commitments. The `/navox` UI displays this detail in the same text/voice
session. Tests cover gateway cookie forwarding, response bounds, matching and
mismatching service snapshots, source failure and structured rendering.

On the combined M3C/M4 tree, root lint/typecheck/tests passed: 281 Web tests,
165 runtime tests (five DB-only skips because disposable PostgreSQL was down),
and eight extension tests. Web production build passed. Existing Web lint
warnings remain; no new lint error. No Python code, migration, live Calendar
connector, model provider or paid call was added.

Still open: an event-specific meeting request beyond the next saved meeting,
live authorized Calendar/course data, relevant SPEC-007 communications and
documents, navigation, voice acceptance and broader SPEC-008 scenarios. This
slice does not claim a complete meeting briefing or full SPEC-008 acceptance.
