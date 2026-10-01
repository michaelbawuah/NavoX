# High-assurance continuation: remaining publication races

Actual correction review found remaining material source/exclusion/privacy defects.
This authorizes a second focused correction under the skill's security exception;
not a full reimplementation or an acceptance. Fix these before intelligence work.

1. `search_knowledge` reloads `published_filters` but never applies it to connected
   `ordered` results. `publication_check` doesn't check exclusions either. Re-read
   current parent/type/source scope and apply ALL exclusions at publication, plus
   source hash/currentVIEW. Regression: insert RESOURCE or FOLDER exclusion after
   retrieval and before publication -> result and facts absent. Avoid copied prior
   parent/type being authority if source metadata moved without body hash change.
2. Native recheck keeps only keys; returned `native_resources` and `native.facts` are
   from the old first read. A changed subscription/news/commitment with same key leaks
   stale facts. Re-read via populate_existing as appropriate; compare full evidence
   signatures/version and selected facts, then drop changed old selections (or use
   exact rechecked values consistently). Test same-key native text/revision/rights
   change in a separate transaction between phases. Do not return old facts.
3. News citation anchor uses `view.sources[0]`, but sources are newest-first while
   story headline/description come from `story.anchor_item_id`. Select the actual
   anchor by ID so a later source doesn't get the original source's quote and URL.
   Multi-source regression with distinct headlines/timestamps/URLs. Current Search
   metadata may use display rights; future Ask must separately require SUMMARY via
   permitted_summary_item (already in intelligence contract). Don't claim summary
   permission is established by story_view.
4. Indexing reloads canonical but trusts supplied ConnectorConnection, never checks
   fresh owner.agent_paused, and `elif knowledge.deleted_at and not force` lets
   force=True resurrect a tombstone. Reload current connection identity/authority,
   check owner unpaused, and force must NEVER bypass retired projection. Test stale
   caller connection after revoked capability and force after tombstone. Existing
   source/owner/workspace mismatch checks still binding.
5. `_revision_current` returns True unconditionally for NO_CONTENT. Check source hash
   for every state; absent/current mismatch -> no detail. Use fresh index state at
   final publication. Never let a same-ID index rebuild supply different revision to
   cached chunks/facts. Native/read metadata caching must have equivalent fences.
6. Raw candidate `truncated` flag still comes from private query-matching rows before
   VIEW (test201+private matches), permitting title-word probing despite removing
   counts. Restrict text matching/candidate limits to currently eligible identities
   first (batch authority metadata; can_view remains final authority). Never use
   source content predicates/counts/truncation to report inaccessible matches. Keep
   bounded scan/coverage honest without a query-specific private existence oracle.
   Add regression comparing response coverage with/without private matching records.

Run targeted regressions and full required gates when final. Root is running separate
PG/schema/browser checks on the current frozen migrations/build, not editing source.
Report exactly what remains unimplemented. No new model calls or production changes.
