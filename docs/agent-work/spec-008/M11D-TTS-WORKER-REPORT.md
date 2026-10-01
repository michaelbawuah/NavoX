# M11D saved-turn provider speech — worker report

**STATUS: ready_for_review**

Task `/root/spec008_tts`. Workspace `/Users/mba_steins/NavoX`, branch
`spec-008-navoxbot`, baseline `262070da0230d189f56e79d9d42fb55b1cc59b33`. The
shared tree was already dirty with accepted SPEC-006/007 and SPEC-008 M1–M11C
work; every pre-existing change (including the untracked
`docs/agent-work/spec-006-*` evidence directories) was preserved. No commit,
push, deploy, publication, live/paid provider call, secret or grant change, or
production migration was made.

## What changed

`AssistantResponse → Response Modality Resolver → SPEC-005 TTS → speaker` is now
complete for one saved assistant turn. The user hears the same bounded answer
`/navox` displays, and a failed or unqualified speech provider leaves the typed
answer usable.

| Path | Change |
| --- | --- |
| `services/api/navox/ai/speech_adapters.py` | Added the provider-neutral `SpeechSynthesisAdapter` protocol and one OpenAI `/v1/audio/speech` implementation on the existing `OpenAISpeechAdapter`: fixed HTTPS destination, JSON body (`model`, `input`, `voice`, `response_format=mp3`), no redirects, bounded timeout, streamed ≤2 MiB cap, `audio/mpeg` content-type check, non-empty body, and fixed error classification. `capabilities()` is unchanged (transcription-only) so the M11B selection gate is not widened; synthesis uses the separate protocol. |
| `services/api/navox/ai/speech_synthesis.py` (new) | Bounded speech-text contract (blank/over 600 characters refused, never truncated), the fixed SENSITIVE `SYNTHESIZE`/`SPEECH_SYNTHESIS` task with `speech_synthesis_prompt@v1` / `speech_synthesis@v1`, no context refs / fallback / shadow, one fixed server voice, M11A `rank_eligible_speech` selection with a synthesis-capable adapter, character-based `Decimal` reservation, under-lock quota admission, content-free trace, bounded provider call, and post-call re-authorization that discards the audio on any authority/catalog change. |
| `services/api/navox/ai/store.py` | `admit_transcription` refactored into a shared private `_admit_audio_attempt`; added `admit_synthesis` with identical `FOR UPDATE` user-row locking, membership/pause re-read and rolling-window count/insert semantics. `admit_transcription` behavior is unchanged. |
| `services/api/navox/api/ai_operations.py` | Added `POST /api/v1/ai/assistant/speech/synthesize` (`{text}` JSON only) with the same-origin guard and one raw `audio/mpeg` response carrying `Cache-Control: no-store`. |
| `services/api/tests/test_ai_speech_synthesis.py` (new) | 39 focused tests. |
| `apps/web/src/app/api/v1/assistant/sessions/[sessionId]/turns/[turnId]/speech/route.ts` (new) | The saved-turn Next route. Same-origin JSON mutation guard, session cookie, `readSession` ownership proof, exact saved turn located by id, bounded text derived on the server from the saved presentation, then one fixed authenticated 30 s `POST /ai/assistant/speech/synthesize`. Returns bounded `audio/mpeg` with `no-store`, propagates the caller abort to the upstream request, and maps every failure to bounded typed copy without echoing an upstream body. |
| `apps/web/src/lib/assistant-client.ts` | Added `assistantTurnPath`, `MAX_SPEECH_AUDIO_BYTES`, and `synthesizeAssistantSpeech(sessionId, turnId, signal)` which sends `{}` as the body (selectors only) and accepts only non-empty `audio/mpeg` at or under 2 MiB. |
| `apps/web/src/lib/assistant-speech.ts` | Replaced the browser `speechSynthesis` playback with `speak(turnId, handlers, synthesize)`: one injected session-scoped fetch, one `Blob`/object URL, one `Audio` element, and an abort controller. Stop, mute, clear, unmount and a newer turn abort the fetch, detach element handlers, pause playback and revoke the object URL; a late callback cannot report into a settled session. `synthesisSupported` now requires `Audio` + `Blob` + `URL.createObjectURL`/`revokeObjectURL`. |
| `apps/web/src/lib/assistant-controller.ts` | `VoiceSessionDeps` gains `synthesize`; `speakAutomatic`/`speakManually`/`startSpeech` carry the saved turn selector instead of answer text; `speechTextForTurn` is now the shared server/client derivation re-exported from the runtime, so the Read aloud control is offered exactly when the server can speak. |
| `apps/web/src/components/navox-assistant.tsx` | Binds the live session to `synthesizeAssistantSpeech` and passes the saved turn to `speakAutomatic`/`speakManually`; the Read aloud control no longer sends text. |
| `packages/assistant-runtime/src/voice.ts` | Added `MAX_SPEECH_LENGTH` and pure `spokenTextForTurn`: the bounded spoken text of one saved turn, refused for approval-required answers, non-READY/CLARIFY turns and answerless turns. This is the single source of truth the Next route and the client both use. |
| `apps/web/src/lib/assistant-synthesis-route.test.ts` (new), `assistant-client.test.ts`, `assistant-speech.test.ts`, `assistant-controller.test.ts`, `packages/assistant-runtime/src/voice.test.ts` | New and updated coverage (see below). |

### Next route contract

`POST /api/v1/assistant/sessions/<sessionId>/turns/<turnId>/speech` takes an
empty JSON body. The browser supplies session and turn selectors only: no speech
text, provider, model, voice, destination, grant or approval field exists in the
request. `assertSameOriginMutation` runs first, then `readSession` proves the
caller owns the session. The exact saved turn is matched by id (an unknown
selector is a 404 before any upstream request). `spokenTextForTurn` derives the
bounded text from the saved presentation; an approval-required turn is refused
with 403 and never reaches the provider. The fixed upstream destination is
server configuration, the request carries the session cookie, and the response
must be non-empty `audio/mpeg` within 2 MiB.

### SPEC-005 route contract

`POST /api/v1/ai/assistant/speech/synthesize` accepts `{"text": string}` only.
The caller cannot supply a model, provider, voice, policy, sensitivity, prompt
or task field. Execution reuses `authorize`/`snapshot`, M11A
`rank_eligible_speech(task, …, units=len(text))`, `claim`, `health` and
`AITaskRun`; exactly one synthesis-capable adapter is selected, with no
fallback or shadow. The selection's character-based `Decimal` reservation is
written as `reserved_cost`/`estimated_cost`, and the trace carries only
task/profile/prompt/schema, provider/model, registry revision, status, latency,
cost and the fixed error code — never the spoken text or the audio. After the
call the service re-runs `authorize` and re-snapshots; a revoked account (403)
or changed grant/catalog/budget (503) discards the audio. Upstream cost may
still have been incurred, and the caller fails closed.

## Verification

All Python commands ran from `services/api` with
`UV_CACHE_DIR=/tmp/navox-m11d-uv-cache` and the repository's existing `.venv`.

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --no-sync python -m pytest -q tests/test_ai_speech_synthesis.py` | 0 | 39 passed |
| `uv run --no-sync python -m pytest -q tests/test_ai_speech_synthesis.py tests/test_ai_speech_transcription.py tests/test_ai_speech_routing.py tests/test_ai_runtime.py tests/test_ai_gateway_foundation.py` | 0 | 284 passed |
| `uv run --no-sync python -m ruff check .` | 0 | all checks passed (462 files) |
| `uv run --no-sync python -m ruff format --check .` | 0 | 462 files already formatted |
| `uv run --no-sync python -m mypy navox` | 0 | no issues in 281 source files |
| `npm run lint` (repo root, all workspaces) | 0 | Web 149 files, assistant-runtime 52 files; the 9 `subscriptions-dashboard.module.css` `noDescendingSpecificity` warnings are pre-existing |
| `npm run typecheck` (repo root) | 0 | web, assistant-runtime, connector-sdk, contracts, ui |
| `npm test` (repo root) | 0 | extension 8/8; web 361/361 in 40 files (was 345/39); assistant-runtime 233 passed, 5 skipped, 1 skipped file |
| `npm run build --workspace=@navox/web` | 0 | Next compiled; `/api/v1/assistant/sessions/[sessionId]/turns/[turnId]/speech` emitted as a dynamic route |
| `bash scripts/check-api.sh` (repo root) with `UV_CACHE_DIR=/tmp/navox-m11d-uv-cache` | 1 | 8 of 10 gates passed; log `/tmp/navox-spec008-m11d-api-gate.log` |

`check-api.sh` detail on the complete M11D tree: Ruff lint PASS, Ruff formatting
PASS, strict mypy PASS, Alembic revision width PASS, full API suite PASS
(`2718 passed, 8 skipped`), Alembic SQL and required schema PASS, deterministic
release evaluation PASS, patch whitespace PASS. The two failing gates are
`SPEC-003 metric thresholds` and `SPEC-004 fixture thresholds and safety
checks`; both fail only because the run recorded 8 skips and
`navox/evaluation/connector_metrics.py` treats any skip as a failed gate. Every
skip needs a disposable PostgreSQL/Temporal stack this sandbox lacks (the same
eight the M11B report lists: four row-lock concurrency tests, three knowledge
row-lock tests and `test_knowledge_temporal.py`). None of the M11D tests is
skipped and no metric evidence comes from the speech path. The full gate is not
claimed as passed.

The 5 skipped assistant-runtime tests are the disposable-PostgreSQL
`store.integration.test.ts` cases. No browser/device run and no live or paid
provider request were performed.

## Decisions worth a reviewer's attention

1. **The M11B adapter capability gate is untouched.** `capabilities(model)`
   still returns `{TRANSCRIPTION}` only, and a regression test asserts it. The
   synthesis selector instead requires the adapter to satisfy the new
   `SpeechSynthesisAdapter` protocol, so neither selection can satisfy the other
   task's adapter check. Model-level qualification is still the authoritative
   gate in `rank_eligible_speech`.
2. **The spoken text is derived in one shared function.** `spokenTextForTurn`
   lives in `@navox/assistant-runtime/voice` and is used by both the Next route
   (server authority) and the client (whether to offer Read aloud). It refuses
   approval-required answers, non-READY/CLARIFY turns and answerless turns, and
   never returns the question. Before M11D the client derived the text itself
   and could have offered a control the server would refuse.
3. **The route sends `{}` rather than a body selector.** Session and turn ids are
   path parameters, matching the brief's "session and turn selectors only"; the
   JSON content type is required only because the existing mutation guard treats
   every assistant mutation as JSON.
4. **Client-side length/type checks are duplicated deliberately.** The Next
   route, the client fetch, the browser adapter and SPEC-005 all independently
   enforce the 2 MiB/no-store/no-empty rule, so a single broken layer cannot
   produce playback of an unexpected payload.
5. **Autoplay may still be blocked by the browser.** `audio.play()` rejection
   is a typed failure that ends `SPEAKING` and shows the fallback copy; a
   gesture-initiated Read aloud is expected to succeed, while an automatic voice
   answer can be refused by a strict autoplay policy. This is a bounded-UX
   choice worth an explicit keep-or-change from root.
6. **`synthesisSupported` is now stricter.** A browser without `Audio`, `Blob`
   or `URL.createObjectURL` reports the typed "cannot play spoken answers"
   fallback and never claims provider TTS. The previous `speechSynthesis`
   capability no longer counts as support anywhere.

## Outstanding risks and gaps

- No live provider request was made (prohibited; the owner's $10 session cap was
  not spent). The OpenAI synthesis adapter's wire shape, content-type check,
  size cap, redirect behavior and error classes are covered by
  `httpx.MockTransport` tests only, and a live response-shape check is still owed
  before traffic.
- The route is usable only when `AI_PROVIDER=automatic`, an operator catalog
  publishes a granted, evaluated `SPEECH_SYNTHESIS` model, and
  `NAVOX_API_BASE_URL` points at the SPEC-005 service. Default catalog and
  default operator settings deny it.
- No real device or browser audio playback was exercised; the player is covered
  by controlled `Audio`/`Blob`/`URL` fakes, so autoplay policy and real MP3
  decoding still need a manual check before this is called live-accepted.
- `AIProviderHealth` is shared with text traffic, so a speech failure can mark a
  model `DEGRADED`/`UNAVAILABLE` for every task type. That is the existing
  gateway behavior and is unchanged here.
- The `SPEC-003`/`SPEC-004` metric gates remain red in this sandbox for the
  pre-existing PostgreSQL/Temporal skip reason; hosted CI on the exact head is
  still required before push.

## Next checkpoint

Root owns architecture/security review and the M11 integration decision. Before
an accepted provider speech path: root review of this bundle, a disposable
PostgreSQL/Temporal `check-api.sh` run, hosted CI on the exact head, and one
browser/device pass on `/navox` once SPEC-005 has an eligible synthesis model
inside the owner's provider budget.

## Root acceptance review

The saved-turn route, synthesis selection, quota and abort paths were reviewed
as one boundary. Root reran `bash scripts/check-api.sh` against disposable
PostgreSQL/Temporal; it passed with 2,726 API tests and zero skips, including
the SPEC-003/004 metric gates. The assistant runtime passed 238 database-backed
tests and the TypeScript migration check passed 29 assertions. Log:
`/tmp/navox-spec008-m11d-root-api-gate.log`. This supports branch publication;
real provider audio, device playback, adaptive modality and live session
acceptance are still open.
