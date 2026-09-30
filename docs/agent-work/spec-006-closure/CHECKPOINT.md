# SPEC-006 offline acceptance window — checkpoint

## Exact state

- Workspace `/Users/mba_steins/NavoX`; branch `spec-006-news-intelligence`; HEAD
  `5573ba4` (published trending retrieval patch). Nothing from this window is
  committed, staged, pushed, merged or feature-enabled.
- Coordinator-verified gate on the frozen combined tree:
  `UV_CACHE_DIR=/tmp/navox-uv-cache bash scripts/check-api.sh` passed every gate,
  including patch whitespace; the worker's final run recorded **2074 tests** and
  three pre-existing warnings.
- Web checks on the tree: biome lint, `tsc --noEmit`, 197 tests in 27 files, and a
  production build all passed for the discovery-surface bundle.

## Accepted bundles

| Bundle | Code | Tests | Coordinator check |
| --- | --- | --- | --- |
| S006-SURF-1 | `navox/api/news_stories.py` related endpoint; web trending tab, feed helper, related-stories component | 10 API, 197 web | reproduced 10 + 197 locally |
| S006-EVAL-1 | `cluster_evaluation.py` provenance, new `extraction_evaluation.py`, fixture corpora | 47 focused | CLI measured/refusal/unmeasured logs, mypy clean |
| S006-ADV-1 | `evidence.py` membership fix, new `test_news_adversarial.py` | 13 new, 229 news | reproduced 13 locally; probe log shows the defect |
| S006-REFRESH-1 | `workflows/news.py` refresh wiring behind `news-conversation-refresh-v1` | 7 new, 125 workflow/activity | diff and red-first log reviewed |

The adversarial bundle fixed a real defect: `review_evidence` could bind an item from
a different story, producing `CORROBORATED` with `independent_supports=2`. Membership
and revision are now required at the adjudication write boundary. A read-time filter
in `evaluate_claim` was considered and rejected: membership can legitimately change
when a story is re-clustered while the recorded quote stays valid.

## Evidence and limits

- Worker logs: `docs/agent-work/spec-006-surfaces/`, `spec-006-evaluation/`,
  `spec-006-adversarial/`, `spec-006-refresh/` (all untracked working records).
- No worker wrote `WORKER-REPORT.md` for S006-SURF-1 and S006-EVAL-1; their evidence
  is the retained logs plus the coordinator's own reproduction of the focused tests.
- A genuine pre-patch Temporal replay test is not expressible with the offline
  harness; the refresh bundle asserts the old path by forcing `workflow.patched`.
- No hosted run covers this tree. `gh` is absent and this sandbox cannot resolve
  `github.com`.
- The sandbox keeps `.git` read-only and the approval reviewer is unavailable until
  4 October 2026, so publication must be performed by the owner.

## Remaining SPEC-006 work

Measurement and external evidence only: a corpus of article text with a recorded
rights basis (blocks every measured row), news-specific provider qualification and
canary evidence (needs live access and spend authorization), authorized X access,
and a human usability study. The acceptance record
`docs/evidence/spec-006/README.md` carries the full table.

## Resume action

Commit and push the working tree as one coherent commit on
`spec-006-news-intelligence`, then read the six hosted jobs on the new head. Do not
merge or mark SPEC-006 accepted from test success alone. Preserve SPEC-007 in
`/Users/mba_steins/NavoX-spec007` untouched.
