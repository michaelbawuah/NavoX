# SPEC-008 M9-TIME worker report - direct current-time capability

STATUS: ready_for_review

Task: `/root/spec008_time_direct_retry` (brief `docs/agent-work/spec-008/M9-TIME-BRIEF.md`)
Workspace: `/Users/mba_steins/NavoX`, branch `spec-008-navoxbot`, baseline HEAD `bb0a7c9`.
Role: implementation worker only. No delegation, commit, push, deploy, or live/paid provider call.

## Changed paths and behavior

Shared contract

- `packages/contracts/src/assistant.ts`: added `time.now` to `AssistantCapabilityId` and `AssistantIntentKind`.

TypeScript runtime (`packages/assistant-runtime`)

- `src/time.ts` (new): `answerCurrentTime(now, timezone)`. Reads only the runtime's injected clock. Uses the validated IANA zone when it is usable; otherwise answers from UTC with an explicit "missing or invalid ... UTC fallback ... not necessarily your local time" label. The decision is always `DELEGATE`/`time.now`/`runtime.clock` with `requires_approval: false` and `action_state: "NONE"`, and the only block is an `ANSWER`. No class, meeting or reminder time is ever converted into the current time.
- `src/capabilities.ts`: registered `time.now` as a read-only capability whose `delegate_target` is `runtime.clock` (a label, not a network path) and mapped the route to it.
- `src/validate.ts`: added `time.now` to `CAPABILITY_IDS` and `INTENT_KINDS`, so an unknown route or capability remains rejected at the boundary.
- `src/runtime.ts`: resolves `time.now` immediately after the local Today block, before the account/scope recheck and before any upstream delegation, so the route never reaches another service. Added the `Time` label used by compound answers.
- `src/index.ts`: exports the new module.

SPEC-005 planner (`services/api/navox/ai`)

- `navox/ai/intent_plan.py`: added `IntentRoute.TIME_NOW = "time.now"`; added `INTENT_PLAN_INSTRUCTIONS_V6` (the previous `class.next` instructions) and a new active instruction block that adds the `time.now` route and forbids resolving a clock reading or reusing a class/meeting time. `INTENT_PLAN_PROMPT`/`INTENT_PLAN_SCHEMA` now point at `assistant_intent_plan@v7`. `intent_plan_json_schema` gained `legacy_v6`; every legacy artifact v1-v6 excludes `time.now` (v6 retains `class.next`), and only v7 includes it. The plan JSON `version` stays `1`.
- `navox/ai/prompts.py`: registers the immutable `assistant_intent_plan@v6` prompt/schema and publishes `@v7` as the current artifact. No historical artifact was rewritten.

Tests

- `packages/assistant-runtime/src/time.test.ts` (new): deterministic injected-clock answer in `America/New_York` (12:00Z -> 8:00 AM), UTC fallback for `null`, empty, unknown and padded zones, explicit `UTC` staying `time.now.local`, and no approval/action/inferred-schedule surface.
- `packages/assistant-runtime/src/runtime.test.ts`: single `time.now` turn answering from the injected clock with no today/weather/class/news/email fetch; UTC-fallback turn; compound "What time is it, and what class do I have next?" turn that keeps exact ordered spans, answers both parts in one turn, labels them `Time:`/`Next class:`, and takes the class answer from the class projection rather than the clock.
- `packages/assistant-runtime/src/planner.test.ts`: `time.now` is accepted in the current plan version and maps to the `time.now` capability; unknown routes/fields stay rejected.
- `packages/assistant-runtime/src/capabilities.test.ts`: registry list, length (7), `runtime.clock` target and read-only/no-approval invariants.
- `services/api/tests/test_ai_intent_plan.py`: v7 catalog artifact carries `time.now`; v6 schema keeps `class.next` and excludes `time.now`; v6 prompt does not mention `time.now`; a dedicated regression test walks all six historical route enums (v1-v6) and asserts `time.now` is absent while v7 has it and v6 keeps `class.next`; `IntentPlan` accepts `time.now` and still rejects an unregistered route (`clock.now`); the audited task run now records `assistant_intent_plan@v7`.

## Review correction (round 1)

Astra found that `intent_plan_json_schema` only added `IntentRoute.TIME_NOW` to the v6 exclusion set, so the v1-v5 artifacts fell through the `else set()` branch and inherited the new route, mutating published historical schemas. Fixed by excluding `TIME_NOW` for every historical version (`legacy_v1 or ... or legacy_v6`) while leaving the existing v1-v5 route exclusions and the v5+ `question` field rule untouched.

Regression evidence, from the same working tree:

- With the original `if legacy_v6:` condition restored temporarily, the new test failed: `AssertionError: v1` / `assert 'time.now' not in ['today.read', 'email.search', 'time.now', 'assistant.clarify']`. The condition was then restored to the fixed form.
- With the fix in place the route enums read: v1 `[today.read, email.search]`, v2 `[+subscription.search]`, v3 `[+news.read]`, v4 and v5 `[+weather.read]`, v6 `[+class.next]`, v7 `[+time.now]`, all plus `assistant.clarify`; only v7 contains `time.now` and only v6 and v7 contain `class.next`.

## Verification

All commands were run from the working tree above.

| Check | Command | Exit | Result |
| --- | --- | --- | --- |
| Runtime package tests | `npx vitest run` in `packages/assistant-runtime` | 0 | 21 files passed / 1 skipped; 212 tests passed / 5 skipped |
| Python planner tests | `UV_CACHE_DIR=/private/tmp/navox-uv-cache uv run --no-sync python -m pytest tests/test_ai_intent_plan.py -q` in `services/api` | 0 | 17 passed in 2.02s |
| Focused AI set | same runner, `tests/test_ai_intent_plan.py tests/test_ai_registry.py tests/test_ai_readiness.py tests/test_ai_settings.py -q` | 0 | 34 passed in 5.50s |
| Full API suite | `... python -m pytest -q -p no:randomly` in `services/api` | 0 | 2578 passed, 8 skipped in 299.71s |
| Ruff lint | `... python -m ruff check navox/ai/intent_plan.py navox/ai/prompts.py tests/test_ai_intent_plan.py` | 0 | All checks passed |
| Ruff format check | `... python -m ruff format --check` (same files, after `ruff format` applied to the test file) | 0 | 3 files already formatted |
| Strict mypy | `... python -m mypy navox` | 0 | Success: no issues found in 277 source files |
| Root typecheck | `npm run typecheck` | 0 | All 6 workspaces (contracts, ui, connector-sdk, assistant-runtime, web, extension) clean |
| Root lint | `npm run lint` | 0 | Biome clean for assistant-runtime (52 files); web reports 10 pre-existing warnings in another worker's files (`navox-assistant.module.css`, `assistant-controller.ts`) |
| Root tests | `npm test` | 0 | web 287 passed (37 files); assistant-runtime 212 passed / 5 skipped; extension checks pass |

Notes on the environment: the default uv cache (`~/.cache/uv`) is outside the sandbox and returned `EPERM`, so every `uv run` used `UV_CACHE_DIR=/private/tmp/navox-uv-cache` with the committed lock and `--no-sync`. The full API suite ran on the default sqlite DSN, so DB/PostgreSQL-only cases are the 8 skips; no PostgreSQL/Temporal run was made for this slice. A direct probe confirmed the route enums: v6 = `[... class.next, assistant.clarify]`, v7 = `[... class.next, time.now, assistant.clarify]`.

## Outstanding risks

- Provider-side enforcement was not exercised. A deployment whose published operator catalog still points at `@v6` will not emit `time.now`; catalog publication is an explicit operator action and was not performed. No model or live provider call was made, so real-utterance planner behavior is unverified.
- The UTC fallback relies on Node `Intl` with full ICU data in the assistant runtime, as the existing class/weather clock formatting already does.
- `time.ts` re-validates the zone locally instead of importing `isIanaTimezone`, because a persisted or older turn can carry a value that would not have to pass today's request boundary; the two implementations intentionally agree.

## Decisions for Astra

Both design choices raised in the first review were accepted by Astra: the local `runtime.clock` delegate-target label for a route that is answered without delegation, and keeping the plan JSON `version` field at `1` with `assistant_intent_plan@v7` as the versioned artifact. No open questions remain in this slice.

## Next checkpoint

Nothing is unfinished in this slice. Astra review would decide whether to republish the operator catalog with `assistant_intent_plan@v7` and whether a final combined gate on the merged tree is warranted before any push.
