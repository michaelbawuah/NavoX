# M11A — SPEC-005 audio catalog qualification foundation

## Result and architectural decisions

Extend the existing SPEC-005 registry with explicit transcription and speech
synthesis task/profile/capability vocabulary and a fail-closed audio-model
eligibility selector. This is the dependency for provider-backed voice; it does
not add a provider request or HTTP endpoint. Keep audio work in the existing
Python SPEC-005 service because that is the already deployed provider boundary.
NavoXbot runtime, transport and UI remain TypeScript.

No current text/embedding registration, credential, or `PERSONAL` grant may
qualify a speech task. Voice input and spoken answers are `SENSITIVE` for this
phase; the candidate model and every operator/workspace/user policy must permit
that class. Audio profiles and their assignment rollout start absent/zero until
explicit operator publication and evaluation. No automatic fallback/shadow.

## Exact contract

- Add `TRANSCRIPTION` and `SPEECH_SYNTHESIS` capabilities,
  `TRANSCRIBE` and `SYNTHESIZE` task types, and `SPEECH_TRANSCRIPTION` and
  `SPEECH_SYNTHESIS` profiles to `foundation/contracts.py`.
- Add optional positive `transcription_cost_per_minute` and
  `synthesis_cost_per_1000_characters` to `ModelDefinition`. **Preserve exact
  canonical JSON of historical snapshots**: these new fields must be omitted
  from serialization when unset (Pydantic `Field(exclude_if=...)` works in the
  installed 2.13.5). A regression test must load/validate/canonicalize an
  actual old-style snapshot without adding keys or breaking digest checks.
  Do not rename existing text pricing fields.
- Add a pure `reserve_speech_cost(model, task_type, units)` helper with
  `Decimal` arithmetic: transcription input duration is measured in
  milliseconds, charged proportionally to a minute; synthesis input is a
  character count, charged per 1000 characters. Reject nonpositive units,
  missing/wrong-mode prices and non-audio task types. Never use token pricing
  for audio. Selection must reject a reservation above the request and current
  operator/workspace/user cost ceilings.
- Add `rank_eligible_speech` (or equivalently named pure selector) consuming a
  validated `AITask`, current `RoutingSnapshot`, bounded units and current
  time. It must bind exact published profile/prompt/schema versions, require
  the matching audio capability, enabled model and assignment, live rollout/
  health availability, exact fresh task/profile/prompt/schema evaluation with
  existing SPEC-005 quality/reliability/safety thresholds, `SENSITIVE` model
  allowance and effective provider grants, and sufficient budget. Return
  deterministic candidate order and reservation; no side effects or provider
  call. A text-only model or old text evaluation cannot qualify.
- Keep new profiles absent from the default catalog; adding enum vocabulary
  alone must not enable traffic. No new language, package, migration, provider
  request, API route, publication, live evaluation, or paid call in M11A.

## Ownership and verification

Own `services/api/navox/ai/foundation/contracts.py`,
`services/api/navox/ai/foundation/registry.py`, and a new
`services/api/navox/ai/speech_routing.py`, plus focused tests under
`services/api/tests/` and your unique report
`docs/agent-work/spec-008/M11A-AUDIO-WORKER-REPORT.md`. If an existing small
helper must change, name and justify it in the report. Do not touch Web/TS,
API routes, settings, migrations, current catalog publication or checkpoint.
The tree is dirty with earlier accepted work. You are not alone: preserve all
existing and unrelated changes and never revert another agent's edits.

Test canonical compatibility, positive cost arithmetic, every default-deny
gate above, task/policy/model/evaluation mismatch, stale evidence, budget and
deterministic ordering. Run targeted pytest, Ruff lint/format check and strict
mypy on the affected Python tree; fix in-scope failures. Full API gate runs at
the M11 integration boundary. No commit, push, deploy or provider setup test.
