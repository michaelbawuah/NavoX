# NavoX API

The FastAPI service owns the API gateway, policy-enforced application logic, and PostgreSQL operational state. It now includes Milestone 1 identity, Milestone 2's authenticated content-minimized provider event pipeline, and Milestone 3's commitment engine. The commitment engine accepts only schema-validated structured output internally, then applies deterministic confidence policy, workspace-scoped deduplication, source provenance, and relation persistence. It cannot send, edit, or otherwise execute anything in an external system.

See the repository [README](../../README.md) for local setup and event-delivery constraints.
