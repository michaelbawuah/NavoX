# News validation records — September 2026

These files capture experiments and checks from the initial News implementation.
They retain the original successes, failures, source revisions, and observed
limitations. They are historical evidence, not a description of current serving
configuration.

| Record | What it measures |
| --- | --- |
| [Public feed](public-feed-20260929.json) | One isolated USGS Atom ingestion and readback |
| [Provider diagnostics](provider-diagnostic-20260929.json) | Initial structured extraction cases across model adapters |
| [OpenAI wire diagnostic](provider-diagnostic-openai-wire-20260929.json) | A subsequent adapter-level experiment |
| [Span diagnostic](provider-diagnostic-span-v2-20260929.json) | Updated source-span selection cases |
| [Relation diagnostic](provider-diagnostic-relations-20260929.json) | Candidate relation extraction |
| [Retrieval validation](retrieval-validation-20260929.json) | Bounded retrieval behavior |
| [UI validation](ui-validation-20260929.json) | Synthetic frontend checks |
| [Browser captures](browser-20260929/README.md) | Interactions, source links, and narrow-screen layouts |
| [Experiment budget](live-budget-20260929.json) | Recorded estimated costs and reserved ceilings |

The browser runs used authored/intercepted API responses. Provider diagnostics
used synthetic evaluation material. Neither establishes live-account retrieval,
human usability, or a model's eligibility for a different task binding.

Current implementation details are in the [News architecture](../../architecture/spec-006-news.md)
and [source configuration](../../../config/news/README.md). New provider runs
follow the [qualification procedure](../../architecture/spec-005-live-acceptance.md).
