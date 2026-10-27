# Prepared synthetic spoken-input qualification

This is a test harness, not application code or proof of a physical phone. It is
locked until root explicitly starts a run after deployment and pricing readiness.
Preparation and the read-only preflight made no native call, provider request,
signup, login, memory change, quota reset or runtime configuration change.

## Prepared files

- `fixtures.json`: five predefined synthetic utterances; no student content.
- `prepare_audio.py`: local macOS Samantha/afconvert synthesis only, PCM16 mono
  at 16kHz, with bounded leading/trailing silence. Regenerate after any manifest
  change and use the updated metadata/hash values. Preflight checks the exact
  spoken-text hash as well as each WAV hash, format and duration; old audio with
  the same fixture IDs cannot qualify a changed utterance manifest.
- `synthetic-mic.cjs`: fake-device permission surface followed by a controlled
  WebAudio microphone track. It records numeric RTP/playback counters only and
  never captures provider audio. The local browser probe verified three nonzero
  input lifecycles, three closed AudioContexts, zero RTC peers and zero external
  requests.
- `qualify.cjs`: actual browser/LiveKit spoken-input flow, at most three calls,
  a 40-second input cutoff and a $2 known-PROVIDER-estimate stop threshold. It
  leaves a close margin toward 45 seconds per call and requires total metered
  transport at most 135 seconds. Transport USD remains explicitly UNKNOWN when
  its price is absent; no combined dollar total or zero transport price is claimed.
  Unchanged native quotas and metering still apply.
  Wall-clock checks run independently of pending usage-status requests; a slow
  usage response cannot defer the cutoff. The offline self-test holds a usage
  request across the cutoff and verifies immediate retirement without a live call.
  The explicit `neutral-private-only` continuation permits exactly two new calls
  after one root-verified prior call. Its prior provider estimate and transport
  duration are subtracted from the original $2 / 135-second limits, and its
  report retains the three-call ceiling. Missing prior usage refuses execution.
  A scoped pass covers only the selected profile.
- `run_on_isolated.py`: exact source/config/pricing preflight, parent-owned
  in-memory session, stdin-only transfer to the browser child, emergency call
  retirement, scoped erasure and logout. No credential is written to files or
  command arguments. Docker timeout cleanup removes only this helper container.
  Native availability is detected from the actual running worker/tool/status
  module source and binding, matched to the exact release archive. The
  `MEMORY_SEMANTIC_LEARNING_ENABLED` flag controls background work only; it is
  reported for context and is never required or enabled by this harness. Current
  consent/generation/native availability are checked through GET `/memory/learning`
  after an authorized test session exists.
  The loopback registration session is revoked immediately before HTTPS login,
  using its exact cookie in memory even when Secure prevents automatic loopback
  transmission. The same revoked cookie must then fail `/auth/me` with 401.
  Final cleanup separately revokes the HTTPS session.

## Flow after root approval

1. First call: exactly two independently certifiable neutral sentences that the
   old whole-turn literal parser does not save. The later remember request also
   supplies the historical science remark; it is unarchived while the tool learns
   the earlier neutral-only parent. Verify two actual source-linked facts. Do not
   append an unknown sentence to the neutral source or weaken the host validator.
2. Second call: an unknown project detail becomes an actual pending private
   suggestion. Fresh Settings reads, initially unchecked agreement and exact
   confirmation must retire the native call before the save request. The existing
   candidate RTC card is recorded only if actually sent; the new learning tool
   intentionally uses a fixed Settings instruction and no invented RTC topic.
3. Third call: verify neutral cross-call recall, the enforced notice prefix before
   historical excerpts, and withholding of the privately saved second source.

The `neutral-private-only` profile runs steps 1–2 and never starts step 3. Its
cross-call recall, historical host notice and private withholding fields remain
`not_run` with `live_qualified: false`. Those checks require future authorized
qualification; the two-call result must not be described as full qualification.

## Fresh functional qualification after the closed plan

The earlier plan remains stopped with two calls, 48,085 ms of metered transport,
$0.0745244 of known provider estimates and one uncertain realtime stream retaining
10,525 tokens. Its total provider cost is unknown. A calculated rate allowance is
not a provider receipt, and no reservation or uncertainty is erased by this run.

The separately authorized `fresh-three-calls` profile admits at most three new
calls for the complete functional story, retaining a cumulative five-call ceiling.
It permits at most 135,000 ms of new metered transport and reports the separate
183,085 ms cumulative ceiling. The known-provider estimate stop remains $2 across
the historical and new calls, leaving $1.9254756 for the new run. This is a stop
threshold for known estimates, not a hard all-in spending cap; old uncertain
provider usage and unpriced transport remain unknown. Every new call must settle
its provider streams and reservations before the next call can start. Any new
uncertainty stops progression.

The fresh profile has a 65-second admission safety deadline and a separate
40-second deadline from media readiness. A passing call must still have no more
than 45 seconds of actual metered transport. These are explicit new-plan limits;
they do not relabel the earlier plan's cutoff. Completed native hang-up clears
the active-call clock before private Settings confirmation is processed.

Before signup, the parent reads the two exact historical call hashes from the
running application's database inside a repeatable-read, READ ONLY transaction.
It verifies the original known totals, uncertain stream and token hold, zero old
active calls/unsettled voice credits and sufficient unchanged prior-owner native
allowance. Historical synthetic-owner identities are checked privately; only
counts, allowance summaries and a ledger fingerprint are reported. A fresh owner
isolates the fixture content and never supplies a quota reset. The old ledger is
fingerprinted again after cleanup, and any change fails this qualification.

The fresh run requires a new content-free run ID. An exclusive claim is written
before signup; that ID cannot be restarted automatically, including after a
pre-signup failure. The retained claim records whether calls may have started.
An additional immutable scope claim is keyed by the original ledger fingerprint
and the fresh profile. Choosing a different run ID cannot grant three more calls
against that same historical baseline. Both claims remain after failure. The
scope claim links to its run record and deliberately reports no live-started
boolean; only the execution record and actual ledger can establish that outcome.
Read-only preflight never claims a run or opens an account. Application source
SHA and each helper's SHA-256 are recorded separately, so preparation against
the deployed app revision does not pretend the new harness was already in that
application image.

Even a functional pass records `financial_qualified:false` and a null prior total
cost. It cannot establish invoice reconciliation, human microphone quality,
physical audibility, a physical phone or student acceptance.

Before any fixture, the harness requires current native remote audio attachment,
an attached audio element with a play attempt for that live remote track, a live
synthetic input track and outbound RTP. A silent worker may leave play pending
until its first reply; pre-speech readiness permits that. Post-response checks
still require resolved playback, live playing output and increased received
bytes/energy. The product admits only the leased agent's output audio and now
waits for its actual output subscription. An enabled textbox by itself is not
sufficient, and no arbitrary startup sleep substitutes for readiness. Completed
dialogue counts and revision numbers expose unexpected split-turn behavior
without retaining questions or answers.

Unexpected writes remain blocked. Diagnostics contain only an HTTP-method enum,
a predefined route template (or `unclassified`) and a pathname hash; query data,
bodies and arbitrary raw paths are never recorded. The one additional permitted
home restore write is exact POST `/workspace/timezone/initialize`, with exactly
the three expected keys, the current in-memory owner IDs and the browser's
validated timezone. Only its fixed route and count enter evidence. Ordinary
assistant session creation and provider writes remain blocked.

The parent then erases QA memory and disables History, ends any owned reservation,
logs out the same session and preserves billing/audit records. No email/calendar
write is permitted. No typed Vox message endpoint is permitted. A failed or
uncertain new bill stops additional calls; the earlier plan remains stopped and
the fresh profile never admits an automatic fourth new call.

## Current blockers

Read-only preflight at deployed 8b67620 found SSE host speech and all native model
credentials present. Realtime, text and TTS token price fields are populated.
Transport pricing/version are absent and remain USD UNKNOWN. Root clarified the
test limit as $2 known PROVIDER estimate plus at most 135 seconds of metered
transport; this requires no invented transport rate or policy change. Root also
verified official OpenAI documentation that exact `gpt-transcribe` bills duration
and its realtime completed event reports `usage={type:'duration',seconds:n}`.
The preflight therefore requires its positive registered duration rate, not
inapplicable token rates. Actual complete positive `billed_audio_ms` receipts
must be verified for every admitted call, and any unknown provider billing stops
additional calls. Missing new native source binding is expected on the old
source; a false background flag is not a native blocker. Owner allowance must be
checked after an authorized new test session; no actor balance
was queried by this preflight.

## Execution examples for the existing isolated host

Copy the prepared directory below `/srv/navox-vox-test/browser/voice-qualification`
only when root stages the test. Existing cached Playwright image/package are
available in that browser directory. Set no provider key or token on the command
line. The helper imports the existing guarded isolated ops module.

```bash
python3 /srv/navox-vox-test/browser/voice-qualification/run_on_isolated.py preflight FULL_SHA
```

Only root may decide that pricing/contract/source readiness is satisfied and issue:

```bash
python3 /srv/navox-vox-test/browser/voice-qualification/run_on_isolated.py authorized-run FULL_SHA --root-explicitly-authorized-live-provider-calls
```

For an approved continuation after exactly one settled call, root must provide
the independently verified prior usage values to both preflight and execution:

```bash
python3 /srv/navox-vox-test/browser/voice-qualification/run_on_isolated.py preflight FULL_SHA --profile neutral-private-only --prior-provider-usd VERIFIED_USD --prior-connected-ms VERIFIED_MS --prior-call-count 1
python3 /srv/navox-vox-test/browser/voice-qualification/run_on_isolated.py authorized-run FULL_SHA --profile neutral-private-only --prior-provider-usd VERIFIED_USD --prior-connected-ms VERIFIED_MS --prior-call-count 1 --root-explicitly-authorized-live-provider-calls
```

The placeholders are mandatory, non-secret billing metadata, not inferred
estimates. No command above authorizes itself; root still controls execution.

For the separately authorized fresh functional plan, use one new lowercase run ID
and preserve the exact historical manifest on both preflight and execution:

```bash
python3 /srv/navox-vox-test/browser/voice-qualification/run_on_isolated.py preflight FULL_SHA --profile fresh-three-calls --prior-provider-usd 0.0745244 --prior-connected-ms 48085 --prior-call-count 2 --prior-uncertain-streams 1 --prior-reserved-tokens 10525 --qualification-run-id voice-memory-20261027-01
python3 /srv/navox-vox-test/browser/voice-qualification/run_on_isolated.py authorized-run FULL_SHA --profile fresh-three-calls --prior-provider-usd 0.0745244 --prior-connected-ms 48085 --prior-call-count 2 --prior-uncertain-streams 1 --prior-reserved-tokens 10525 --qualification-run-id voice-memory-20261027-01 --root-explicitly-authorized-live-provider-calls
```

The second command consumes that run ID. Do not remove its claim or choose a new
ID to retry an uncertain run; independently review the retained usage and the
remaining authorized scope first.

`--stt-billing-shape duration` is the default only for exact `gpt-transcribe`,
whose duration contract root verified. It never adds missing prices or changes
policy. The preflight still refuses missing native source binding, missing SSE
speech, incomplete route fields, stale image labels, active calls, bad audio or
missing cached browser dependencies. Unknown transport dollars are reported,
never silently treated as free or added to the known provider estimate.
