# Provider qualification and rollout

NavoX separates regression tests, model qualification, and deployment acceptance.
The gateway admits a request only when the exact model/task/prompt/schema binding
has qualifying evidence and its policy, sensitivity, health, budget, and traffic
settings permit the request.

## Inspect the deployment

From `services/api`, with the intended deployment environment loaded:

```bash
uv run --no-sync python -m navox.ai.readiness inventory
```

This command reads the registered bindings, evidence, traffic assignments, and
provider health. It does not publish a catalog, grant permissions, make provider
calls, or enable traffic. Workspace and user policy intersections are checked
separately during request admission.

Record the source commit, registry revision and digest, environment/policy
revision, and current immutable images before a rollout. Keep credentials and
personal data out of public reports.

## Qualify an exact binding

Use the existing evaluation CLI with synthetic material, a registered model,
an explicit profile, a cost ceiling, and a fresh output path. Inspect its options
before selecting a corpus or prompt version:

```bash
uv run --no-sync python -m navox.ai.manage evaluate --help
```

Retain the complete report, input corpus, actual responses, request identifiers,
usage, latency, definition digests, and review results. Recording an evaluation
does not promote serving traffic. Published prompt and schema versions are
immutable; changed definitions require their own qualification.

The serving floors require at least 10 samples, quality of at least 0.90,
reliability of at least 0.95, all designated safety checks passing, and evidence
no more than 30 days old. Qualification is specific to the task and profile:
reasoning evidence does not qualify drafting or speech. Preserve failed cases
and keep failed combinations disabled. The [gateway design](spec-005-gateway.md)
describes the policy intersections and fallback controls.

## Stage the rollout

An operator-approved rollout can progress through 0, 5, 25, 50, and 100 percent.
At each stage, observe actual request outcomes, quality, latency, cost, and
source isolation. Roll back to zero and verify that new requests cannot use the
withdrawn assignment before restoring the intended stage.

Changing a traffic percentage is a control operation; successful requests are
the evidence that the route serves. Fallback and shadow require their own
permission and compatible qualified bindings.

The retained [conversation rollout records](../evidence/operations/conversation)
describe the original scoped route and the October 4 personal-account expansion.
That expansion applies only to ordinary conversation: it does not grant speech,
connector, drafting, News, or external-action access. Deployment configuration
must preserve the reviewed task, context, sensitivity, token, and cost limits.

## Exercise the deployed boundary

Use a controlled workspace and synthetic content. Capture expected and observed
results for eligible and excluded accounts, history continuity, ambiguous input,
provider failures, and revoked permissions. Record task and trace identifiers so
the results can be checked against saved gateway attempts.

For action testing, inspect the exact recipient, subject, body, draft version,
and payload hash before obtaining approval. Confirm that an edit invalidates the
old approval. An executed action needs independent provider verification; an
uncertain send must not be repeated simply to obtain a passing test.

The request manifest for trace measurement contains the workspace and user IDs,
UTC start/end times, and the complete list of task IDs made during the test:

```bash
uv run --no-sync python -m navox.ai.readiness trace-coverage \
  --manifest /tmp/acceptance-requests.json
```

This reports coverage for that explicit denominator. Separately inspect
unauthorized actions, cross-workspace access, credential exposure, sensitivity
violations, changed approved payloads, and fabricated results after failure.
Trace presence alone does not measure those outcomes.

## Release evidence

Retain the applicable [local quality results](../development/api-quality.md),
all six required hosted checks for the final source revision, model reports,
reviewed policy changes, deployed observations, and rollback results. Historical
reports keep their original dates and scope; a new release records new evidence.
