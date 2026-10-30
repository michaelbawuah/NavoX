# Calendar, memory and voice qualification

This record separates the source work from the accounts and devices needed to
qualify it. It does not declare M3–5 accepted. The existing isolated test site is
<https://vox-test.66-175-235-169.nip.io/>; main and public production are outside
this release scope.

## Read-only preflight

The preflight examined the isolated deployment of `8b67620`, whose application
files match `f95dfc8`. It inspected configuration presence and content-free
database counts, without opening provider accounts or making inference calls.

| Capability | Observed state | Required qualification |
| --- | --- | --- |
| Google calendars | No OAuth client ID/secret and no connected calendar. | Configure a test OAuth app through secret storage, register the exact HTTPS callback, then complete calendar-purpose read consent and separate scheduling consent. |
| Microsoft calendars | No OAuth client ID/secret and no connected calendar. | Configure a test app and tenant policy through secret storage; complete calendar-purpose consent. Exact stale-version deletion must be established before admitting writes that promise Undo. |
| Apple/iCloud calendars | No connected calendar. | Enter an app-specific credential in the secure Connections form, verify owned calendar discovery and busy-time reads, then give separate write consent. |
| Native Vox | Media and qualified LiveKit are enabled; the configured realtime, STT, LLM and TTS routes are present. All 99 observed calls were ended. | Recheck native pricing/allowance, then qualify the final source with bounded synthetic spoken input and actual provider output. |
| Background semantic learning | The ordinary AI gateway is disabled and its model registry is empty. | Publish a qualified, priced versioned extraction task and permit its personal-data sensitivity before enabling background processing. Native learning can use its separately admitted existing tool loop. |
| Physical phone | No physical-phone evidence was collected by this preflight. | Run the final release on a supported phone and observe actual microphone, speaker, interruption, reconnect, privacy controls and hang-up behavior. |

Google's existing callback path is `/api/v1/connections/google/callback`;
Microsoft's is `/api/v1/outlook/callback`. Deployment settings and registered app
callbacks must use the same exact HTTPS origin/path. Credentials belong in the
deployment's secret storage or the native secure credential form. ChatGPT's own
calendar connection does not grant NavoX calendar access.

## Provider action story

Start with a dedicated test account and student-controlled personal calendar.
Read busy time from every connected source. Verify identity, scope narrowing,
disconnect/revocation, complete coverage and fresh source revisions before
proceeding to writes. Busy-time consent alone must not admit a write.

For each supported write provider, review one neutral personal event with its
calendar, local date/time/timezone, title, location and provenance. Include a
separate case with a feasible reviewed study replacement. Confirm once and
verify exactly one provider event plus its durable receipt. Check the actual
created resource's first version, then verify exact unchanged-event Undo.
Independently prove that an intervening edit makes stale-version Undo fail
without deletion. Protocol support and live behavior must agree.

Exercise lost/ambiguous responses through synthetic transports before live use.
Never replay creation to resolve an uncertain outcome, infer absence from an
empty search, or restore a pre-action database over admitted/uncertain work.
Study moves and external event outcomes remain distinct; event Undo does not
restore study changes. Unsupported conditional deletion remains an explicit
capability limit.

## Bounded voice and memory story

The next live harness is preparation only until the final source and its pricing
checks pass. Use synthetic neutral speech, a fresh synthetic test account and
explicit retention consent. Limit the scenario to three short calls under the
existing allowance and configured routes; preserve usage receipts and limits.

The prepared harness caps each call at 45 seconds and the three-call transport
window at 135 seconds, with a $2 stop threshold for known provider estimates.
Transport USD remains explicitly unknown because the existing policy has no
transport rate; this is not a known all-in dollar cap. No pricing value is
invented or changed. The exact configured `gpt-transcribe` uses the registered
duration rate and must return duration usage, rather than being assigned missing
token rates. See the official [input transcription usage contract](https://developers.openai.com/api/docs/guides/voice-latency-cost).

Qualify actual audio input through STT or realtime input, model/tool handling,
host speech, browser playback and native hang-up. Observe content-free timing,
call/generation IDs, usage counters and result hashes. Exercise multi-statement
neutral learning, a distinct-call recall, a real private-review handoff and a
host-enforced prospective notice before permitted historical speech. A private
confirmed source must disappear from later voice recall and loaded context.

Save no diagnostic transcript, real voice recording, credential, cookie or
provider response body. Erase the synthetic account's test memory after checking
the result, close every call and test login, and remove temporary fixture audio
and processes. Synthetic microphone input proves an audio integration path;
human speech, physical audibility and phone acceptance need their own evidence.

## Final evidence

PR 59 records the exact branch, deployed image revision, migration rehearsal,
PostgreSQL/browser checks and complete CI outcome. New provider and semantic
source slices require their own evidence before being described as live support.
No preflight observation silently enables provider writes, a new model route,
background learning, local wake or native closed-phone ringing.
