# Vox M1 operator runbook

This is an operator setup and qualification guide. The dated records below distinguish live provider and isolated browser checks from unfinished phone acceptance. Credentials are never included, and production Vox activation remains pending. See [accounts](vox-m1-accounts.md), [decisions](vox-m1-decisions.md), and the milestone verification record for the current release status.

## Configuration and secrets

1. Use an isolated, funded OpenAI project and a LiveKit Cloud project. Keep project secrets in the server secret store or a private environment file outside Git. Do not paste credentials into JSON, browser build variables, logs, or support output.
2. Provide `OPENAI_API_KEY` to both the API readiness process and the dedicated Vox worker. Provide `STUDENT_VOX_LIVEKIT_URL`, `STUDENT_VOX_LIVEKIT_API_KEY`, and `STUDENT_VOX_LIVEKIT_API_SECRET` from the same Cloud project to API, Vox worker, and cleanup worker. The URL must be the project's `wss://<project>.livekit.cloud` URL. Keep `STUDENT_VOX_LIVEKIT_AGENT_NAME` consistent; its default is `navox-vox`.
3. Copy [vox-m1-inference.example.json](vox-m1-inference.example.json) to an absolute operator-owned path visible to both API and Vox worker. Set `STUDENT_VOX_INFERENCE_CONFIG_PATH` to that path. The file must be a regular file, not a symlink, owned by root or the service UID, at most 32 KiB, and not writable by group or others. Mode `0600` is suitable. Mount it read-only into containers. It contains credential environment-variable names only.
4. Supply a valid existing Fernet secret through `CONNECTOR_SECRET_ENCRYPTION_KEY`, or the existing `GOOGLE_TOKEN_ENCRYPTION_KEY` fallback, for encrypted wake delivery and active-call recovery checkpoints. Preserve existing keys; do not rotate or replace keys merely to set up Vox. Configured call admission fails before room allocation when no usable key exists.

The JSON is reloaded for each new call. API and worker must see the same file and credential references. An absent path selects the existing qualified policy gateway; a malformed configured file fails closed rather than selecting the legacy path. Configuration changes do not replace an active call's model or voice.

The example intentionally selects OpenAI realtime `gpt-realtime-2.1` with `marin`, and automatic fallback STT `gpt-transcribe` → text `gpt-5.6-luna` → TTS `gpt-4o-mini-tts-2025-12-15` with the same `marin` voice. All four routes, endpoints, and voices are explicit; no fallback voice is inherited accidentally. Verify account access, endpoint compatibility, current model availability, and voice quality before qualification. Changing a provider label alone does not implement another provider's native protocol: the fallback adapters require OpenAI-compatible transcription, chat-completion, and speech APIs. Alternative endpoints must be explicitly operator-allowlisted origins.

Validate the example locally without resolving secrets or contacting a provider, from `services/api`:

```sh
uv run --no-sync python - <<'PY'
from pathlib import Path
from navox.core.settings import Settings
from navox.vox.inference_config import load_inference_config
path = (Path.cwd() / '../../docs/product/vox-m1-inference.example.json').resolve()
config = load_inference_config(Settings.model_construct(student_vox_inference_config_path=str(path)))
assert config is not None
assert config.mode == 'realtime' and config.fallback_enabled
assert config.realtime.voice == config.tts.voice == 'marin'
assert all(route.api_key_env == 'OPENAI_API_KEY' for route in (config.realtime, config.stt, config.llm, config.tts))
print('Example config valid; credentials not resolved; no provider calls')
PY
```

## Release and policy prerequisites

All release switches default off. Server admission requires `STUDENT_VOX_ENABLED`, `STUDENT_VOX_MEDIA_ENABLED`, `STUDENT_VOX_WORKER_QUALIFIED`, and `STUDENT_VOX_LIVEKIT_CLOUD_QUALIFIED`. Set qualification switches only after their corresponding deployment checks are satisfied; they are declarations of verified readiness, not setup substitutes. The cleanup process still requires the qualified Cloud project when new-call admission is off.

The browser is built with `NEXT_PUBLIC_NAVOX_STUDENT_VOX_ENABLED`, `NEXT_PUBLIC_NAVOX_STUDENT_VOX_MEDIA_ENABLED`, and `NEXT_PUBLIC_NAVOX_STUDENT_VOX_CAPTURE_QUALIFIED`. These are build-time public booleans, never secrets. Changing them requires a web rebuild. Microphone access requires a supported browser and a secure origin. Local Hey NavoX requires the separately qualified Porcupine runtime and correctly served model assets; an API key or JSON inference file does not supply those assets. Set neither `STUDENT_VOX_LOCAL_WAKE_ENABLED` nor `STUDENT_VOX_PORCUPINE_BROWSER_CREDENTIAL_APPROVED` until vendor approval is recorded. The server holds `STUDENT_VOX_PORCUPINE_ACCESS_KEY`; the Web SDK necessarily receives that licensed credential in browser memory through the authenticated, connection-bound, private/no-store config endpoint. It is not a browser build variable or an ephemeral vendor token. Set `STUDENT_VOX_PORCUPINE_KEYWORD_PUBLIC_PATH` and `STUDENT_VOX_PORCUPINE_MODEL_PUBLIC_PATH` to approved same-origin, versioned `.ppn`/`.pv` asset paths. No global COOP/COEP headers are introduced.

Run the current database migrations before starting the updated application and dedicated processes. The source tree includes the Vox media, usage ledger, encrypted wake-command tables, and `0056_vox_call_checkpoints`. Use the deployed release's migration head rather than pinning an older revision:

```sh
uv run --no-sync alembic upgrade head
```

Quota policy is separate from inference route selection. The current defaults are 60 voice minutes/day, 600/month, 300,000 tokens/day, 3,000,000/month, with an 80% notice threshold, measured using the user's local timezone. Operator access is an explicit `STUDENT_VOX_OPERATOR_USER_IDS` allowlist; normal students cannot edit pricing or quota policy.

Authenticated operator endpoints are `GET /api/v1/vox/usage/policy`, `PUT /api/v1/vox/usage/policy`, and `GET /api/v1/vox/usage/daily`. All these endpoints require a freshly valid browser cookie and the matching `X-NavoX-Account: <workspace UUID>:<user UUID>` header. Policy PUT also requires the approved same-origin `Origin` header. Do not paste or log session cookies. Read the current revision before updating; PUT accepts `{ "revision": <current>, "policy": <reviewed policy> }` and checks for conflicting updates. Never blindly publish the example's revision zero over an existing policy. Student-owned reads are `GET /api/v1/vox/usage/status` and `GET /api/v1/vox/usage/calls/{call_id}`.

The [pricing example](vox-m1-usage-policy.example.json) contains the exact `openai` / `gpt-realtime-2.1` token-rate entry. It also includes reviewed transcription-duration, fallback text and speech rates. It does not select a model or establish a LiveKit rate. The text rate has a standard-context ceiling; larger requests remain unknown until a matching tier is configured. Token modality rates are USD per million tokens; transport is USD per connected minute. Add only reviewed, versioned rates for the exact selected provider/model and supported billing modality. Provider duration objects are stored as billed_audio_ms and priced using audio_usd_per_minute, never converted into invented tokens. The example selects tts_stream_format=sse to receive completed speech usage. Other providers can select audio; missing usage stays unknown. Unknown rates, unknown modality, missing usage, and null transport rates mean an incomplete/unknown total, not free service. Provider estimates remain distinct from reconciled invoices. Unknown failed usage can retain a token reservation; do not delete ledger entries to reset quota. Voice activity uses durable credits of at most one second: lower budgets apply to new grants, while an already-issued credit remains usable until its deadline. Per-frame revocation is still fresh. An uncertain crash hold remains reserved, not actual speech. See [credit accounting](../vox-voice-credit-batching.md) for period attribution, checkpoint fencing and estimate limitations.

## Start the existing processes

Use the same release, database, server settings, private environment, and operator config in API and workers. The API image's existing command is:

```sh
uv run --no-sync fastapi run navox/api/main.py --host 0.0.0.0 --port 8000
```

Start these as separate supervised processes from `services/api` (or with that directory as the API image working directory):

```sh
uv run --no-sync python -m navox.vox.worker_livekit start
uv run --no-sync python -m navox.vox.worker_reconcile
```

The first command registers the named LiveKit AgentServer. The second independently retries durable media cleanup and must remain running while cleanup obligations exist. The ordinary Compose `worker` is the Temporal worker; it does not start either Vox process. The isolated docker-compose.vox-test.yml provides dedicated vox-worker and vox-cleanup services with a separate database volume and loopback ports 13000/18000. The ordinary stack still has no Vox services; do not assume `compose up` starts voice. Do not run multiple unsupervised copies. The worker admits one active job per process and uses a PostgreSQL epoch lock to reject duplicate job execution. Its health listener binds to `127.0.0.1:8081`; do not expose it publicly or treat that listener as proof of successful audio.

## Qualification and normal behavior

Use a dedicated test account and non-sensitive synthetic questions. The signed-in browser drives the existing call API; do not mint room identities or submit fake usage from a browser. The API creates a call, admits media, and renews the same media binding through `/api/v1/vox/calls` and its call/media subroutes. Initial participant JWT admission is bounded separately from the renewable live authority lease. Fresh renewal cannot revive an expired or revoked epoch.

Record actual results for: two natural spoken turns on one provider WebSocket; interruption with no unheard text later treated as spoken; a read-only real schedule question; a short network interruption with the same room/worker; two minutes of quiet followed by a local wake question; automatic fallback before speech; failure after partial speech without duplicate replay; hangup/logout/revocation; quotas and usage settlement; and a quiet provider rollover. Inspect metadata and sanitized readiness only. Do not collect raw audio, transcripts, room JWTs, project secrets, or provider error bodies in diagnostics.

The configured worker suppresses long silence locally and uses short preroll/hangover buffers. It keeps a persistent realtime socket through normal turns, and warms a quiet replacement before the configured rollover age. Fallback is attempted only for eligible primary failure before output or tool execution; it must not repeat a question after partial spoken output. Schedule tools read the authenticated student's actual schedule and do not authorize writes. Question/transcription input over 4,000 characters is rejected rather than truncated.

## Failure, revocation, and rollback

- For unavailable admission, check sanitized configuration validity, credential-presence booleans, Cloud qualification, worker registration/name, database connectivity/migrations, and the owned budget status. Key presence alone is not live qualification. Do not dump environment values, user grant rows, or raw model policy.
- For a partial spoken failure, clear/stop output and offer a retry. Do not silently resend the same turn through fallback. A typed wake submission being accepted by the queue does not prove it was spoken; an ended/expired call requires a visible retry outcome.
- Hang up using the normal authenticated end/close path. Ownership loss, logout, generation change, expired lease, or quota exhaustion must stop further inference/audio. Leave the durable cleanup worker running until remote cleanup is confirmed. Disabling admission alone is not a replacement for revoking existing sessions.
- To stop new calls, disable the relevant server/browser release switches and rebuild browser flags where needed. Preserve end/close routes and cleanup operation. Drain/close active calls before stopping their worker or changing the deployed inference file.
- Roll back application images/source and the operator JSON to the last tested release/config using the established deployment procedure. Do not drop usage/wake tables, rewind migrations, delete user data, erase uncertain reservations, or rotate unrelated secrets. Keep the cleanup reconciler compatible with outstanding obligations. Clearing the inference path selects legacy gateway behavior only if that separate gateway remains qualified; it is not a guaranteed working fallback deployment.

## Explicit unfinished qualification

- Configured workers persist encrypted completed-turn checkpoints for the same active call. A replacement worker loads them only under its physical PostgreSQL worker lock and fresh joined authority, with User-serialized revision checks. The browser retires old audio/JWTs and obtains fresh admission; it waits up to 20 attempts for an explicit HTTP 425 pre-allocation cleanup response. Unknown join outcomes and authentication errors are not automatically retried. Missing, corrupted or expired context fails rather than pretending continuity. A configuration fingerprint also prevents a replacement from silently changing the active voice/model; drain active calls before changing the inference configuration. The legacy gateway has no durable checkpoint and cannot resume a replacement conversation. Real terminal-room/worker replacement remains unqualified on phones.
- Checkpoint expiry follows the renewable reservation (at most 120 seconds without a fresh renewal). Hang-up, logout and reset purge it; source Forget clears the active call and retires its media, preventing stale provider RAM from continuing. The reconciler erases expired ciphertext independently. A crash between physical playout and checkpoint commit can lose the last turn. No raw audio or ordinary saved conversation-history API is added. Historical backups are outside the runtime deletion guarantee. Drain active calls before a rollback; migration 0056 refuses to drop nonempty checkpoint storage.
- Automatic context compaction is not implemented. Context limits fail closed rather than promising indefinite multi-hour conversation. Quiet socket rollover has synthetic coverage; real long-duration and physical-network behavior remain unqualified.
- Heard-prefix tracking uses the local RTC playout estimate and provider truncation result, not physical proof of sound reaching the listener. Real microphone quality, VAD boundaries, naturalness, echo/barge-in, and phone latency require device tests.
- Wake-command API/queue plumbing does not qualify the browser's local keyword assets/runtime, spoken-question capture, or user-visible completion/retry behavior. Verify those in the actual signed-in browser; do not claim a local wake test from a synthetic text enqueue.
- Token/transport metering has synthetic server tests. Complete provider usage reconciliation, configured fallback pricing, actual provider failover, LiveKit cleanup/revocation, and cost accuracy still require live qualification. This document does not certify paid audio, locked-screen/native-phone operation, or end-to-end M1 completion.


## Live provider checks, 2026-10-23

A realtime session was acknowledged and returned audio. Two generated audio-input
turns then completed on the same socket with transcription and spoken responses.
The full transcription → text → speech cascade also completed. These are live
provider checks with synthetic input, not microphone, browser, LiveKit transport,
student-schedule or phone acceptance.

The checks exposed missing speech token usage in raw MP3 responses and discarded
duration-billed transcription usage. Speech SSE now settles provider text/audio
counts; duration objects settle separately from tokens. Unknown/incomplete
responses retain their reservations. No transcripts, audio, credentials or raw
provider errors were retained in evidence.

Pricing/format references (reviewed 2026-10-23):
- https://developers.openai.com/api/docs/models/gpt-transcribe
- https://developers.openai.com/api/docs/models/gpt-5.6-luna
- https://developers.openai.com/api/docs/models/gpt-4o-mini-tts
- https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create

The current documentation labels the configured TTS snapshot deprecated. It
worked in the live checks; replacement qualification remains open before public
launch. No model has been silently changed.

## Isolated qualification deployment

Use docker-compose.vox-test.yml with --env-file pointing to the private operator
file, outside the source tree. That file must define NAVOX_VOX_ENV_FILE (itself),
NAVOX_VOX_CONFIG_FILE (the private inference JSON), NAVOX_PUBLIC_ORIGIN, and unique
NAVOX_DATABASE_PASSWORD and TEMPORAL_DATABASE_PASSWORD. Set DATABASE_URL for this
stack's postgres service, TEMPORAL_TARGET=temporal:7233, WEB_ORIGIN to the test
HTTPS origin, and STUDENT_VOX_INFERENCE_CONFIG_PATH=/run/navox/vox-inference.json.
Use only the dedicated test LiveKit/OpenAI credentials, a distinct agent name
and a new encryption key for the empty test database. Do not copy production
sessions, connected accounts, credentials, volumes, or encryption keys.

The overlay's public build flags enable mobile-emulation qualification, not
phone acceptance. Server switches apply only to this isolated test environment.
Block public registration at the test proxy after creating the named test
accounts locally; keep phone tests pending until the owner completes them.
This file does not change the normal production deployment.


## Browser audio qualification, 2026-10-23

The isolated HTTPS deployment returned spoken audio through LiveKit to Chromium
with a 390 × 844 mobile viewport and generated microphone input. A two-turn
check answered 2 + 2 with four, then adding three with seven. The encrypted
active-call checkpoint was checked in memory for those expected results; no
transcripts or raw audio were written to diagnostic evidence. Browser audio
energy was nonzero, both turns completed, and no page/capture faults occurred.
This is mobile emulation, not a phone recording or phone acceptance.

The successful two-turn call recorded 58,837 ms connected, 8,400 ms speaking,
50,437 ms idle, 1,434 provider tokens, and zero remaining token reservations.
Known model cost was estimated at USD 0.0146808. Total estimated cost remains
unknown because the LiveKit transport rate is unconfigured; invoice
reconciliation remains pending. End and media-close requests returned HTTP 204.

Real browser testing exposed and corrected an unpicklable worker entry point,
an unsupported realtime tool-schema field, finite browser audio peaks outside
[-1, 1], premature uplink pausing between words, and input loss while provider
admission stalled. One measured receive operation took 2,559 ms while the old
RTC queue retained only 100 ms. RTC ingestion now drains into a separate,
transient five-second queue. Overflow terminates explicitly instead of silently
losing words. Idle sealing cannot run during input processing or pending frames;
disconnect discards queued frames, and shutdown stops input before closing the
provider. Fresh per-frame authority and budget checks remain in place.

The longer, multi-sentence synthetic prompt initially split into separate turns
and failed the expected-answer assertions. After the configured 1,200 ms turn
window was introduced, it completed as exactly two turns with four followed by
seven. All provider reservations settled, with 1,492 tokens and USD 0.017691 in
known estimated model cost. Physical-device turn timing and barge-in remain open;
these emulated checks do not complete all conversational acceptance tests.

The test origin is https://vox-test.66-175-235-169.nip.io/ and public sign-up is
blocked. Named test credentials are stored privately outside the repository.
The previous dotted sslip.io hostname failed TLS from the connected Mac despite
working from the server. The replacement hostname passes normal certificate
validation, browser secure-context checks, and sign-in from the Mac. The web
bundle and isolated API origin configuration were updated together; production
routes remain unchanged. Physical-phone reachability is not yet verified.
Picovoice vendor approval and licensed wake assets remain pending, so local
wake switches remain disabled. Real student schedule answers, phone recordings,
long calls, provider failover, and student usage targets remain unqualified.


### Network recovery check

A separate browser container lost its network attachment for six seconds between
the two arithmetic questions. Vox showed Reconnecting, paused audio, retired the
old media binding, waited through HTTP 425 cleanup, and rejoined. The second
question alone was injected after recovery; the first question was not replayed.
The checkpoint retained exactly two completed turns, with four followed by seven.
The UI then showed Call ended; end/close returned 204. Usage settled 1,384 tokens
with zero remaining token/voice reservations. This verifies one emulated network
scenario, not mobile Wi-Fi/cellular switching or a long outage.

The configured worker now exposes end_of_turn_silence_ms (400–2,000, default
1,200) in the operator inference file. The measured 980 ms sentence pause exceeded
the previous fixed 600 ms cutoff. The longer window allows that pause inside one
utterance, at the cost of up to 600 ms additional end-of-turn wait. It does not
change the browser's silence gate, wake timing, or animation timings. Configuration
changes apply to new calls and require draining existing calls before deployment.


### Interruption and saved-schedule checks

A generated interruption arrived while the worker was actively playing its
counting response (output_started=true; played_audio_ms=6,382). The interrupted
answer was truncated and the next answer correctly returned fifteen for ten
plus five. The browser's instantaneous UI-state probe was not reliable enough
to establish the interruption point; worker playout evidence established it.
This does not measure physical speaker/echo/Bluetooth interruption latency.

A temporary QA-only course and 10:00 meeting in Lab 42 were created through the
normal authenticated student API. A spoken next-class query correctly returned
the saved course, time and room. Its meeting was deleted and course archived
afterward. This verifies live tool integration with saved test data, not the
owner's real student timetable.

### Fallback tool compatibility

The isolated primary-transport outage test exposed a Chat Completions rejection
at reasoning_effort when the schedule tools were present. A direct request with
reasoning_effort=none succeeded. The configured fallback now sends
llm_reasoning_effort (default none); setting it to null omits the field for a
compatible provider that does not accept it. The same setting applies on every
tool round trip. No model, voice, authorization boundary or tool permission was
changed.

On 2026-10-24 the isolated primary endpoint was temporarily pointed at a verified
closed loopback port, then the real STT → text → speech fallback completed both
questions correctly through the browser. The call settled 937 provider tokens,
zero remaining reservations, and USD 0.0018700 in known estimated model cost.
The original operator configuration was restored byte-for-byte afterward. One
earlier run returned an incorrect follow-up and another was ended before its
second speech request completed. This successful controlled scenario is not a
voice-accuracy benchmark or a latency acceptance pass; broader corpus/device
quality and slow-provider behavior remain open.

Official references reviewed 2026-10-23:
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/docs/models/gpt-5.6-luna


Repeated terminal connection replacement is now bounded to three recoveries in
two minutes. A fourth failure ends the call through the existing cleanup path
and offers a fresh call, preventing a persistent provider fault from producing
an endless reconnect loop. Ordinary same-room SDK recovery does not consume
this allowance. A new call resets it; old recoveries expire from the window.
This changes failure handling, not layout or the normal successful call flow.
