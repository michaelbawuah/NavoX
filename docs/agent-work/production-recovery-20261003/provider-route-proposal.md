# Production recovery: proposed conversation permission

Status: **proposed; not enabled or approved by this document**. No provider policy
change or new qualification is claimed here.

The existing qualified `assistant@v3` / `assistant@v2` route selects saved facts
and forbids free-form answers. It cannot serve ordinary conversation. The recovery
brief requires explicit approval when no qualified, authorized conversation route
exists. Qualification for another prompt or schema cannot be reused.

## Exact proposed scope

| Field | Proposed value |
| --- | --- |
| Provider/model | OpenAI `gpt-5.6-luna` |
| Task/profile | `REASON` / `ASSISTANT_INTERACTIVE` |
| Prompt/schema | `assistant_conversation@v1` / `assistant_conversation@v1` |
| Sensitivity | `PERSONAL` |
| Input | Current question, at most 500 characters; at most four verified ordinary conversation pairs from the same owned native session |
| History bounds | Each prior question at most 500 characters; each answer at most 3,000 characters |
| Output | One ordinary answer, at most 3,000 characters; no action authority |
| Budget | Maximum $0.01 per task; maximum 1,000 output tokens |
| Fallback/shadow | Disabled |

The operator policy would retain its existing eight task scopes and append exactly
three scopes with the task/profile/prompt/schema/sensitivity above:

| Principal | User ID | Workspace ID |
| --- | --- | --- |
| Owner | `b1842045-a1a1-499b-ae41-f53bcb99d0f9` | `aad0f47a-4948-44df-8e52-60f6d7a918e3` |
| Controlled canary | `45f3b9b1-8c67-4e40-885a-49209e77015f` | `b3128162-94cb-4db2-a098-5ef351a16a37` |
| Isolated fresh acceptance account | `2a328833-324b-4c27-85ea-69e939883e0e` | `39c16ca5-97b2-48bb-8948-64971eb7cc63` |

This proposal does not grant conversation access to all present or future accounts.
It does not add providers, remove the principal allowlist, or change existing
planner, drafting, speech, connector or exact-action approval permissions.

## Enforcement and activation

The proposed route contains no tool, email, communication-draft or connector
bodies. History must match completed ordinary-conversation tasks in the same owned
native session and the stored commitments to their exact question/answer text.
Ownership and session status are checked again when context is built. Credential
rejection and sensitivity enforcement remain in effect. No new `SENSITIVE` or
`RESTRICTED` grant is proposed; personal content is not relabeled `PUBLIC`.

After explicit approval, qualify only this exact model/task/profile/prompt/schema
binding using the existing minimum qualification and safety gates. Keep the route
unavailable unless qualification and all policy checks pass. Preserve the reviewed
prompt/schema digests, qualification evidence, exact before/after policy and digest,
and scoped task/trace/provider request evidence. Do not send email to demonstrate
ordinary conversation.

## Separate News blocker

Conversation permission does not authorize redistribution of news content or news
summarization. A default feed for fresh accounts still requires a source whose
agreement permits the intended public distribution, plus the existing content
rights and evidence checks. An owner-scoped source or metadata-storage permission
does not establish that entitlement. Do not remove private workspace filters or
copy the owner's News data to other users. Until a permitted public source is
confirmed and provisioned, public News remains an independent provisioning blocker.

## Requested approval

Approve only the proposed OpenAI conversation binding and the three exact
principals above, including its minimum qualification and controlled acceptance
checks. Preserve the stated bounds, existing eight scopes, sensitivity enforcement,
audit evidence and action approvals. This approval would not cover public News
distribution, additional sensitive connector content, or default access for other
accounts.
