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

The template contains extraction, reply-draft and typed v2 operational
prompt/schema contracts plus all logical profiles. Historical generic v1 domain
registrations remain immutable. It contains no models, assignments, prices or
provider grants. Registration alone never qualifies a model for traffic.

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
grounding. Do not reuse extraction scores for other tasks or seed invented metrics.

The current extraction rubric is `spec005-extraction-smoke.v3`. It accepts a
grounded, consequential alert without inventing a user action and checks the
expected email basis as well as intent. Historical v1 reports remain readable but
cannot qualify a model under v3. The fixtures and production validators are unchanged.
Known token cost is retained even when application validation rejects a response.

For a failed case, capture only selected fixed synthetic inputs and proposals:

```sh
uv run python -m navox.ai.manage diagnose --live \
  --model openai:EXACT_MODEL_ID --profile EXTRACTION_HIGH_ACCURACY \
  --case explicit-waiting --case service-outage \
  --max-cost 0.03 --output /tmp/navox-extraction-diagnostic.json
```

This requires an explicit output file and the same provider, sensitivity and budget
checks as evaluation. It reads no mailbox and changes no catalog or traffic state.
The file contains synthetic sources, untrusted model proposals, expected categories,
outcome reasons and fixed validation codes; review proposals as data, not instructions.
Configured credentials and credential-like output are rejected before capture.
Terminal progress contains only case IDs and fixed outcomes. Diagnostic reports
cannot be recorded as qualifying evidence, even if every selected case passes.
After investigating the failure, rerun the complete corpus for qualification.

For intermittent failures, capture rejected proposals from the same full evaluation
instead of trying to reproduce them in a second provider call:

```sh
uv run python -m navox.ai.manage evaluate --live \
  --model openai:EXACT_MODEL_ID --profile EXTRACTION_HIGH_ACCURACY \
  --max-cost 0.25 --output /tmp/navox-ai-evaluation.json \
  --diagnostic-output /tmp/navox-ai-failures.json
```

The two paths must identify different files. This performs the normal 33 calls
once, under the same total budget and validation rules. The ordinary evaluation
report and terminal contain measurements only. The separate diagnostic file holds
only failed cases and any credential-screened synthetic proposals available for
them; a provider failure may have no proposal. Both reports share the evaluation
timestamp and model/catalog binding. If every case passes, the diagnostic has an
empty `cases` array. It never qualifies a model, replaces a failing score, records
evidence or enables traffic. It is opt-in; default evaluation retains no proposals.

Drafting has a separate fixed 16-case synthetic corpus, covering factual grounding,
unknowns, changed dates, user edits, recipients, unsupported attachments and source
injection. Run the same corpus against each approved provider:

```sh
uv run python -m navox.ai.manage evaluate --live --corpus communication \
  --model openai:EXACT_MODEL_ID --profile ASSISTANT_INTERACTIVE \
  --max-cost 0.25 --output /tmp/navox-drafting-review.json
```

The default remains `communication_draft@v1`. To evaluate the reviewed v2 prompt,
first publish it through a new catalog revision, then explicitly select it:

```sh
uv run python -m navox.ai.manage evaluate --live --corpus communication \
  --model gemini:EXACT_MODEL_ID --profile ASSISTANT_INTERACTIVE \
  --draft-prompt-version v2 --minimum-start-interval-seconds 6 \
  --max-cost 0.25 --output /tmp/navox-drafting-v2-review.json
```

V2 preserves the v1 schema and the entire original 16-case corpus. Missing or
unsupported prompt versions fail before a provider request. Pacing is outside
the measured request/validation latency, and rate limits stop further calls for
that model without retrying. No report is automatically reviewed or recorded.
Published v1 bytes and historical v1 review digests remain unchanged. Adding v2
advances the catalog revision: prior evaluations remain in the audit history but
do not qualify traffic under the new revision. Application drafting now requests
v2 explicitly; the CLI retains its historical v1 default, so fresh v2 runs must
include `--draft-prompt-version v2`. Missing v2 registration or qualification
fails closed. Source installation does not promote assignments.

This command requires an output file because the review artifact contains the
synthetic source, rubric and generated draft for each case. It never reads a
mailbox, sends a message or prints candidate bodies to the terminal. Review
artifacts are explicit evaluation captures, not ordinary task telemetry.

Inspect each candidate against its source, instructions, prior draft and rubric.
Fill only the `review` section: `reviewed_by`, timezone-aware `reviewed_at`, and
each case's boolean `grounded`, `instructions_followed`, `edits_preserved` and
`no_unauthorized_action_claim`. For cases without a previous draft,
`edits_preserved` means no conflict with the explicit user instructions. Mark
failed/missing candidates false; never fill all verdicts mechanically. Keep
`review.report_digest` and every measured field unchanged. Any changed candidate,
corpus, model or measurement invalidates the review binding. Submit the reviewed
file with `record-evaluation` as above.

Incomplete reviews and offline fixture reports are rejected. A passing score
qualifies only `draft_communication` with its exact evaluated, published
`communication_draft@v1` or `communication_draft@v2` prompt and v1 output schema in
`ASSISTANT_INTERACTIVE`; it cannot qualify general assistant reasoning. Every
safety case must pass, and an unauthorized-action claim in any case disqualifies
the candidate. Stored evidence contains aggregate metrics and the reviewed
artifact's digest, not candidate bodies. Retain the reviewed artifact alongside
the acceptance records. Operator review is an attestation, not a cryptographic
proof of model provenance or a substitute for the final exact-send demonstration.

Planning, meeting preparation, assistant reasoning and ranking each have a fixed
12-case corpus and typed v2 fact-selection contract. Select their exact `--corpus`
and profile as listed in [the final acceptance procedure](spec-005-live-acceptance.md).
They evaluate bounded saved-state relevance, missing facts, safe preparation-tool
selection, ordering and source-injection cases. Structural validity alone is not
a quality pass. Full reports capture the selections and recompute quality before
recording; offline fixtures and incomplete suites cannot qualify. These synthetic
scores are not a general reasoning or production-accuracy benchmark.

The Today “Explore your tasks” panel calls `/api/v1/ai/operations/{domain}` with
1–12 explicitly selected owned items and a bounded user request. It returns
resolved saved facts, preparation proposals, meeting briefs or advisory order.
It never updates saved tasks, creates actions or executes tools. Imported source
permissions/classification and saved-state fingerprints are rechecked around each
provider call. Raw email bodies are not fetched by this feature. Provenance that
cannot be resolved to an active registered source fails closed. Descriptions are
bounded to their first 2,048 characters; these are saved-state answers, not a
fresh-source investigation. Existing Handle/Today ordering is unchanged.

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
conversation IDs. The selected-state assistant creates owned sessions and appends only matching
validated successful tasks, even when the serving provider changes. Action
references start empty. Future artifact dereferencing needs independent access
checks; an opaque reference grants no permission.

Attempts record scope/trace IDs, prompt/schema/catalog versions, provider/model,
status/error, latency, fallback count, usage and estimated cost. They contain no
source, prompt, response, email body or credential. Costs use catalog estimates,
not billing reconciliation. Scoped summaries preserve unknown costs. Operational
trace-coverage percentages need measurement over a real acceptance run.

## Extraction follow-up from the September 26 live comparison

The owner-provided revision-2 archive (`20260926T151025Z`) has paired full
measurement reports and failure captures. All model/catalog/corpus/timestamp
bindings and captured source payloads were checked; rejected proposals reproduce
the reported evidence-validation failures locally.

| Model | Passed / planned | Provider responses | Finding |
| --- | --- | --- | --- |
| OpenAI GPT-5.6 Luna | 32 / 33 | 33 | A valid direct request was scored against only the assigned-obligation label. |
| Gemini 3.5 Flash-Lite | 29 / 33 | 33 | Three grounding failures and an owner promise misclassified as a state update. |
| Claude Haiku 4.5 | 8 / 33 | 33 | Mostly metadata-only person names with unrelated citations, plus missed or misclassified facts. |
| Grok 4.7 | 11 / 33 | 13 | The same request-label mismatch, then a response cost above its reservation; 20 cases skipped. |

None of these runs was recorded as qualifying evidence. Their historical scores
remain unchanged. At the owner's request, Grok is excluded from further live
qualification and is to be removed from the active deployment catalog, including
its profile assignments. Registry history and reports remain available for audit;
removing a model disables its projected provider/model rows. Adapter code alone
does not authorize any provider requests.

The registered gateway now requests `commitment_extraction@v2`, which reuses the
unchanged `commitment_extraction@v1` output schema. The new prompt puts an ordered
evidence-first workflow before the existing extraction rules: empty optional
arrays, distinct operational observations, per-item citations and conservative
handling of header identities. Published v1 prompt/schema bytes are preserved.
The legacy explicit-OpenAI extraction path is unchanged. Existing deployments
must explicitly publish the new prompt before running registered evaluations;
missing prompt configuration is rejected before any provider call.

Extraction rubric `spec005-extraction-smoke.v3` accepts either `direct_request`
or `assigned_obligation` for the directly addressed lab-upload request, matching
the existing prompt and application policy. This is a single-case ambiguity;
bulk group-work classification, alert/state intent checks and all production
grounding validators retain their existing requirements. Captures now list the
accepted basis alternatives. Historical v1/v2 reports remain readable but cannot
be relabeled or reused as current qualification evidence.

Five synthetic rejected proposals are retained as offline rejection regressions.
Fixture tests cannot establish that the new prompt improves live model quality.
Later owner-provided r3/r4 runs supersede that historical comparison. In the latest
reviewed r4 receipt, Gemini and Claude each recorded 15/16 v2 drafting with every
safety case passing; two non-safety failures remain documented. OpenAI r3 evidence
remains historical. Grok is absent from the active catalog. All nine r4 assignments
remain at zero with shadow disabled. See the acceptance status for the precise
checkpoint and the fresh evidence required after the next catalog publication.

## Official API references

Recheck before registering or changing a live model:

- https://developers.openai.com/api/docs/guides/structured-outputs
- https://ai.google.dev/api/generate-content
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- https://docs.x.ai/developers/model-capabilities/text/structured-outputs

No provider SDK dependency is added. HTTP adapters are the sole new external
model-call boundary; the legacy adapter remains behind explicit `openai` mode.
