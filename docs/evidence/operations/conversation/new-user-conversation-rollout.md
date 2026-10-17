# Ordinary personal-account conversation rollout

On 2026-10-04 the owner requested that other people who sign in can use NavoX,
after a friend received an unavailable ordinary-conversation answer. This
supersedes the three-principal test boundary for ordinary conversation only.
It does not broaden speech, connectors, drafting, external actions, or News
content permissions.

## Exact activation change

Append `"allow_personal_conversation": true` to the existing operator
`AI_PROVIDER_POLICY` JSON. Preserve its eleven explicit task scopes, provider
grants, $0.01 ceiling, disabled fallback and shadow, and all other fields.
The new field defaults to false and is operator-only. Record the exact raw
before/after JSON, canonical hashes, and field diff before activation.

This switch permits a derived in-memory scope for the current authenticated
owner of an active personal workspace, only after the server verifies an
active owned PERSONAL AssistantSession and ordinary ConversationContext.
The derived store is bound to that exact task and session UUID. Generic AI
feature stores receive no entitlement. Workspace and per-user policy denials
remain effective; signup does not write provider grants or policy rows.

The public binding is pinned to OpenAI `gpt-5.6-luna`, REASON /
ASSISTANT_INTERACTIVE, `assistant_conversation@v1` prompt and schema,
PERSONAL, TEXT + STRUCTURED_OUTPUT, INTERACTIVE / HIGH. Each current question
is at most 500 characters. At most four ordered ordinary history pairs must
match audited completed tasks and exact stored question/answer commitments
in the same session. Questions are bounded to 500 and answers to 3,000.
Output remains at most 3,000 characters and 1,000 tokens; the task ceiling is
$0.01, including any retries. Fallback and shadow remain disabled.

No connector bodies, tool results, drafts, attachments, source documents, or
connected-data summaries become conversation context. Credential rejection,
sensitivity checks, paused-user denial, model availability, exact binding
qualification, and lower policy ceilings remain enforced. Current/private
information still uses authorized retrieval and cannot be invented by this
ordinary route. No external action is approved or executed by conversation.

## Evidence and deployment

Reuse the existing passing qualification for this identical immutable binding
and r10 catalog. Do not copy qualification from fact selection or modify the
prompt/schema/model/catalog to widen principal eligibility. Preserve their
existing digests and evaluation provenance.

Before pushing, run the complete repository-required API and web/SDK gates,
review the final diff, and preserve the results. Require all six hosted CI jobs
on the exact PR head, merge through protected main, then require green main
before normal IONOS activation. No migration is introduced by this rollout.
Capture the current web/API/worker immutable images, environment and release
pointer before preparing the build. Turning the switch off withdraws new-user
conversation access; restore the saved policy and images for full rollback.

Demonstrate greeting, explanation/writing and verified-history follow-up using
a newly created standard personal account outside all eleven explicit scopes.
Preserve request/task IDs, actual answers, timing, account/session isolation,
model/binding/cost evidence and no-action results privately. Do not send email.

## Separate public News provisioning

Owner-bound Perigon connectors are not public News credentials. Their private
workspace data and activation ownership checks stay intact. Source templates
that a user cannot activate must not be offered as usable options, and source
errors must not be mislabeled as missing stories. A truthful friendly empty
state remains necessary until a production feed agreement permits distribution
and the intended text/image/summary uses. The personal/evaluation trial and
metadata permission do not establish those rights. This blocker must not delay
the independent ordinary-conversation rollout.
