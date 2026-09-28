# ADR-004 AI Provider Abstraction

## Status

Accepted

## Context

NavoX needs structured extraction and bounded planning without locking product logic to one AI provider or allowing models to bypass deterministic controls.

## Decision

Route all model use through an AI Gateway. Begin with one provider behind provider-neutral interfaces and an evaluation capability. The gateway is responsible for minimization/redaction, timeouts, telemetry, provider handling, structured-output validation, and errors. Models propose; deterministic code controls authority and execution.

## Consequences

- Providers can be compared using evaluation data rather than demos.
- Direct provider calls are prohibited outside the gateway.
- Initial implementation stays narrow rather than deeply integrating several providers.

## SPEC-005 implementation update

The draft registered runtime implements four structured-text HTTP adapters,
catalog persistence, explicit policy intersection, measured-evidence eligibility,
bounded fallback, durable health, validation and content-free traces. Features
request logical profiles, not external model IDs. Automatic mode is opt-in; the
existing OpenAI path remains for controlled migration.

Communication drafts bind final versions and exact payloads to existing approval
and execution. Sessions store NavoX-owned references. Model outputs, session
references and tool proposals grant no authority. Unimplemented adapter
capabilities are refused. No live model, price, quality score or grant is seeded.
Passing synthetic transport tests is not a production-model quality measurement.
See [acceptance status](../architecture/spec-005-foundation.md) and the
[operator guide](../architecture/spec-005-gateway.md) before rollout.
