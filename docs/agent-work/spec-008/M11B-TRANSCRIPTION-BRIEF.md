# M11B — Bounded SPEC-005 transcription request path

## User-visible result and scope

Add an authenticated, provider-backed transcription operation inside the existing
Python SPEC-005 service. A web client will later send a short recorded WAV clip
through the TypeScript assistant API; M11B builds and fake-tests the backend
boundary first. It must be unavailable by default because M11A added vocabulary
and selection but published no speech profile/model/evaluation/traffic. No live
provider request, catalog publication, new backend language or UI edit here.

## Request and data contract

- `POST /api/v1/ai/assistant/speech/transcribe` accepts raw `audio/wav` only,
  not multipart or base64 JSON. Require current account and the existing
  same-origin mutation check. Stream-read at most 1,000,000 bytes; reject
  oversize without buffering the rest. Parse in memory with the standard
  library as uncompressed PCM WAV, mono, 16 kHz, 16-bit, between 200 ms and
  30,000 ms, with valid complete frame data. Calculate bounded duration from
  frames, not a client claim. No raw audio or transcript in logs, trace rows,
  URL or exception detail. Return only `{text: string}` with `Cache-Control:
  no-store`; trim and bound transcript to the assistant's 500-character
  question limit. Reject blank, invalid/overlong provider output.
- Construct a fixed SPEC-005 `AITask` using `TRANSCRIBE`,
  `SPEECH_TRANSCRIPTION`, `TRANSCRIPTION`, `SENSITIVE`, interactive latency,
  fixed `speech_transcription_prompt@v1` and `speech_transcription@v1` refs,
  no context references, no fallback/shadow, and a per-request cost bound at
  most the operator policy ceiling. Never accept model, provider, policy,
  sensitivity, prompt or task fields from the request.
- Use `GatewayStore.authorize` and `snapshot`, M11A `rank_eligible_speech`,
  `claim`, `health` and content-free `AITaskRun` trace. Select only one eligible
  installed adapter; no fallback. Enforce a fixed 20 attempted transcription
  requests per user per rolling hour before a provider call, atomically under
  the user's database row lock; failed attempts count. Reserve the selection's
  Decimal estimated cost and trace only duration/cost/status/model IDs, never
  audio/transcript. After a slow call, re-read authorization and qualification;
  discard the transcript if eligibility, grant, catalog revision or budget
  changed. A revocation may still incur upstream cost; fail closed to the user.
- Add a provider-neutral speech adapter protocol and one OpenAI implementation
  under `navox.ai`, using the configured server-only key and a fixed HTTPS
  `/v1/audio/transcriptions` destination. Send selected model ID plus WAV
  bytes. Apply a bounded timeout, no redirects, a finite response cap,
  response/model/text validation, and fixed error classifications without
  exposing response bodies or credentials. Do not route directly from TS or
  depend on an OpenAI SDK. Controlled fake adapter tests must make no network
  request. The operator must explicitly register/evaluate/enable an audio
  model to make this route usable.

## Failure behavior and tests

Return generic 401/403 for missing account/origin or revoked scope; 415 for
wrong MIME; 413 for oversize; 422 for malformed/unsupported/too short/long WAV;
429 for quota; 503 for unqualified/unconfigured provider; 502 or 503 for
bounded upstream failure. No error should echo payload or provider body.
Test all denials, WAV edge cases, positive fake transcript, exact model
selection, quota concurrency or its lock protocol, trace content, changed
grant/catalog after the call, provider timeout/invalid output, no-request on
default catalog, and no action authority conveyed by a transcript like `yes`.
Do not perform paid tests.

## Ownership and checks

Own new speech service/adapter modules under `services/api/navox/ai/`, the
speech route in `services/api/navox/api/ai_operations.py`, and focused Python
tests under `services/api/tests/`. If a small supporting change to `GatewayStore`
or error mapping is required, explain it in the report. Do not edit TS, the
catalog template, registry contracts, M11A selector, migrations, CI, settings,
checkpoint, SPEC-006/007, or lockfiles. The shared tree is dirty; you are not
alone, and must preserve all others' work. Write one final report to
`docs/agent-work/spec-008/M11B-TRANSCRIPTION-WORKER-REPORT.md` **before** your
final agent message. Avoid ending the turn with a promise to write the report.

Run focused pytest, Ruff lint/format check and strict mypy, then
`bash scripts/check-api.sh` with the current uv.lock and the available local
test stack; if infrastructure prevents the full gate, report the exact
unexecuted/failing portion without calling it passed. No commit, push, deploy,
provider call or production migration.
