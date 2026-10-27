# Prepared synthetic spoken-input qualification

This is a test harness, not application code or proof of a physical phone. It is
locked until root explicitly starts a run after deployment and pricing readiness.
Preparation and the read-only preflight made no native call, provider request,
signup, login, memory change, quota reset or runtime configuration change.

## Prepared files

- `fixtures.json`: five predefined synthetic utterances; no student content.
- `prepare_audio.py`: local macOS Samantha/afconvert synthesis only, PCM16 mono
  at 16kHz, with bounded leading/trailing silence. The prepared Mac fixtures have
  a maximum duration of 10.844 seconds. Regenerate after any manifest change and
  use the updated metadata/hash values.
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

The parent then erases QA memory and disables History, ends any owned reservation,
logs out the same session and preserves billing/audit records. No email/calendar
write is permitted. No typed Vox message endpoint is permitted. A failed or
uncertain bill stops additional calls; no automatic fourth call is available.

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

`--stt-billing-shape duration` is the default only for exact `gpt-transcribe`,
whose duration contract root verified. It never adds missing prices or changes
policy. The preflight still refuses missing native source binding, missing SSE
speech, incomplete route fields, stale image labels, active calls, bad audio or
missing cached browser dependencies. Unknown transport dollars are reported,
never silently treated as free or added to the known provider estimate.
