# SPEC-008 M7 — cross-domain decomposition and synthesis

R3's next fast-track slice is multi-intent conversation. The current
SPEC-005 plan carries up to four routes but no per-intent question span; the
runtime deliberately clarifies compound plans rather than sending the whole
utterance to each service. Add a versioned planner artifact with a bounded
per-intent span copied exactly from the user's utterance. TypeScript must
verify each span and route before delegation. Keep historical planner
artifacts unchanged and never derive executable authority from a plan.

First establish current, scoped adapters for the missing scenario-H domains:
reuse the authenticated `/workspace/weather` endpoint, which already gets a
short-lived public city forecast from Open-Meteo when the user's city is
enabled, and resolve next class through the existing connected
Calendar/course service. Current time should be a direct TypeScript utility.
Do not fabricate weather when the workspace preference is disabled or the
source is unavailable, or a class record from Today text. Keep AI access
behind SPEC-005. A missing live source produces a qualified unavailable
sub-result.

Then resolve each read-only intent against its owning SPEC service using the
validated sub-question and current session scope. Bound fan-out to four,
recheck account scope between calls, retain per-domain evidence/currentness,
and synthesize only returned facts with clear partial-failure labels. A
consequential sub-intent must not execute in this phase; it needs the existing
SPEC-001/003 approval workflow and explicit target binding. Do not allow a
compound utterance to bypass approval or turn an ambiguous referent into an
action.

Tests need weather + next class + Today + News decomposition, per-intent
source outage, account switching, stale source data, follow-up reference
binding, voice/text continuity and zero unauthorized actions. Run the full
API gate for the planner schema, root TypeScript/Web checks, PostgreSQL
integration and live authorized acceptance only when sources are configured.
