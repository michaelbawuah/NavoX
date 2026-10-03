# M12 adaptive response modality — worker report

**STATUS: ready_for_review**

Task `/root/spec008_modality`. Workspace `/Users/mba_steins/NavoX`, branch
`spec-008-navoxbot`, baseline `8cd61b683ab69ec00b6f15028021644fb24d1b5f`. The
shared tree was already dirty with accepted SPEC-006/007 and SPEC-008 M1–M11D
work, and `docs/agent-work/spec-008/CHECKPOINT.md` was edited concurrently by
root while this bundle ran; no pre-existing change was reverted, and CHECKPOINT
was not touched by this worker. No commit, push, deploy, publication, live/paid
provider call, secret or grant change, or production migration was made.

## What changed

One saved response now carries a `TEXT` / `VOICE` / `BOTH` decision that is
derived from the submitted modality, a bounded delivery-preference layer and the
existing presentation plan. Delivery never carries authority: it cannot name a
capability, a provider, a model, a voice or an action, and the client still
supplies no speech text.

| Path | Change |
| --- | --- |
| `packages/assistant-runtime/src/delivery.ts` (new) | `resolveDeliveryIntent(text)` classifies one utterance as `AUTOMATIC`, `SPEAK` or `SUPPRESS`, with `cue_only` and a bounded `ordinal`. Suppression is checked first so "don't read it to me" cannot be read as a read-aloud cue. Short forms such as "read it" count only as the whole utterance. A cue phrase in a longer utterance must start a clause or follow a bounded lead-in, so "he read it to me" and "I read it again" stay ordinary questions. The utterance is never rewritten, so upstream question spans stay exact. |
| `packages/assistant-runtime/src/presentation.ts` | `summarizeForSpeech` builds the spoken text from whole sentences of the saved blocks **in saved block order**, stopping before the bound and preferring a word boundary inside a single long sentence. `hasSpokenBlock` keeps the old offer rule (a response needs an answer or a notice). `decideResponseModality` and the reworked `buildPresentationPlan` produce `TEXT`/`VOICE`/`BOTH`, record the delivery preference on the plan, and store `speech_text` only when playback may start by itself. |
| `packages/assistant-runtime/src/runtime.ts` | `submitTurn` classifies delivery before any planning. A cue-only utterance is answered from this session's saved turns: a read-aloud request re-presents the most recent eligible answer (or a bounded ordinal) as a `PRESENT` delivery turn whose plan is `assistant.delivery` and whose decision delegates nothing; unknown, out-of-range, non-ready and approval-required referents produce a clarification instead. A cue-only utterance makes no SPEC-005 and no source call. An embedded cue still routes the question normally and only changes playback. Both branches share one failure path, so a store error while reading the saved conversation becomes a qualified turn and resolves the durable claim instead of leaving it to expire. |
| `packages/assistant-runtime/src/voice.ts` | `VoiceSessionState` gains the visible `voiceMode` preference and `SET_VOICE_MODE`; `voiceModeAllowsSpeech` is the pure preference check. `spokenTextForTurn` now returns the saved summary, or re-derives the same coherent summary from the saved blocks, so an explicit Read aloud of a silent typed answer is bounded and whole-sentence. |
| `packages/assistant-runtime/src/validate.ts`, `capabilities.ts`, `packages/contracts/src/assistant.ts` | The `assistant.delivery` intent, the `PRESENT` decision kind and the `AssistantDeliveryIntent` field on `AssistantPresentationPlan` are added to the shared vocabulary. `assistant.delivery` maps to no capability in the registry, so it can never delegate, and `PRESENT` never requires approval. `parsePresentationPlan` validates the delivery field and reads a legacy row as `AUTOMATIC`. No SPEC-005 prompt or schema revision was changed. |
| `apps/web/src/lib/assistant-controller.ts` | The runner passes the saved turn's **server-recorded** delivery preference as `explicit`; it never inspects the question text. `speechStartForTurn(state, explicit)` is the single rule the page uses: an explicit request is `MANUAL` (works after Stop and without a microphone, mute still vetoes), a spoken question is `AUTOMATIC` only while Voice Mode is on, and a muted/stopped/unsupported session is `NONE`. |
| `apps/web/src/components/navox-assistant.tsx` | Adds the visible Voice Mode toggle beside Mute, keeps Stop and Mute as the cancel controls, and shows a short status line while Voice Mode is off. Typed input and the per-turn Read aloud control are unchanged and stay available when playback is unavailable. |
| Tests | `packages/assistant-runtime/src/delivery.test.ts` (new), `presentation.test.ts`, `voice.test.ts`, `runtime.test.ts`, `news.test.ts`, `apps/web/src/lib/assistant-controller.test.ts`, `assistant-synthesis-route.test.ts`, `apps/web/src/components/navox-assistant.test.tsx`. |

## Behavior contract

- Typed question, no cue: `TEXT`, `speak=false`, no `speech_text`, no playback.
  The client never auto-speaks it, and the explicit Read aloud control remains.
- Spoken question: `BOTH` — the same detailed visual blocks plus a bounded
  spoken answer. The page starts playback only while Voice Mode is on and the
  session is not muted, stopped or unsupported.
- An explicit read-aloud cue inside a **spoken** utterance is also `SPEAK`: the
  server records it on the saved plan, so the page speaks it even with Voice
  Mode off. Only mute and Stop still cancel it.
- "read it to me" (or a bounded variant): the most recent eligible answer in the
  same authenticated session is re-presented as a new read-only turn with
  `PRESENT` and speaks through the M11D saved-turn route. "read the second one"
  resolves the named saved turn; an absent or ineligible one clarifies.
- "don't read it aloud" (embedded or alone): the answer is rendered in full and
  is never spoken. A standalone cue is acknowledged and makes no provider call.
- Long or compound answers: full visual detail is preserved and the spoken text
  is whole sentences, ≤600 characters, derived only from the saved response.
- Approval-required, `WITHHELD`, `UNAVAILABLE` and answerless turns stay
  unspeakable, including on an explicit request; the M11D route still refuses
  them independently.
- A transcript of "yes", "send it" or "approve" carries no delivery cue and no
  approval field, so it routes as an ordinary question and cannot act.

## Verification

All commands ran from the repository root on the complete working tree. Full log:
`/tmp/navox-spec008-m12-js-gate.log`.

| Command | Exit | Result |
| --- | --- | --- |
| `npm run lint` | 0 | extension, web (149 files), assistant-runtime (54 files). The 9 `subscriptions-dashboard.module.css` `noDescendingSpecificity` warnings are pre-existing; no errors. |
| `npm run typecheck` | 0 | extension, web, assistant-runtime, connector-sdk, contracts, ui. |
| `npm test` | 0 | extension 8/8; web 368/368 in 40 files (was 361/40); assistant-runtime 263 passed, 5 skipped, 1 skipped file (was 237 passed). |
| `npm run build --workspace=@navox/web` | 0 | Next compiled; all assistant routes emitted, including the saved-turn speech route. |

The 5 skipped assistant-runtime tests are the disposable-PostgreSQL
`store.integration.test.ts` cases; the skipped file is the same suite. No
browser/device playback, no live provider request and no hosted CI run was
performed. The SPEC-005 planner and its prompts/schemas are untouched, so no
Python check was needed; root still owns `bash scripts/check-api.sh`, hosted CI
on the exact head and live acceptance.

M12-focused coverage added: typed/Voice-Mode-off silence; spoken → `BOTH` with
the saved blocks retained; explicit read-aloud of the most recent eligible
answer with zero planner or source calls; unknown ordinal; ineligible referent;
embedded suppression that still routes the question; standalone suppression with
zero provider calls; a long silent answer speaking whole sentences within the
bound; the route deriving the same summary for a silent typed turn; Voice
Mode/mute/Stop/explicit precedence; the visible Voice Mode control; an explicit
cue in a spoken turn recorded as `SPEAK`; suppression winning over the
read-aloud phrase it contains; saved block order preserved in the summary; and
statement-shaped phrases ("he read it to me") left as ordinary questions.

## Correction cycle

One batched reviewer correction was applied on top of the first bundle.

1. **An explicit cue in a spoken utterance now speaks even with Voice Mode
   off.** The first bundle derived `explicit` from `modality !== "VOICE"`, which
   lost the intent for a cue-only `PRESENT` turn and for an embedded cue in a
   spoken question. `AssistantPresentationPlan` now carries a
   server-validated `delivery` field, `buildPresentationPlan` records it, and
   the runner reads it instead of inspecting the question text. Tests cover a
   spoken cue recorded as `SPEAK`, an explicit flag reaching the page for a
   spoken turn, an embedded cue still routing to its owning service, and mute
   still vetoing an explicit request.
2. **The spoken summary follows the saved block order.** `speechLines` grouped
   answer, details and notice instead of walking the saved sequence; it now
   walks the blocks once and a test asserts an interleaved answer/detail/notice
   summary keeps that order.
3. **Cue classification rejects statement-shaped false positives.** A cue
   phrase in a longer utterance must start a clause or follow a bounded
   lead-in, so "he read it to me" and "I read it again" stay ordinary questions
   while "and read it to me" and "do not read it aloud" still classify.
4. **A cue-only store failure now takes the claim-safe path.** The cue-only
   branch read the saved conversation outside the routing branch's
   `try`/`catch`, so a store error could leave the durable claim until its TTL
   and surface as an unhandled failure. Both branches now share one failure
   path: an `unauthorized` failure releases the claim and rethrows, and any
   other failure becomes a qualified `UNAVAILABLE` turn with the
   `assistant.delivery` plan, which resolves the claim through the normal insert.
   A focused regression makes `listTurns` fail once and asserts the qualified
   turn, no provider call, and `replay: true` for the same request id.

## Decisions worth a reviewer's attention

1. **Delivery is classified in TypeScript, not in the SPEC-005 planner.** The
   brief allows a small deterministic delivery layer, and keeping it out of the
   planner leaves every published prompt and schema revision frozen. General
   capability routing is untouched: an embedded cue still sends the operator's
   exact words to the planner, and a suppression cue cannot cause or suppress a
   delegation.
2. **A delivery turn re-presents the saved answer instead of copying its
   decision.** The new turn carries `assistant.delivery` and `PRESENT` with no
   capability, so the client's email-draft and approval affordances cannot
   attach to it. `action_refs` stays empty.
3. **A silent turn stores no speech text.** `speech_text` is written only when
   playback may start by itself; the saved-turn speech route re-derives the same
   coherent summary from the saved blocks. This avoids a second stored copy of
   the answer and keeps one derivation shared by both paths.
4. **The client reads a server-recorded delivery signal.** `explicit` comes from
   `presentation.delivery === "SPEAK"` on the saved turn, not from re-scanning
   the question on the client, so a spoken explicit cue is honored even when
   Voice Mode is off and the client never re-derives intent from text it did not
   validate. The field is server-derived, stored with the plan, validated on
   read, and is not accepted in any request body.
5. **Voice Mode is a client presentation preference.** It lives in the voice
   session state and gates automatic playback of spoken answers only. An
   explicit "read it to me" outranks the default, because otherwise the
   operator's own instruction would do nothing. It is session-scoped and
   resets on reload; persisting it was not in this brief.
6. **A cue-only request is still a saved turn.** It is answered from saved state
   and stored so the M11D route can speak the referenced answer by selector.
   Its `question` is the operator's own request and its plan is the
   non-delegable `assistant.delivery` route.
7. **A spoken clarification is spoken.** When a cue-only read-aloud request
   needs clarification, the clarification notice is itself eligible, so the
   operator hears why nothing was read. This is a bounded consequence of the
   existing eligibility rule.

## Outstanding risks and acceptance limitations

- No real browser or device playback was exercised, and no live or paid speech
  provider request was made. The M11D limitations still apply: autoplay policy,
  real MP3 decoding, an operator-published synthesis model and hosted CI on the
  exact head are all still owed before this is called live-accepted.
- The delivery layer is phrase-bounded. Natural forms outside its cue list (for
  example "keep it quiet") are treated as ordinary questions and carry no
  delivery effect; the explicit Read aloud control and Mute remain available. A
  cue phrase in a longer utterance must start a clause or follow a bounded
  lead-in, which trades a few unusual phrasings for fewer false positives.
- `spokenTextForTurn` still derives item-only responses as unspeakable, matching
  the pre-M12 offer rule. A response that renders only items or links has no
  sentence to read.
- `VoiceSessionDeps.synthesize` and the M11D route are unchanged, so a speech
  provider failure still leaves the typed answer usable.

## Next checkpoint

Root owns the batched acceptance review, `bash scripts/check-api.sh` against a
disposable PostgreSQL/Temporal stack, hosted CI on the exact head, and the
browser/device pass on `/navox` once SPEC-005 has an eligible synthesis model
inside the operator's budget. No further worker action is requested.
