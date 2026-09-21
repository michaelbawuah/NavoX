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
