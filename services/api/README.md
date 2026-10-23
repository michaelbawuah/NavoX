# NavoX API

The FastAPI service owns the API gateway, policy-enforced application logic, and PostgreSQL operational state. It now includes Milestone 1 identity, Milestone 2's authenticated content-minimized provider event pipeline, and Milestone 3's commitment engine. The commitment engine accepts only schema-validated structured output internally, then applies deterministic confidence policy, workspace-scoped deduplication, source provenance, and relation persistence. It cannot send, edit, or otherwise execute anything in an external system.

See the repository [README](../../README.md) for local setup and event-delivery constraints.

OpenAI model definitions may explicitly set `openai_service_tier` to `"default"`
for Responses requests. The [official API reference](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses/methods/create)
defines this as Standard pricing and performance; leaving the field unset retains
the existing request and the provider project's tier behavior. The optional field
is omitted from serialized model metadata when unset, preserving historical
registry and model digests. It is rejected for other providers. Setting it changes
the model digest and requires fresh qualification; it does not register a model,
grant a task, change traffic, or establish invoice cost. Billing reservations must
still account separately for caching and any applicable regional pricing.
