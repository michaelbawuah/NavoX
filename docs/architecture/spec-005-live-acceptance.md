# SPEC-005 final acceptance procedure

This procedure distinguishes implementation regressions, public synthetic model
qualification and observed acceptance on the owner's deployment. None substitutes
for the others. PR #15 stays draft until the applicable acceptance records exist.
Grok is excluded from this deployment at the owner's request; its adapter remains
covered offline. No step below grants permission to send or promote traffic.

## 1. Review the final catalog and data boundary

Run `bash scripts/spec005-readiness.sh` from a clean, reviewed
`spec-005-ai-gateway` checkout with Docker/PostgreSQL running. It builds a one-off
API image, reads the live database and creates a dated Desktop ZIP. It does not
restart API/worker, publish configuration, call providers or read mailbox bodies.
The revision/digest check binds the reviewed r4 checkpoint. A different deployment
state stops preparation; inspect its inventory rather than bypassing the guard.

The archive contains current catalog/qualification inventory and two **unpublished
alternatives** for r5, both preserving historical prompts and schemas, exact model
IDs, prices and Grok's removal:

- `public-only`: appends the four operational v2 contracts and compatible profile
  assignments. Existing model sensitivity ceilings are preserved.
- `personal-proposal`: the same changes plus `PERSONAL` in the three existing
  models' sensitivity ceilings. This is a proposal, not consent or a policy grant.

Choose the data boundary before paying for qualification. Real saved-task and
email contexts have a PERSONAL floor. A PUBLIC-only catalog can qualify synthetic
cases but cannot serve those real features. If PERSONAL processing is approved,
review which providers may receive it and the corresponding operator/workspace/
user policy ceilings. No SENSITIVE or RESTRICTED expansion is proposed. Persisted
source classifications can require stricter permission and always win.

Select and review **one** catalog, then publish it through the existing CLI using
the expected current revision. Do not publish both alternatives. Adding contracts
or changing model ceilings advances the revision and invalidates prior serving
qualification, while preserving all historical evaluation rows. Finish catalog
choices before the complete evaluation run to avoid unnecessary repetition.

## 2. Run full public synthetic qualification

Use the existing `navox.ai.manage evaluate --live` CLI with an explicit model,
profile, budget and fresh output path. Keep source commit, catalog JSON/digest,
UTC run ID and SHA-256 hashes with every report. Policy must permit PUBLIC for the
selected provider. Review current model availability and prices before paid runs.
The evaluator uses registered cost ceilings; it does not reconcile actual billing.

| Corpus | Profile | Prompt / schema | Cases per model |
| --- | --- | --- | ---: |
| extraction | EXTRACTION_HIGH_ACCURACY | commitment_extraction@v2 / @v1 | 33 |
| communication | ASSISTANT_INTERACTIVE | communication_draft@v2 / @v1 | 16 |
| planning | PLANNING_HIGH | planning@v2 / @v2 | 12 |
| meeting_preparation | REASONING_STANDARD | meeting_preparation@v2 / @v2 | 12 |
| assistant | ASSISTANT_INTERACTIVE | assistant@v2 / @v2 | 12 |
| ranking | REASONING_STANDARD | ranking@v2 / @v2 | 12 |

Example for a registered model, replacing `PROVIDER:MODEL` and the output path:

```sh
uv run --no-sync python -m navox.ai.manage evaluate --live \
  --corpus assistant --profile ASSISTANT_INTERACTIVE --model PROVIDER:MODEL \
  --max-cost 0.25 --minimum-start-interval-seconds 6 \
  --output /tmp/NEW-RUN-assistant.json
```

Communication additionally requires `--draft-prompt-version v2`. Inspect all 16
actual candidate drafts and complete the bound human review. Historical r4
Gemini/Claude 15/16 passes and r3 OpenAI 16/16 remain historical; they are not
fresh r5 evidence. The four diagnostic fixes are not a full-suite qualification.

Operational reports capture exact fact selections/tool proposals and score them
against an independent fixed corpus. `record-evaluation` recomputes those scores;
partial, edited-score or offline reports cannot qualify. Inspect failed cases;
do not replace candidates with manually authored passing answers. Any designated
safety failure disqualifies that task, even with acceptable aggregate quality.

Record full bound reports only after inspection. Recording never promotes traffic.
The existing serving floors remain >=0.90 quality, >=0.95 reliability, >=10 samples,
all safety cases passing and evidence no more than 30 days old. Keep failed
model/task combinations disabled. Each task needs a qualified serving model;
provider-switch/fallback acceptance needs two compatible qualified models.

Do not record a failed experiment in a shared profile without recognizing its
fail-closed effect: it zeros that model's entire profile assignment. Qualifying
assistant reasoning does not qualify drafting, or vice versa. Extraction FAST
requires its own profile-bound full run if that profile will be enabled.

## 3. Observe acceptance scenarios A–I

Use an explicitly approved test workspace and accounts. Record source commit,
catalog revision, policy revisions, selected model, request/task/trace IDs,
expected outcome, observed outcome and relevant IDs/hashes for every scenario.
Keep private message bodies out of ordinary telemetry and public PRs.

| Scenario | Required observation |
| --- | --- |
| A | Same complete public corpus through all three in-scope real adapters; common output contract, failures retained. |
| B | An eligible interactive request and background extraction, with their actual profile/model traces. |
| C | Preferred provider unavailable; an eligible second provider returns the same task contract within the original scope/budget. Use a scoped test harness, not invalid production credentials. |
| D | SENSITIVE input excluded from a provider lacking that exact grant; zero request to that provider. |
| E | Generate, edit, inspect final sender/recipient/subject/body/version, explicitly approve one test send, execute through Temporal, independently verify sent content. |
| F | An edit invalidates the prior version/hash approval. The old action cannot send; reapproval binds the new exact content. |
| G | Source routing/recipient/tool injection cannot expand context, policy or actions. |
| H | All providers fail; no fabricated answer, draft or action is produced. |
| I | Two assistant turns use different eligible providers under the same NavoX session ID with ordered turns and unchanged ownership/selected saved state. |

The new operational API is read-only, extractive assistance over explicitly
selected current saved state. Planning proposes existing preparation tools; it
does not execute them. It is not a free-form research assistant or the future
SPEC-008 conversation interface. Session artifact IDs are opaque; they do not
provide a general-purpose transcript retrieval API.

For E/F, prepare the exact final test message and obtain approval for that content
and destination. A qualification or general SPEC continuation instruction is not
approval to send. Preserve the approval payload hash, draft version, action ID,
Temporal workflow outcome, provider message ID and independent verification.

## 4. Observe canary and rollback

After explicit rollout approval, use the existing staged CLI: 0 -> 5 -> 25 -> 50
-> 100, observing results before each increase. A control change alone is not an
observed canary. Record actual request outcomes and quality/latency/cost behavior,
then rollback to zero and verify new requests cannot use the removed assignment.
No weakening of sensitivity or evidence gates is permitted to make a demo run.
Shadow is separately authorized, costs tokens and cannot produce user actions.

## 5. Measure and sign off

Record an explicit denominator of gateway requests made during acceptance,
including failures. Do not infer trace coverage from the trace table alone. The
read-only command measures an independently collected request manifest:

```json
{
  "workspace_id": "WORKSPACE_UUID",
  "user_id": "USER_UUID",
  "started_at": "UTC_START",
  "ended_at": "UTC_END",
  "task_ids": ["TASK_UUID"]
}
```

```sh
uv run --no-sync python -m navox.ai.readiness trace-coverage \
  --manifest /tmp/acceptance-requests.json
```

Require >=99% trace coverage, inspect missing/nonterminal attempts, and separately
review the zero-failure security targets: unauthorized actions, cross-workspace
leaks, credential leaks, sensitivity violations, unsafe fallback, changed approved
sends and fabricated results after total failure. This trace command does not
measure those targets or general production accuracy. Preauthorization rejections
that never create a gateway task belong in their own denied-request record.

Attach full local gates and all six green CI jobs for the exact source head,
measured live reports, bound human reviews, A–I receipts, observed canary/rollback
and the owner's acceptance to PR #15. Document any agreed exclusions. Only then
mark SPEC-005 complete and consider merge/SPEC-006 integration.
