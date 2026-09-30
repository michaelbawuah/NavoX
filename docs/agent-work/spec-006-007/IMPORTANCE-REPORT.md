# Reviewed News importance

An internal trusted-review boundary now records all six SPEC-006 dimensions:
geographic scope, people affected, public safety, economic/policy/scientific
significance, source breadth, and duration. Each ordinal input cites an exact
current story-member span. Reviews bind story revision and current catalog policy,
expire within seven days, and contain locators rather than quoted text. No public
API or model can create a review. This implementation installs no reviews.

Top orders by reviewed significance only if every eligible candidate has a current
review. Otherwise the entire feed retains recency ordering and is labelled Latest.
Trending and explicit personal preferences retain their separate behavior. The UI
explains which ordering is in use. Weights are a versioned initial policy; independent
ranking evaluation remains required, and this is not an automatic importance model.

Nine importance + story API tests pass, including rights revocation, foreign scope,
source and story revision changes, expiry, complete review validation, ranking and
whole-feed recency fallback. Migration0032 adds one nullable JSON column; actual
PostgreSQL roundtrip and final combined gates remain pending.
