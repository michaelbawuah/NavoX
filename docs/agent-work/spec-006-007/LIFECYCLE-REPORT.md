# Connected-knowledge lifecycle implementation

Root owns knowledge/jobs.py, activities.py, lifecycle.py, freshness.py,
workflows/knowledge.py, worker registration, api/knowledge_lifecycle.py, API main
registration, index freshness assignment, additive connector runtime/settings hooks
(activities/google Gmail/calendar) and test_knowledge_lifecycle.py.

Seven PDF workflows registered plus paginated reconciliation. Identifier-only jobs
check current owner/membership/pause/capability/canonical hash. Connection lock comes
before owner lock to match existing connector runtime. Accepted canonical revisions
project in the connector transaction; periodic reconciliation repairs missed work.
Stale events cannot overwrite a current revision. Cleanup still runs with feature off,
clears text/title/URL/chunks/vectors/graph, and permanent delete/disconnect tombstones
cannot be rebuilt even with force. Temporary permission loss clears derived data but
does not permanently prevent a later legitimately authorized current-source rebuild.
Orphan graph nodes after canonical cascade deletion are boundedly cleaned too.

Background embeddings are gated by both flags; one durable deterministic request ID
per source revision/chunk/artifact. Each paid execution still enforces gateway task
qualification, current authority, per-user quota, and .05 ceiling. No flags activated
and no paid work started. Namespace upgrades need explicit new embedding job IDs;
a previous attempted revision is not automatically repurchased after failure.

Explicit resource refresh POST resolves owned resource to current connector identity
and invokes existing managed sync path (source scope/configuration/cooldown/dispatch).
It accepts no URL/tool/authority fields and reports ERROR if dispatch stays pending.
Imports require a new snapshot. Existing source sync may include its preexisting
intelligence processing; this endpoint does not bypass those checks. GETs never queue.
Freshness policy is keyed by trusted read capability: messages15m, calendar5m,
assignments/announcements30m, courses6h, documents/files1h; unknown stays unspecified.
Fresh-until uses canonical fetched time, never reindex/projection time. Native News
and subscriptions keep their own policy. No automatic live refresh during Ask yet;
UI may explicitly request it through the source refresh control.

Local15 lifecycle+connector runtime tests pass; Temporal sandbox preparation covers
all8workflow classes. Focused Ruff+strictmypy pass. PostgreSQL combined lifecycle/
graph/semantic check in progress; full current-tree gate awaits Ask integration.
