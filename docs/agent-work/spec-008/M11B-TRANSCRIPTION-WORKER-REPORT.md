# M11B bounded transcription — worker report (1 October 2026)

**STATUS: ready_for_review**

Task: `/root/spec008_transcription`. Workspace `/Users/mba_steins/NavoX`,
branch `spec-008-navoxbot` at `bb0a7c96`. The shared tree was already dirty with
accepted SPEC-006/007 and SPEC-008 M1–M11A work; every pre-existing change was
preserved and no other agent's file was edited. No commit, push, deploy,
publication, live provider call, or production migration was made.

## What changed

| Path | Change |
| --- | --- |
| `services/api/navox/ai/speech_adapters.py` (new) | Provider-neutral `SpeechAdapter` protocol, one OpenAI `/v1/audio/transcriptions` implementation, `configured_speech_adapters(settings)` |
| `services/api/navox/ai/speech_service.py` (new) | Bounded WAV contract, stream byte cap, transcript bound, fixed `TRANSCRIBE` task, M11A selection, quota, provider call, post-call re-authorization, content-free trace |
| `services/api/navox/ai/store.py` | Added `GatewayStore.admit_transcription` (small supporting helper) |
| `services/api/navox/api/ai_operations.py` | Added `POST /api/v1/ai/assistant/speech/transcribe` and `TranscriptionResponse` |
| `services/api/tests/test_ai_speech_transcription.py` (new) | 63 focused tests |
| `docs/agent-work/spec-008/M11B-TRANSCRIPTION-WORKER-REPORT.md` | This report |

### Route contract

`POST /api/v1/ai/assistant/speech/transcribe` (raw `audio/wav` body only).
Current account plus the existing `require_origin` same-origin check.
`read_bounded_audio` refuses a chunk when `len(collected) + len(chunk)` exceeds
1,000,000 bytes *before* extending, so one oversized streamed chunk is rejected
unread with 413. `navox.ai.speech_service.parse_wav` then validates in memory
with the standard library `wave` reader: uncompressed PCM, mono, 16 kHz,
16-bit, complete frame data, and a duration derived from frames
(200 ms–30,000 ms). The response is exactly `{"text": string}` with
`Cache-Control: no-store`. Transcripts are trimmed and bounded to the
assistant's 500-character question limit; blank, non-string, or overlong
provider output is rejected.

### Selection and execution

`transcription_task` builds one fixed `AITask`: `TRANSCRIBE`,
`SPEECH_TRANSCRIPTION`, `TRANSCRIPTION`, `SENSITIVE`, `INTERACTIVE`,
`speech_transcription_prompt@v1`, `speech_transcription@v1`, no context
references, `allow_fallback=False`, `max_fallbacks=0`, and `max_cost` equal to
the operator policy ceiling. The request can never supply a model, provider,
policy, sensitivity, prompt, or task field.

Execution reuses `GatewayStore.authorize`/`snapshot`, M11A
`rank_eligible_speech`, `claim`, `health`, and `AITaskRun` traces. Exactly one
installed adapter is selected (first qualified candidate, no fallback or
shadow). `BoundedAudio.data` is the validated original WAV container, not
`readframes()` PCM, and `OpenAISpeechAdapter` refuses any payload without
`RIFF`/`WAVE` markers before opening a request. The selection's `Decimal`
reservation is written as `reserved_cost` before the call and as
`estimated_cost` on completion; the trace carries only
task/profile/prompt/schema, provider/model, registry revision, status,
latency, cost, and the fixed error code — never audio or transcript text.
After the provider call the service re-runs `authorize`, re-snapshots the
catalog and re-ranks; a revoked account (403), or a changed grant, catalog
revision, or budget (503) discards the transcript. Upstream cost may still
have been incurred, and the caller fails closed.

The adapter accepts a transcription response that omits `model` (the documented
shape) and rejects one that contradicts the selected model identifier.

### Quota

`GatewayStore.admit_transcription(task, limit=20, window=1h)` locks the
caller's `users` row (`SELECT ... FOR UPDATE`), re-reads the
`(workspace_id, user_id)` membership and pause flag under that lock, counts
this user's `ai_task_runs` rows with `task_type = transcribe` inside the
rolling window, and inserts the committed `STARTED` attempt row before any
provider call, so failed and discarded attempts consume the same budget and a
revocation between `authorize` and admission cannot reserve a paid slot.

## Correction cycle (root review, one pass)

1. **Container bytes.** `parse_wav` previously returned `readframes()` PCM;
   `transcribe_audio` forwarded that as `audio.wav`, so the provider would have
   received bare PCM mislabeled as WAV. `BoundedAudio.data` is now the validated
   original container, the adapter enforces `RIFF`/`WAVE` before building a
   request, and tests assert the header on the wire
   (`test_openai_adapter_sends_the_selected_model_and_wav_bytes`,
   `test_openai_adapter_rejects_bare_pcm_mislabeled_as_wav`, and the positive
   route test's `sent_audio[:4] == b"RIFF"` / `sent_audio[8:12] == b"WAVE"`
   check).
2. **Single oversized chunk.** The route now calls `read_bounded_audio`, which
   tests `len(collected) + len(chunk) > limit` before extending.
   `test_read_bounded_audio_rejects_one_oversized_chunk_without_reading_more`
   feeds one over-limit chunk and asserts no further chunk is pulled; the route
   oversize test is also a single ASGI message (`ASGITransport` delivers the
   body as one `http.request` chunk).
3. **Response model identity.** `OpenAISpeechAdapter._decode` now takes the
   requested model and rejects a present-but-different `model` field with
   `INVALID_RESPONSE` while still accepting omission
   (`test_openai_adapter_accepts_a_matching_returned_model`,
   `test_openai_adapter_rejects_a_contradicting_returned_model`, and the
   omission case in the wire-shape test).
4. **Revocation at admission.** `admit_transcription` now re-reads
   `WorkspaceMembership` under the same row lock as the count and insert.
   `test_quota_admission_fails_closed_without_membership` covers a membership
   revoked between `authorize` and admission.

## Verification

All commands ran from `services/api` unless noted, with
`UV_CACHE_DIR=/tmp/navox-m11b-uv-cache` and the repository's existing `.venv`.

| Command | Result |
| --- | --- |
| `uv run --no-sync python -m pytest -q tests/test_ai_speech_transcription.py` | exit 0 — 63 passed |
| `uv run --no-sync python -m pytest -q tests/test_ai_speech_transcription.py tests/test_ai_speech_routing.py tests/test_ai_intent_plan.py tests/test_ai_runtime.py tests/test_ai_gateway_foundation.py tests/test_ai_evaluation.py` | exit 0 — 303 passed |
| `uv run --no-sync python -m ruff check .` | exit 0 — all checks passed (460 files) |
| `uv run --no-sync python -m ruff format --check .` | exit 0 — 460 files already formatted |
| `uv run --no-sync python -m mypy navox` | exit 0 — no issues in 280 source files |
| `bash scripts/check-api.sh` (repo root) | exit 1 — 8 of 10 gates passed; see below. Log `/tmp/navox-spec008-m11b-api-gate-correction.log` |

`check-api.sh` gate detail on the corrected tree: Ruff lint PASS, Ruff
formatting PASS, strict mypy PASS, Alembic revision width PASS, full API suite
PASS (`2679 passed, 8 skipped`), Alembic SQL and required schema PASS,
deterministic release evaluation PASS, patch whitespace PASS. The two failing
gates are `SPEC-003 metric thresholds` and `SPEC-004 fixture thresholds and
safety checks`. Both fail only because the run recorded 8 skips, and
`navox/evaluation/connector_metrics.py:129` treats any skip as a failed gate
(`not (failed or skipped or invalid)`). Every skip needs infrastructure this
sandbox lacks:

```
tests/test_knowledge_semantic.py:1556        Hourly quota serialization needs a real row lock (PostgreSQL)
tests/test_news_semantic_clustering.py:1104  Concurrency requires PostgreSQL row locks
tests/test_news_semantic_clustering.py:1197  Concurrent membership requires PostgreSQL
tests/test_news_semantic_clustering.py:1504  Row-lock ordering requires PostgreSQL
tests/test_knowledge_ask.py:798              Row-lock concurrency needs PostgreSQL
tests/test_knowledge_ask.py:1155             Row-lock concurrency needs PostgreSQL
tests/test_knowledge_ask.py:1365             Row-lock concurrency needs PostgreSQL
tests/test_knowledge_temporal.py:38          Requires explicitly configured disposable PostgreSQL and Temporal
```

`pg_isready` and `docker` are unavailable and no `NAVOX_CONNECTOR_TEST_DSN` is
set, so the local stack is SQLite-only. None of the M11B tests is skipped and
none of the metric evidence comes from transcription. The full gate is not
claimed as passed; it needs a disposable PostgreSQL/Temporal stack, which is
the same limitation the checkpoint records for earlier slices. The earlier gate
run on this branch (`/tmp/navox-spec008-m11b-api-gate-final.log`, before the
correction cycle) produced the same per-gate outcome.

## Decisions worth a reviewer's attention

1. **Overlong transcript is rejected (502), not truncated.** The brief says
   both "bound transcript to the 500-character question limit" and "reject
   ... overlong provider output"; the fail-closed reading was chosen so a
   truncated question never reaches the assistant as user input.
2. **Upstream split.** `authentication`, `rate_limit`, `timeout`, and
   `unavailable` map to 503; `invalid_response`, `refusal`, `invalid_request`,
   and `unknown` map to 502. Both are inside the brief's "502 or 503".
3. **Fixed task fields.** `quality_class=STANDARD` (0.80/0.95 thresholds) and
   `max_output_tokens=1000`. The token field is a required `AITask` field, not
   a transcription budget: audio stays priced by duration through M11A's
   `reserve_speech_cost`.
4. **Exact response model match.** A returned `model` must equal the selected
   identifier; omission is accepted. If the live endpoint ever answers with a
   dated snapshot alias, the adapter rejects it as `INVALID_RESPONSE` and the
   caller sees 502 — a live response-shape check is still owed before traffic.
5. **Origin semantics reuse the existing check.** `require_origin` rejects a
   present-but-different `Origin` (403); an absent `Origin` is not a mismatch,
   matching every other SPEC-005 mutation route. If M11B requires a mandatory
   `Origin` header, that is a deliberate behavior change for Astra to accept
   before it is implemented.
6. **Quota evidence.** The `FOR UPDATE` lock, membership re-read, and
   rolling-window count are asserted directly (compiled PostgreSQL SQL plus
   deterministic count/limit/window tests); true lock contention on SQLite is
   not asserted, because the repository's own concurrency tests skip without
   PostgreSQL.

## Outstanding risks and gaps

- No live provider request was made (prohibited). The OpenAI adapter's wire
  shape, container header, model-identity check, redirect behavior, response
  cap, and error classes are covered by `httpx.MockTransport` tests only.
- The transcript route has no TypeScript/Web caller yet; M11B is the backend
  boundary only.
- The route is available only when `AI_PROVIDER=automatic`, an operator
  catalog is published, and a speech profile/model/evaluation/grant qualifies
  under M11A. Default catalog and default operator settings deny it.
- `AIProviderHealth` is shared with text traffic; a speech failure can mark a
  model `DEGRADED`/`UNAVAILABLE` for every task type, which is the existing
  gateway behavior and not changed here.

## Next checkpoint

Astra owns security/architecture review and the M11 integration decision.
Remaining before an accepted voice path: the TypeScript client handoff to this
route, hosted CI on the exact head, and a full `check-api.sh` run with a
disposable PostgreSQL/Temporal stack.
