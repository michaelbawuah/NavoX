# SPEC-008 M6 — read-only News answers, no X

After M5's local gate, add `news.read` to a new versioned SPEC-005 planner
artifact and to the TypeScript capability registry. Preserve v1 and v2. The
planner only identifies a read-only news intent and an optional entity copied
from the user. Do not restore any X route, account connection or API dependency.

Delegate through authenticated SPEC-006 `/news/trending` for a generic
trending question or a named trending entity. Bound and validate the feed and
source timestamps. For a named entity, use only the exact text in the user's
utterance as a local filter; zero or multiple current stories clarify rather
than selecting one by guess. Recheck scope and fetch a unique story's current
detail plus `/news/stories/{id}/summary`; require matching ID/version and a
READY source-backed summary before explaining what happened. Preserve every
claim's verification label and publisher attribution; an attributed headline
or trending rank is not verified truth. PENDING, unavailable, changed or
incomplete source data gets a qualified response, never an invented summary.
Use canonical in-app story links or validated publisher source URLs only.

Use TypeScript gateway/runtime/contracts and existing UI blocks. Add tests
for generic trends, unique/ambiguous/missing entity, stale story, source
outage, verification/attribution distinction, account change, no X path and
zero action calls. Run root JS gates and Web build. Because the SPEC-005 plan
schema changes, rerun the full API gate with disposable PostgreSQL/Temporal.
Do not publish the catalog or run a paid provider call for this slice.
