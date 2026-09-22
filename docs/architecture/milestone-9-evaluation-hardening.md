# Milestone 9 — Evaluation and Hardening

Milestone 9 closes the NavoX MVP roadmap by turning the product's safety,
reliability, intelligence, and release quality into explicit, repeatable gates.

## What this milestone proves

The release evaluation is offline by default and operates only on synthetic
fixtures. It evaluates the same deterministic policies used in production code:

- commitment precision and recall against labeled synthetic cases;
- strict structured-output acceptance;
- malicious-output and prompt-injection rejection;
- low-confidence false-positive suppression;
- proactive signal tier and signal-type policy;
- action risk / permission / approval policy;
- coverage evidence for the security threats named in SPEC-001;
- reliability invariants for plan bounds, approval TTL, idempotency, Temporal
  workflow references, and Gmail at-most-once execution semantics.

The benchmark runner is `python -m navox.evaluation`. Release thresholds live
in `evals/gates.json`; they are data, not hidden constants in CI.

## Provider comparison contract

`evals/providers/*.json` snapshots contain:

- provider and model identifiers;
- one output for every labeled commitment case;
- structured model output;
- measured or recorded latency;
- input/output token counts;
- estimated cost.

Every provider is scored against the same labels and confidence policy. The
committed `navox-reference / synthetic-reference-v1` file is intentionally
marked `synthetic_reference`. It validates the comparison machinery but makes
no claim about a live OpenAI, Gemini, Claude, or other external model.

A future live provider benchmark must preserve the same case IDs and snapshot
format and must not commit private prompts, user content, credentials, or
production data.

## Security control matrix

`evals/security/control_matrix.json` maps the SPEC-001 threat categories to
concrete repository evidence. Current controls cover:

1. prompt injection;
2. cross-tenant access;
3. approval payload tampering;
4. approval bypass and expiry;
5. tool escalation;
6. webhook replay / duplicate delivery;
7. duplicate consequential execution;
8. OAuth scope minimization;
9. malformed model output;
10. data-exfiltration instructions;
11. agent-loop bounds.

The normal API test job executes the referenced runtime tests. The evaluation
runner additionally fails if required evidence disappears from the repository.

## Runtime hardening

`RequestHardeningMiddleware` adds:

- a validated or server-generated `X-Request-ID`;
- `Server-Timing` for application processing latency;
- metadata-only JSON request logs;
- path-only logging (query strings are deliberately omitted);
- `Cache-Control: no-store` for API responses;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- `X-Frame-Options: DENY`.

CORS declares only the client headers NavoX uses and exposes only correlation
and timing headers. Production settings reject a non-HTTPS web origin. When
Google OAuth is configured in production, the redirect must use HTTPS and the
client secret and refresh-token encryption key must both be configured.

## CI release gate

The `Evaluation & hardening` job:

1. installs the locked API environment;
2. runs the full deterministic evaluator with `--fail-on-gate`;
3. uploads JSON and Markdown evaluation reports for 30 days;
4. becomes a dependency of the real Docker Compose integration job.

This means a regression in an evaluation threshold prevents the final
end-to-end integration gate from running.

## Metrics covered vs. deployment metrics

The repository can prove deterministic quality and policy behavior before a
deployment. Some SPEC-001 metrics require production telemetry and real user
feedback and therefore are not fabricated here:

- real user-rated briefing usefulness;
- real notification dismissal/fatigue rates;
- real approved-action success rates by provider;
- end-to-end production latency percentiles;
- infrastructure cost per active user;
- live-provider model quality and cost.

The report format is designed so those observations can be added later without
changing the benchmark contract.

## MVP hardening status

With Milestone 9, every roadmap stage from Foundation through Chrome Extension
has an automated quality boundary. A public launch still requires an explicit
deployment/security review, real production secret configuration, domain/TLS,
provider OAuth verification where applicable, monitoring/alerting, backup and
restore validation, and a user-selected license/public-repository decision.
