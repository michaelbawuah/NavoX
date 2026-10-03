# Scoped conversation recovery rollout

The owner approved the unchanged conversation proposal from
`fc4b12ae4dba46dd9c6472edd48ad201b246253f` on 2026-10-03. Activation is conditional
on new qualification of OpenAI `gpt-5.6-luna` for `REASON` /
`ASSISTANT_INTERACTIVE`, `assistant_conversation@v1` prompt and schema,
`PERSONAL`, for the three exact principals in `provider-route-proposal.md`.

Preserve the existing eight operator task scopes and append only those three
conversation scopes. No provider, sensitivity, connector, speech, drafting, or
external-action grant changes. Fallback and shadow remain disabled. Each task
has a $0.01 ceiling, including any retry, and a 1,000 output-token ceiling. The
current adapter performs one HTTP request; there are no automatic retries for
this route. Existing workspace/user cost ceilings, provider cooldown, saved
request replay, and 200-turn session bound remain enforced. The text gateway
has no separate rolling aggregate-dollar or per-user rate admission control;
provider account limits and existing audio quotas must not be described as one.

Ordinary conversation contains only a question of up to 500 characters and up
to four exact, verified ordinary pairs from the same active owned native
session. Prior questions are limited to 500 characters and answers to 3,000.
Connector answers, tool results, drafts, attachments, and action references
cannot become conversation history. Credential rejection remains enforced.

The repair preserves full oversized input and explicitly rejects it before
submission, including microphone transcripts and wake-phrase suffixes. Output
character/token limit failures are explicit unavailable results, never
truncated answers or clarification. Named subjects copied from user text do
not grant a capability: only the existing validated single clarification plan
can use ordinary conversation, with verified ordinary follow-up provenance.
Current/private information still needs its authorized retrieval route.

## Controlled operator sequence

1. Capture ten fresh synthetic cases for the exact immutable binding; review
   ordinary answer quality and safety independently. Do not reuse fact-selection
   qualification for conversation. Preserve actual answers, request IDs, usage,
   latency, prompt/schema/model/catalog digests, and the review artifact.
2. Publish only appended conversation artifacts through the existing registry
   control. Preserve old evidence dates, scores, safety, and artifact digests
   when rebinding unchanged existing definitions to the new catalog revision.
   Compare every unchanged model/profile/prompt/schema definition first.
3. Record passing conversation evidence, append exactly the three task scopes,
   and preserve the full policy before/after, exact diff, and hashes privately.
   Check all three eligible principals and an excluded principal without
   making external actions.
4. Pass required local gates and all six exact-head hosted CI jobs, then merge
   through the normal protected branch process. Deploy green `main` using the
   existing IONOS prepare/activate procedure. No new migration is introduced.
5. Demonstrate greetings, thanks, explanations, writing, and a verified-history
   follow-up on `https://navox.net/navox`, recording actual results and latency.
   Send no email. Retain the previous release and pre-change environment/policy
   for rollback; disable the three added scopes before reverting the release
   if the route must be withdrawn.

Public News distribution/provisioning remains separate and must not delay this
repair. Private workspace filters stay intact. Do not invent stories or share
owner data. Ordinary new-user conversation needs a separate approved rollout
beyond these three test principals. This is a scoped conversation repair, not
completion of the entire NavoX product.
