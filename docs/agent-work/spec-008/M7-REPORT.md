# SPEC-008 M7 — current weather and bounded compound reads

Status: local implementation in progress, uncommitted and unpushed. The M7
database-backed API gate failed strict mypy on two new schema typing errors;
the full tests and evaluation passed. The type errors were corrected in M8,
whose final-tree gate is recorded in `M8-REPORT.md`.

The TypeScript runtime now asks SPEC-005 to plan before calling Today. This
prevents SPEC-002's keyword classifier from taking a weather question merely
because it says “today,” or a named subscription lookup because it says
“renew.” If no qualified planner is available, only an exact SPEC-002
supported query template can use Today; other requests fail as unavailable.

The planner's v5 schema adds an exact, ordered question span to each intent.
Python validates spans before accepting the model output; TypeScript validates
them again against the operator's original utterance. Earlier v1-v4 prompt
and schema definitions remain immutable. Compound plans still carry no action
grant. The runtime preflights all intents and clarifies a mixed unsafe plan
without making any read, then executes up to four read-only routes in order,
rechecks account scope between them, and labels partial source failures. An
account switch withholds prior results rather than publishing a partial answer.

Weather reuses the existing authenticated SPEC-002 `/workspace/weather`
observation. The runtime checks source status, units, currentness, temperature
range, and the requested city against the leading city in the configured
location; a region substring alone cannot pass. Disabled preferences ask the
user to choose a city; source outage and stale data do not become an answer.
No AI provider SDK is imported by NavoXbot, and no live weather/provider call
was made in this local test slice.

The Web transcript links SPEC-006 story items to the signed-in NavoX story
page and validated HTTPS claim citations to their publisher source. Connected
email and Calendar navigation still need exact authorized resource resolution;
raw selectors are never treated as navigation permission.

Focused planner tests pass 16/16. The assistant runtime passes 193 local tests
with five database tests skipped, and 198/198 against disposable PostgreSQL;
its migration check passes 29 assertions. The root Web/runtime/extension tests
and production builds passed on the M7 tree before the final persistence and
navigation updates; rerun the root JavaScript gate on the final tree. The full API
gate and live acceptance remain at this slice. Current time, authoritative next class,
four-domain synthesis, streaming voice and wake-word behavior, activity ledger,
Temporal goals/workflows, and broader security/quality thresholds are not yet
implemented or accepted.
