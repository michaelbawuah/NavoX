# Exact-source structural graph — local implementation

Root owns knowledge/graph.py, api/knowledge_graph.py, API main registration and
test_knowledge_graph.py. No schema changes. Internal sync_resource_graph is
transaction-owned and serializes writes on the owner. Canonical external parent
ID + same exact connection determine PART_OF edges; title/name never matches.
Nodes keep no display text, read current authorized title/URL only after metadata
permission/exclusion/hash checks. Source fingerprint includes parent scope to
invalidate moves with unchanged content hash. Reads recheck both endpoints before
publication; one-hop traversal scans at most100edges and publishes at most20.
Coverage always says bounded structural subset, discloses no hidden counts.

13 SQLite +13 isolated PostgreSQL tests passed at22:48UTC: actual API routes,
feature/auth guards, replay/rebuild, no name merge, parent deletion/revision/view
removal/exclusion, connection capability revocation, child revision/move, expiry,
foreignworkspace, preauthorization SQL privacy and mid-read revision change.
Ruff+strict mypy passed; latest metadata-only entity projection and optional title
are included in the forthcoming combined gate. These tests do not establish
broad semantic entity resolution or the PDF quality thresholds.
