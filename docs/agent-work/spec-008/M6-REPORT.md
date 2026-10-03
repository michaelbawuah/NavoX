# SPEC-008 M6 — read-only News answers, no X

Status: local read-only implementation on `spec-008-navoxbot`, uncommitted
and unpushed. The final combined API gate is running with the M7 planner.

The existing SPEC-005 planner registers `assistant_intent_plan@v3` with a
read-only `news.read` route; v1 and v2 remain registered unchanged, and the
newer v4/v5 artifacts preserve this historical definition. The
TypeScript runtime verifies a named entity against the user's own words and
reads authenticated SPEC-006 trending stories. A generic trend request shows
observed-activity ranking and each story's verification label. A named request
requires exactly one current matching story, rechecks account scope, rereads
the story detail, and accepts only a READY source-backed summary whose
attributions and claim URLs match the current story sources.

The answer labels the publisher headline as attributed, carries each
displayed fact's own verification status and citation selector, and never
treats trending rank as proof. Empty, ambiguous, stale, pending, malformed or
unavailable data produces clarification or qualified unavailability. The
runtime performs no action and has no X route or API dependency. Long source
text is bounded to the shared presentation contract with explicit truncation.

Focused Python planner and TypeScript runtime/UI tests pass. Root JavaScript
checks and the Web/extension builds passed on the M7 tree; database-backed
assistant tests pass 198/198. Record the full API result before marking
this a local checkpoint. Live News/source observation, broader scenario E and
hosted CI on an exact published head remain open. No catalog publication,
paid provider call or external account setup occurred.
