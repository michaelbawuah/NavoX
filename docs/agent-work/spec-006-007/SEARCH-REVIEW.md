# Astra review: S007-SEARCH correction bundle

Status: changes requested; do not start intelligence phase yet. Full gate passed but
actual patch has material permission/privacy defects. Same worker owns corrections.
Preserve the other News/AI work. Root source frozen. Fix below in one coherent pass,
add behavioral regressions, repeat required API/web gates on final bytes.

1. `retrieval._candidate_rows` selects whole KnowledgeResource ORM rows, including
   normalized_text before VIEW. `permissions.can_view_resource` and
   `_canonical_source_is_live` also select full content-bearing rows. Select only
   identity/authority columns (or explicitly defer/raiseload content) until current
   authority passes. Do not let unrelated inaccessible matching content enter the
   session identity map. Test SQL-selected columns / denied content loads meaningfully.
2. `service._no_content_matches` deliberately deletes filters, ignores user and VIEW,
   and counts same-workspace private-title matches. This leaks existence through
   query probes. Authorize before title matching, honor ALL exclusions and source/type/
   date filters, or omit counts entirely in favor of an honest non-sensitive generic
   coverage notice. Candidate/examined/stale/truncated counts must not expose private
   matches either. Test another workspace member's NO_CONTENT secret-title record.
3. `load_resource_detail` ignores exclusions and index/source hash and lacks final
   recheck. Reuse permission/exclusion/revision boundaries before text and publication,
   including NO_CONTENT, rights/retention loss, expired/changed source. No cached old
   title/URL/chunks on a denied detail. Test every exclusion kind and revision drift.
4. `adapters._news_evidence` uses `database.scalars(select(NewsStory, NewsItem,
   NewsSource))` then unpacks triples: fails for any nonempty News data. It also bypasses
   trusted catalog fingerprint, disabled source, original rights intersection and
   original-source URL validation. Call current News owned_story/story_view and
   permitted_summary_item with catalog(settings), never duplicate a weaker policy.
   lifecycle_status is NOT verification_status. Recheck native News at publication;
   no suppressed, expired, corrected/stale or revoked evidence or policy-derived text.
   Add seeded nonempty API test, disable/config change/original restrictive rights cases.
5. Native adapters ignore plan.types, plan.source_ids and per-resource exclusions;
   only type exclusions apply. A Calendar-only query must not return subscriptions.
   Implement native query eligibility before reading content; connected-source filter
   excludes native domains unless explicitly represented in contract. RESOURCE exclusion
   currently FK only supports KnowledgeResource: either add explicit native keyed scope
   or return clear 422 unsupported; never 500 or silently ignore. Revalidate all native
   evidence (and current account/exclusions for all results) before publication.
6. `structured_retriever` doesn't produce connected StructuredFacts and returns unrelated
   typed-date rows despite topic terms. Emit current typed Calendar/Canvas facts with
   authoritative labels from trusted adapter identity; apply topical constraints unless
   planner intentionally consumes date/domain cue words. Ordinary keyword queries must
   be able to find native domain text too; currently native only when STRUCTURED selected.
   Tests need all three native domains and connected Calendar/Canvas, not just commitments.
7. Indexing lowers sensitivity from canonical['sensitivity'] including PUBLIC and can
   retain old title/URL with `... or knowledge.title` when content/URL removed. Source
   content cannot reduce sensitivity; default at least PERSONAL unless trusted operator
   config grants otherwise. Clear removed fields and NO_CONTENT bodies, never recreate
   deleted/revoked projections. Index a freshly reloaded canonical row, active owner/
   membership/legacy authority; reject stale supplied row/deleted source. Boundary tests.
8. ExclusionCreate permits irrelevant mixed fields and create_exclusion flush then DB
   CHECK failure ->500; unknown/wrong workspace IDs likewise IntegrityError. Validate
   exact shape in Pydantic and supported owned/visible scope before write, no ID existence
   oracle. API regression returns 422/404 rather than500, transaction remains usable.
9. UI `submit` lacks active-controller identity guards; old abort finally clears new busy
   and late old response can replace newer query. Old results remain after failed new
   search and after hide if refresh fails, disclosing excluded results. Clear invalidated
   response synchronously; only active request may publish state/finally, sidebar too.
   Date To uses 23:59:59 instead of next-midnight exclusive, losing final second.
   Suggested instruction text isn't a query: render plain guidance or real query actions.
   Remove raw intent/"Typed details" implementation wording from consumer UI. Add race
   and exclusion-refresh-failure tests at the appropriate existing client harness.
10. EvidenceBundle permission_snapshot_id truncates workspace:user:trace at64, removing
    trace entirely and part of user. Use a unique collision-resistant opaque snapshot
    identifier tied to retrieval_trace_id; it still grants nothing. Add separation case.

Accepted scope limits for this phase: bounded ILIKE lexical search (do not call it
measured semantic/full-text quality), default-off, no Ask/graph yet, no runtime indexing
hook until next phase. Do NOT edit 0029. Browser port failure can be handled by root
with authorized escalation later; record no visual evidence for now.
