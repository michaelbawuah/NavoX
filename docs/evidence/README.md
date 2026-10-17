# Validation records

This directory retains dated provider experiments, ingestion checks, and browser
captures. Each record belongs to the source revision, fixtures, and environment
named in that record. A historical pass does not describe every later release.

| Records | Contents |
| --- | --- |
| [News](spec-006/README.md) | Provider diagnostics, source ingestion, retrieval checks, and frontend captures |
| [Connected retrieval](spec-007) | Embedding candidate reports, reservations, and browser interaction checks |
| [Conversation rollout](operations/conversation) | Reviewed route proposal and scoped/personal-account rollout boundaries |
| [News availability](operations/public-news-availability.md) | Account isolation and source-permission decision from October 2026 |

The provider reports preserve failed cases, reservations, and their original
evaluation dates. The [live experiment budget](spec-006/live-budget-20260929.json)
records estimated and conservatively reserved costs for those experiments.
These figures are not current provider pricing or a production billing total.

The repeatable regression gates live in [CI](../../.github/workflows/ci.yml).
Synthetic fixtures and reference results live in [evals](../../evals/README.md).
Use the [qualification procedure](../architecture/spec-005-live-acceptance.md)
for a new live model evaluation or rollout.
