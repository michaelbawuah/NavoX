# Engineering documentation

Start with the [project overview](../README.md) or
[local development guide](reference-guide.md).

## System design

| Topic | Guide |
| --- | --- |
| Sessions, planning, voice, and durable goals | [Assistant runtime](architecture/assistant-runtime.md) |
| Retrieval permissions, ranking, and source changes | [Connected retrieval](architecture/connected-retrieval.md) |
| Daily priorities and connector processing | [Operational intelligence](architecture/SPEC-002-navox-operational-intelligence.md) |
| Provider abstraction, qualification, and budgets | [AI gateway](architecture/spec-005-gateway.md) |
| Versioned approval and execution | [Action lifecycle](architecture/milestone-6-approval-execution.md) |
| News ingestion, evidence, and source rights | [News architecture](architecture/spec-006-news.md) |
| Recurring costs, renewals, and cancellations | [Subscriptions](architecture/spec-004-subscriptions.md) |
| Browser permissions and side-panel integration | [Chrome extension](architecture/milestone-8-chrome-extension.md) |

The [architecture decision records](adr) explain choices about PostgreSQL,
Temporal, storage, providers, Google events, action approval, and workspace
isolation. Detailed specifications remain in [architecture](architecture).

## Development and operations

- [Development checks](development/api-quality.md)
- [Deployment and rollback](../deploy/README.md)
- [Provider qualification and rollout](architecture/spec-005-live-acceptance.md)
- [Public news configuration](../config/news/README.md)
- [Evaluation fixtures](../evals/README.md)
- [Historical validation records](evidence/README.md)
- [Interface captures and walkthrough](media/README.md)

Historical validation records describe the source and configuration used on
their recorded dates. Current tests, configuration, and source code define the
behavior of a new checkout.
