# SPEC-006 trending retrieval continuation

Status: phases 1–2 finished; Astra local patch review accepted after one correction;
published as `5573ba4` on `spec-006-news-intelligence`, PR #21 still draft. No merge,
no hosted checkpoint completion on the new head and no SPEC-006 acceptance.
See CHECKPOINT.md for final evidence and the exact resume boundary.

## Baseline and ownership

- Branch `spec-006-news-intelligence`, HEAD/PR #21 head
  `9b36ee44a1b45837bd09b585c4d7afb2bd473c4b` (draft, unmerged).
- Hosted run `36594520421`: all six jobs succeeded on that published head.
  This does not cover the working-tree patch.
- Pre-existing edits: `services/api/navox/news/ranking.py`,
  `services/api/navox/news/retrieval.py`, `services/api/tests/test_news_retrieval.py`.
  Their complete initial bytes and patch are in
  `/tmp/navox-spec006-trending-20260929/baseline` and `baseline.patch`.
- Preserve the untracked handoff and all of `/Users/mba_steins/NavoX-spec007`.
- Astra owns this plan, CHECKPOINT.md and acceptance. One native
  `astra_flash_builder` owns the implementation, tests, debugging and its report.
- Static doctor and root session metadata confirm GPT-6 Astra and the pinned
  `deepseek/deepseek-v4.1-flash` / DeepSeek API role. Prior router usage events
  record successful requests on this route; no paid setup probe is authorized.

## Phase 1: finish the existing patch

The requested result is observed-activity ordering for TRENDING retrieval over
the existing bounded set of owned, currently permitted, fresh evidence. Generic
phrases such as "What's trending?" must not require the word "trending" in an
article. Meaningful topic words in a trend question must still constrain retrieval;
this does not authorize discarding the entire user's question.

Preserve explicit story and numbered-reference scope, non-TRENDING lexical
retrieval and follow-up behavior, freshness, rights and snippet permissions,
suppression, owner/pause checks, candidate/item limits and honest limited flags.
Rank by the existing observed TrendSignals contract, with deterministic story
and item ties. Do not describe activity as truth, importance, global popularity,
calibration, or X access. No networking or mutation belongs in selection.
The existing bounded recency candidate pool remains a stated coverage limit.

Allowed implementation files are the three dirty files plus
`services/api/tests/test_news_ranking.py` if needed. No schema, migrations,
provider routing, feature flags, web, SDK, dependency, CI or gate changes.
Record any material architectural or permission defect before expanding scope.

Required verification: regressions for generic/topic trends, genuine activity
ordering and deterministic ties, explicit story/number scope, non-trend behavior,
and denial/freshness/limits. Reuse existing tests where they exercise the contract.
Use actual Ruff formatting, run focused news tests, then run the unchanged
`bash scripts/check-api.sh` with the current lock on the final proposed tree.
Retain failed attempts and warnings. Do not read owner dotenv, contact live
providers/accounts or operate owner databases. Use isolated test facilities;
report environmental restrictions without weakening or bypassing them.

## Phase 2: reconcile acceptance evidence

After implementation and testing, produce a compact implementation/verification
matrix in WORKER-REPORT.md using actual code, recent commits, existing evidence
and the original specification. Original PDF:
`/Users/mba_steins/Downloads/SPEC-006_NavoX_Complete.pdf`, SHA-256
`f0c23c289d01440059785494249bd9d4113f2fed746ed69ab6d6813615b3e27c`.
Include newer followed updates, observed trend feed and entity follows; old PR
prose is stale. Separate code/fixture coverage from measured acceptance and
external dependencies. Identify the next dependency-ordered bundle; do not start
an unrelated feature under this brief. X access remains externally unavailable.

## Review and checkpoint

Astra reviews the actual final diff against HEAD and captured dirty baseline,
regressions and gate logs in one compliance/security pass. At most one ordinary
consolidated correction cycle. Report implemented, tested, committed, pushed,
merged and accepted separately. Local acceptance of this bounded patch never
means full SPEC-006 acceptance or a completed hosted checkpoint. Update the
acceptance record and CHECKPOINT.md with evidence and exact remaining work.
