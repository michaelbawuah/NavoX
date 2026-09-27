# ADR-008: xAI/Grok deferred

Status: Accepted by Michael, 2026-09-27.

The initial NavoX Multi-Model Gateway production provider set is OpenAI, Google
Gemini, and Anthropic Claude. The gateway remains provider-extensible so xAI/Grok
or other providers can be introduced later without modifying feature-level NavoX
architecture.

xAI/Grok is intentionally outside current SPEC-005 deployment qualification. Its
absence is not a missing requirement or a blocker. Existing adapter tests and
historical evaluation/health records may remain. Reintroduction requires separate
catalog, policy and qualification review; it cannot enable itself by discovery.

This decision grants no runtime traffic, shadow traffic, personal-data processing
request or email execution. Those controls remain separate from model metadata.
