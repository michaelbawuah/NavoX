# Remaining News intelligence: design decisions (not implemented/accepted)

The original SPEC-006 remains authoritative. This file is a next-phase contract,
not a renamed completion gate. Current root/live evidence is in README and ledger.
No approved News source or embedding model currently exists in this runtime config.

## Semantic clustering

Reuse gateway EMBEDDING and its strict vector namespace/permissions from SPEC-007,
but News owns its rights and source identity. A News vector needs current+original
SUMMARY/processing permission, item revision and catalog fingerprint at prepare,
provider call and publish. Never send restricted full text because an embedding API
is available. Metadata-only feeds can use only their allowed metadata.

A deployable policy binds the exact signal extractor version, embedding namespace,
source/language scope, corpus manifest digest and independently labelled holdout
report to reviewed thresholds; authored fixtures cannot qualify it. Existing
cluster_evaluation selection remains an evaluation recommendation until reviewed
operator config supplies the matching artifact. Never infer deployment approval
from evaluation_reference text or a numeric threshold. Source/model/parser change
invalidates calibration. Missing/incomplete/tied candidates => UNCERTAIN, not merge.
Separate event times and shared entity identities, bounded permitted candidate set,
weighted score exactly perPDF. Explicitly incompatible events never join. Content
copies/syndication remain one independence component after semantic joining.

## Automatic evidence interpretation

Keep EvidenceReview a trusted application input, absent from public API/model schema.
A new versioned provider task may PROPOSE exact same-story source spans and
SUPPORTS/CONTRADICTS/ATTRIBUTES relationships only. It cannot select strength,
independence groups, primary-record authority, retraction authority or verification
state. Source identity/origin metadata comes only from operator-reviewed registry.

Automatic publication of relation proposals requires separate task-specific measured
qualification and current rights/revision/story membership. Model confidence is never
verification. Primary-record authority requires an explicitly reviewed structured
source policy/record type, not government/company domain alone. Company/interested
party statements default ATTRIBUTED; volume cannot establish truth. Retraction is
bound to original source/claim plus exact source update; sources cannot retract each
other. Unknown relation or incomplete coverage remains uncertainty. Keep an audit
of policy/artifact/model/revisions; correction invalidates selections downstream.
This closes no precision gate until an independent lawful relation corpus exists.

## Ranking

Trend, importance and relevance are distinct application-owned dimensions with a
versioned policy and per-input missingness. Existing observed publication/source
activity can measure trend; it cannot stand in for public importance. Importance
inputs (affected scope/count, safety, policy/economic/scientific significance and
duration) require source-cited structured facts or explicit trusted review. Never
infer politics or importance from outrage/clicks/model certainty. Missing importance
inputs keep a clearly-labelled recency fallback, not a fabricated importance value.
For You uses explicit user follows/preferences, within workspace/user scope. Any
learned relevance must be bounded, opt-in history and never sensitive-identity inference.

## Broader research

Read-only research can refresh approved feed/source identifiers through existing
bounded workflows, retrieve across permitted current stories and time windows,
return timeline/background/source comparisons/material changes with exact stored
citations. It cannot fetch arbitrary user/model URLs or bypass rights. Report the
connected-source scope and missing/stale sources; no claim of exhaustive web research.
Default-off Deep Research requires durable request identity, bounded sources/depth,
provider ceiling and quotas; one interrupted provider attempt cannot silently retry.

## External acceptance dependencies (currently unmet)

Independent rights-cleared labelled news corpus for clustering/extraction/relations/
ranking; approved live source configuration and X access; task qualification/canary;
human first-use News task study and screen-reader flows. These are distinct from
remaining code, browser screenshots and synthetic tests. Final A-H demonstrations
must state actual path and evidence kind. Source/API rights must be verified from
current official terms before adding any source integration.

## Automatic relation phase contract (root implementation)

Use an additive news_evidence_relations@v1 task selecting current claim IDs, exact
source spans and SUPPORTS/CONTRADICTS/ATTRIBUTES only. The existing gateway must
qualify that exact artifact before any call. Optional operator-owned source policy
specifies reviewed evidence role, explicit strong-evidence permission, origin groups
and expiry. Missing policy means no additional request and no automatic adjudication.
Original-report roles are restricted to publisher/wire sources; company/social cannot
be promoted. Primary-record, correction and retraction remain outside automatic
relation selection until a structured authority adapter exists. Application logic
constructs EvidenceReview and owns verification. No model receives or selects review
strength, role, source independence or truth. Policy changes bind into source catalog
fingerprint; default absent policy preserves existing fingerprint bytes. Three-phase
budget totals at most $0.05, durable run reservation remains existing one-attempt
boundary. This implementation will not create policy, qualification or source approvals.
