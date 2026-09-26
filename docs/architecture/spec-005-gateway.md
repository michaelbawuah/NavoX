# SPEC-005 gateway operator guide

## State and ownership

Read [the acceptance status](spec-005-foundation.md) before enabling traffic.
Catalog, policy and evaluation publication are operator CLI operations; browser
preferences cannot grant access to data or promote a model.

The database stores immutable catalog revisions and projections, policies,
health, evaluations and task attempts. Publication checks the current revision.
Model/catalog changes invalidate old evaluations; prompt/schema changes require
a new version. Migrations `0024_ai_registry` and `0025_ai_drafts_sessions` follow
SPEC-004's `0023` migration. Run the normal migration service before the updated
API/worker. Downgrade removes the new data and is not a traffic rollback method.

## Configuration and catalog

Keep `AI_PROVIDER=disabled` while preparing the catalog. Existing deployments may
retain `AI_PROVIDER=openai` for the legacy extractor. The new path is selected
explicitly with `AI_PROVIDER=automatic`.

Configure the intended providers' protected server credentials: `OPENAI_API_KEY`,
`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`. Never put a credential in a
registry, prompt, evaluation, browser form or PR. From `services/api`, generate
an empty reviewable catalog:

```sh
uv run python -m navox.ai.manage template --output /tmp/navox-ai-catalog.json
```

The template contains extraction and reply-draft prompt/schema contracts and all
logical profiles. Other domain prompts are preliminary grounded-answer templates
requiring feature-specific contracts/review. It contains no models, assignments,
prices or provider grants.

Add exact model definitions and compatible profile assignments to the JSON after
reviewing current official model IDs, token limits, capabilities and USD token
prices. Keep that review with deployment evaluation artifacts. Provider values
are `openai`, `gemini`, `anthropic` and `xai`. A model is disabled with no allowed
sensitivity classes unless explicitly configured. Publish with the expected
current revision (`0` for a new installation):

```sh
uv run python -m navox.ai.manage publish \
  --file /tmp/navox-ai-catalog.json --expected-revision 0
uv run python -m navox.ai.manage status
```

New assignments start at zero traffic with shadow disabled. Publication does
not evaluate or promote a model. `status` reports IDs, revision, traffic and
health codes. Legacy `OPENAI_MODEL` does not select automatic-mode models.

The four adapters currently support text plus structured JSON only. Registry
claims cannot enable unimplemented adapter capabilities. Different returned and
requested model IDs are rejected; verify stable model attribution against the
provider's current API before assigning that model.

## Policy and context boundaries

`AI_PROVIDER_POLICY` is JSON. Default `{}` denies all automatic calls, fallback
and shadow. A minimal synthetic-evaluation example is:

```json
{
  "grants": [{"provider": "openai", "sensitivities": ["PUBLIC"]}],
  "max_cost": "0.25",
  "allow_fallback": false,
  "max_fallbacks": 0,
  "allow_shadow": false
}
```

This does not permit personal-mailbox processing. Connector sources are at least
`PERSONAL`; reviewed connector configuration may require `SENSITIVE` or
`RESTRICTED`. Connector read permission is not a grant to send data to AI.

Effective grants intersect the server-created task, operator policy and any
saved workspace/user policies. Each layer can narrow the result, never broaden
a stricter layer. Absent saved policy inherits the operator ceiling; an explicit
empty grant list denies all. Fallback and shadow need every applicable layer's
permission and share the original task budget.

Workspace/user policy files contain `PolicyRules` JSON without secrets. Publish
with the expected policy revision (`0` for a new row):

```sh
uv run python -m navox.ai.manage policy \
  --workspace-id WORKSPACE_UUID --file /tmp/navox-ai-policy.json \
  --expected-revision 0
```

Add `--user-id USER_UUID` for a member-specific policy. Browser preferences only
change preference/fallback within existing grants. Preference never bypasses
scope, sensitivity, capability, quality, health or budget gates.
Preferences are stored separately from operator per-user limits. Disabling and
reenabling a fallback preference cannot overwrite those limits.

Context selection is explicit and bounded. Ownership, membership, pause state,
active source, read capability, source revision and classification are checked
before and after model calls. Source metadata, model text and tool proposals do
not authorize actions. JSON Schema remote references/rebasing are rejected and
domain validation is mandatory.

## Evaluation and promotion

The evaluator runs the same 33 synthetic SPEC-002 extraction cases for each
provider. It measures failures, schema validity, expected fixture outcomes,
latency and estimated usage cost. It does not read personal documents or execute
tools. Authored fixture reports cannot qualify a live model.

The following is an opt-in paid provider request. Run only with an approved
provider, exact registered model and budget; replace `EXACT_MODEL_ID`:

```sh
uv run python -m navox.ai.manage evaluate --live \
  --model openai:EXACT_MODEL_ID --profile EXTRACTION_HIGH_ACCURACY \
  --max-cost 0.25 --output /tmp/navox-ai-evaluation.json
```

Repeat for the other approved providers without changing the corpus. Policy must
permit `PUBLIC` for that provider. Missing prices, permissions or budget stop
calls. Failed/incomplete cases remain failures; unknown usage/cost remains
unknown, not zero. Evaluation never sends email, records qualifying evidence or
enables traffic automatically. Record a complete, bound live-provider report:

```sh
uv run python -m navox.ai.manage record-evaluation \
  --file /tmp/navox-ai-evaluation.json
```

Evidence binds catalog revision, model digest, profile, task, prompt and schema.
This smoke corpus does not measure production extraction precision/recall or
grounding. Communication, planning and assistant profiles have no CLI report
qualification path yet; they need profile-specific evaluation work before
activation. Do not reuse extraction scores or seed invented metrics.

Promotion requires enabled healthy models and fresh passing evidence: at least
ten samples, quality 0.90, reliability 0.95, safety passing, at most thirty days
old. These are serving floors, not production-quality acceptance. Advance one
stage at a time: `0 → 5 → 25 → 50 → 100`.

```sh
uv run python -m navox.ai.manage rollout \
  --model openai:EXACT_MODEL_ID --profile EXTRACTION_HIGH_ACCURACY \
  --expected-revision 1 --expected-percent 0 --percent 5
```

Observe attempts and task quality before each stage. A new failing evaluation
atomically sets zero traffic and disables shadow. A later success never restores
traffic automatically. Assignment `--shadow` plus `allow_shadow` in every policy
permits a bounded extra comparison. It shares the original budget, checks the
same context authorization, discards outputs and cannot perform actions. Shadow
incurs provider usage.

## Rollback and failures

Return to zero using the actual current revision and percentage; omit `--shadow`
to disable it too:

```sh
uv run python -m navox.ai.manage rollout \
  --model openai:EXACT_MODEL_ID --profile EXTRACTION_HIGH_ACCURACY \
  --expected-revision 1 --expected-percent 25 --percent 0
```

For a deployment-wide stop, set `AI_PROVIDER=disabled` and restart API/worker
through the normal deployment workflow. Do not drop migrations. Total failure
returns unavailable; no fabricated response replaces missing results.

Rate limits and repeated failures persist across workers. Only one recovering
model probe can claim a half-open lease. Authentication/invalid-request failures
disable a candidate until the operator corrects the cause and explicitly probes
the exact configured model:

```sh
uv run python -m navox.ai.manage probe --live \
  --model openai:EXACT_MODEL_ID --expected-revision 1
```

The probe requests the provider's model list without personal context. Success
clears the circuit but sets all that model's assignments to zero traffic and
disables shadow. Evaluation and explicit promotion are still required; failed or
stale probes do not restore health. Retry/fallback never expands permissions.

## Drafts, sessions and telemetry

The Today draft panel uses one authorized Gmail source and preserves versions
across edits/regeneration. Exact sender, recipient, subject and body are shown
before final approval, which binds draft ID, version and payload hash to the
existing action contract. An edit blocks the old action. The worker rechecks the
version, hash and live source permissions after token refresh. Generation never
sends email. Unsupported CC/BCC/attachments fail validation. A real send still
requires explicit user approval.

Draft bodies are user-reviewable content, not telemetry. Source erasure removes
unsent history transactionally and refuses to erase an in-flight approved send.
Completed actions follow existing action retention controls.

Sessions store NavoX-owned IDs and scoped opaque artifact references, not external
conversation IDs. Appending needs a matching validated successful task. Action
references start empty. Future artifact dereferencing needs independent access
checks; an opaque reference grants no permission.

Attempts record scope/trace IDs, prompt/schema/catalog versions, provider/model,
status/error, latency, fallback count, usage and estimated cost. They contain no
source, prompt, response, email body or credential. Costs use catalog estimates,
not billing reconciliation. Scoped summaries preserve unknown costs. Operational
trace-coverage percentages need measurement over a real acceptance run.

## Official API references

Recheck before registering or changing a live model:

- https://developers.openai.com/api/docs/guides/structured-outputs
- https://ai.google.dev/api/generate-content
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- https://docs.x.ai/developers/model-capabilities/text/structured-outputs

No provider SDK dependency is added. HTTP adapters are the sole new external
model-call boundary; the legacy adapter remains behind explicit `openai` mode.
