# SPEC-005: gateway foundation — draft implementation slice

## Baseline and scope

Source of truth: `SPEC-005_NavoX_Multi-Model_AI_Gateway(1).pdf`, supplied by the
owner on September 25, 2026. This starts M1; it is not a SPEC-005 completion claim.
Reviewed repository baseline: `ecdde5afde39ab352978f1e0fd9bf75dbe4d6c08`.
SPEC-004 was merged in PR #14; CI run 36194966291 reports success on that baseline.
The existing `navox.ai.gateway`, factory, OpenAI adapter, operational extraction,
Gmail flows, subscription workflows, approvals, database, and UI are unchanged.

## Included

- `navox.ai.foundation.contracts`: versioned task/result envelopes, stable logical
  profile names, capabilities, sensitivity classes, scoped context references,
  explicit provider grants, bounded fallback policy, usage, decimal cost estimates,
  and task/result attribution checks.
- `navox.ai.foundation.registry`: immutable in-memory model/profile/prompt/schema
  snapshots, referential checks, capability-compatible assignments, exact-version
  binding, and update checks that preserve prior prompt/schema versions.
- `navox.ai.foundation.adapter`: a provider-independent protocol plus bounded
  request/response, health, and error vocabularies. It contains no HTTP client,
  provider credential, tool executor, or concrete production adapter.
- Offline tests using synthetic configuration and one synthetic adapter. No live
  model names, assumed current capabilities, invented prices, or quality scores
  are seeded. Models are disabled and sensitivity access is empty by default.

## Boundaries that must survive integration

The task and provider-policy types are internal, server-created values. A matching
workspace/user UUID is a consistency check, not proof of ownership or consent.
The context builder must fetch authoritative ownership, authorization, sensitivity,
and source provenance before invoking any provider. Source documents and model
outputs must never be deserialized into routing or authorization policy.

`validate_result_binding` checks attribution, provider permission at the declared
sensitivity, and the fallback count. It does not select models, enforce a total
retry/spending budget, validate the result against its schema, inspect grounding,
check live permissions, or authorize execution. Validation flags are descriptive
assertions to be set by the future gateway, not authority supplied by providers.

JSON is stored as immutable, normalized text instead of a mutable dictionary.
Transport rejects duplicate keys, non-finite numbers, documents over 262,144
characters, and nesting above 64 levels. This is transport validation, not JSON
Schema compilation, semantic validation, or an exact-email approval hash format.
Do not reuse this serialization as a communication approval contract.

Unknown token usage and prices stay `None`; they are not silently recorded as zero.
Price fields are USD estimates. Region/currency policy, billing reconciliation,
and whole-task budget enforcement belong to later gateway work.

Prompts, context, and response text are excluded from object representations.
Pydantic's `hide_input_in_errors` hides inputs in stringified validation errors;
it does not make arbitrary `model_dump`, `.errors()`, or exception tracebacks safe
for telemetry. Do not log those payloads. Telemetry is not implemented here.

Registry snapshots are in memory only. A future persistence boundary must enforce
atomic revisions and call the update validator under appropriate concurrency
control. The current value classes are not a database uniqueness constraint or a
security sandbox. Pydantic construction/copy shortcuts must not be used to bypass
validation at trust boundaries.

## Remaining M1 and later work

Finish registry persistence and reviewed production prompt/schema registrations;
then build authorized context minimization and the first end-to-end provider.
Implement the remaining adapters, policy/evaluation-based routing, bounded retries,
circuit breakers, schema/domain validation, canonical action proposals, telemetry,
shadow/canary controls, cross-spec migration, exact-approved communication drafts,
and provider-independent assistant sessions according to the original spec.

Before implementing external provider APIs, recheck all relevant official provider
documentation. This batch intentionally does not pin external models or add SDKs.
The legacy extractor remains active until a separately tested migration is ready.

## Verification and release status

124 focused tests passed in an isolated overlay using Python 3.13.5, Pydantic
2.13.4, and pytest 9.0.2. The new modules also passed bytecode compilation.
These tests did not use the full repository, its parent-package imports, its
`uv.lock`, or its PostgreSQL/Temporal services. They are not the full API suite,
production-provider contract acceptance, or an end-to-end application check.

Cloning the private repository was unavailable in the working container. Ruff,
mypy, and Temporal were unavailable there; offline installation of Ruff/mypy found
no cached distributions. Formatting, lint, strict types, migrations, full API tests,
release evaluation, and hosted CI for this new code remain unverified.

Do not push this draft before running the repository preflight on its complete
working tree. The accompanying local application script runs the lockfile-backed
formatter/import tools on only the new files, then `bash scripts/check-api.sh`.
Any failure stops that script. It never commits, pushes, restarts the application,
or changes provider settings. Hosted checks on the exact eventual commit are still
required before a milestone can be declared complete.

## References checked

- The owner-supplied SPEC-005, especially Core Contracts, Registry, Implementation,
  Milestones, Security Invariants, and Completion Gate.
- Repository `AGENTS.md` and `services/api/navox/ai/gateway.py` at the baseline above.
- Pydantic documentation: models, configuration, and fields, checked September 25,
  2026. In particular, frozen models alone do not make nested dictionaries immutable.
  https://docs.pydantic.dev/latest/concepts/models/
  https://docs.pydantic.dev/latest/api/config/
  https://docs.pydantic.dev/latest/concepts/fields/
