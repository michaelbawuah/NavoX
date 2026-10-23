# Vox M1 operator runbook

This is an operator setup and qualification guide, not evidence of a working live call. No credentials, production activation, paid provider requests, or physical-device qualification are supplied by this change. See [accounts](vox-m1-accounts.md), [decisions](vox-m1-decisions.md), and the milestone verification record for the current release status.

## Configuration and secrets

1. Use an isolated, funded OpenAI project and a LiveKit Cloud project. Keep project secrets in the server secret store or a private environment file outside Git. Do not paste credentials into JSON, browser build variables, logs, or support output.
2. Provide `OPENAI_API_KEY` to both the API readiness process and the dedicated Vox worker. Provide `STUDENT_VOX_LIVEKIT_URL`, `STUDENT_VOX_LIVEKIT_API_KEY`, and `STUDENT_VOX_LIVEKIT_API_SECRET` from the same Cloud project to API, Vox worker, and cleanup worker. The URL must be the project's `wss://<project>.livekit.cloud` URL. Keep `STUDENT_VOX_LIVEKIT_AGENT_NAME` consistent; its default is `navox-vox`.
3. Copy [vox-m1-inference.example.json](vox-m1-inference.example.json) to an absolute operator-owned path visible to both API and Vox worker. Set `STUDENT_VOX_INFERENCE_CONFIG_PATH` to that path. The file must be a regular file, not a symlink, owned by root or the service UID, at most 32 KiB, and not writable by group or others. Mode `0600` is suitable. Mount it read-only into containers. It contains credential environment-variable names only.
4. Supply a valid existing Fernet secret through `CONNECTOR_SECRET_ENCRYPTION_KEY`, or the existing `GOOGLE_TOKEN_ENCRYPTION_KEY` fallback, for the encrypted wake-question queue. Preserve existing keys; do not rotate or replace keys merely to set up Vox. Queue delivery is unavailable without a usable key.

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

Run the current database migrations before starting the updated application and dedicated processes. The source tree includes the Vox media, usage ledger, and encrypted wake-command tables. Use the deployed release's migration head rather than pinning an older revision:

```sh
uv run --no-sync alembic upgrade head
```

Quota policy is separate from inference route selection. The current defaults are 60 voice minutes/day, 600/month, 300,000 tokens/day, 3,000,000/month, with an 80% notice threshold, measured using the user's local timezone. Operator access is an explicit `STUDENT_VOX_OPERATOR_USER_IDS` allowlist; normal students cannot edit pricing or quota policy.

Authenticated operator endpoints are `GET /api/v1/vox/usage/policy`, `PUT /api/v1/vox/usage/policy`, and `GET /api/v1/vox/usage/daily`. All these endpoints require a freshly valid browser cookie and the matching `X-NavoX-Account: <workspace UUID>:<user UUID>` header. Policy PUT also requires the approved same-origin `Origin` header. Do not paste or log session cookies. Read the current revision before updating; PUT accepts `{ "revision": <current>, "policy": <reviewed policy> }` and checks for conflicting updates. Never blindly publish the example's revision zero over an existing policy. Student-owned reads are `GET /api/v1/vox/usage/status` and `GET /api/v1/vox/usage/calls/{call_id}`.

The [pricing example](vox-m1-usage-policy.example.json) contains only the exact `openai` / `gpt-realtime-2.1` token-rate entry. It does not select a model, price the three fallback models, or establish a LiveKit rate. Token modality rates are USD per million tokens; transport is USD per connected minute. Add only reviewed, versioned rates for the exact selected provider/model and supported billing modality. File-STT duration billing and missing provider usage must not be converted into invented token counts. Unknown rates, unknown modality, missing usage, and null transport rates mean an incomplete/unknown total, not free service. Provider estimates remain distinct from reconciled invoices. Unknown failed usage can retain a token reservation; do not delete ledger entries to reset quota. Voice activity uses durable credits of at most one second: lower budgets apply to new grants, while an already-issued credit remains usable until its deadline. Per-frame revocation is still fresh. An uncertain crash hold remains reserved, not actual speech. See [credit accounting](../vox-voice-credit-batching.md) for period attribution, checkpoint fencing and estimate limitations.

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

The first command registers the named LiveKit AgentServer. The second independently retries durable media cleanup and must remain running while cleanup obligations exist. The ordinary Compose `worker` is the Temporal worker; it does not start either Vox process. There is currently no dedicated Vox worker service in the repository's Compose files: add an explicitly reviewed service/supervisor deployment rather than assuming `compose up` starts voice. Do not run multiple unsupervised copies. The worker admits one active job per process and uses a PostgreSQL epoch lock to reject duplicate job execution. Its health listener binds to `127.0.0.1:8081`; do not expose it publicly or treat that listener as proof of successful audio.

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

- Conversation context is active-call RAM, keyed to the authenticated call and generation. Same-worker transient RTC reconnection can preserve it; a worker crash, replacement process, terminal expiry, or new call does not restore it. No off-record call-history database is implemented.
- Automatic context compaction is not implemented. Context limits fail closed rather than promising indefinite multi-hour conversation. Quiet socket rollover has synthetic coverage; real long-duration and physical-network behavior remain unqualified.
- Heard-prefix tracking uses the local RTC playout estimate and provider truncation result, not physical proof of sound reaching the listener. Real microphone quality, VAD boundaries, naturalness, echo/barge-in, and phone latency require device tests.
- Wake-command API/queue plumbing does not qualify the browser's local keyword assets/runtime, spoken-question capture, or user-visible completion/retry behavior. Verify those in the actual signed-in browser; do not claim a local wake test from a synthetic text enqueue.
- Token/transport metering has synthetic server tests. Complete provider usage reconciliation, configured fallback pricing, actual provider failover, LiveKit cleanup/revocation, and cost accuracy still require live qualification. This document does not certify paid audio, locked-screen/native-phone operation, or end-to-end M1 completion.
