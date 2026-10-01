# SPEC-006 trending retrieval checkpoint

## Exact state

- Workspace `/Users/mba_steins/NavoX`; branch `spec-006-news-intelligence`.
- HEAD and draft PR #21 head: `5573ba4` (published on top of `9b36ee44`).
- Implemented: existing trending retrieval patch completed, preserving the initial
  three-file draft and adding ranking regressions in a fourth code/test file.
- Tested: final unchanged API gate passed, **2,024 tests**, three retained warnings;
  49 retrieval/ranking and 163 News tests passed. Source hashes are in WORKER-REPORT.md.
- Committed: yes, `5573ba4` with five files (four code/test plus the acceptance
  record). Pushed: yes, `origin/spec-006-news-intelligence` `9b36ee4..5573ba4`;
  the committed blobs re-hash to the gated `fce249be`, `e88de5bb`, `b3fb91af` and
  `7176dac3`. Merged: no; PR #21 stays a draft.
- Accepted: Astra local review of this bounded patch only. The published checkpoint
  and full SPEC-006 remain incomplete; no production activation is authorized.
- Hosted CI: run `36594520421` independently verified successful in all six required
  jobs on `9b36ee44`. It does not cover `5573ba4`. This sandbox cannot resolve
  `github.com` or `api.github.com` (`gh` is also not installed), so verification of
  the new head must be read from the PR page and recorded here.
- Preserved: original handoff and separate `/Users/mba_steins/NavoX-spec007` draft;
  all 601 tracked/untracked source-file hashes in its baseline manifest match.

## Contract and review

The ordering helper receives already-authorized items and preserves each input.
TRENDING uses observed activity inside the existing bounded recency candidate pool.
Explicit story/number scope and prior-answer precedence remain intact. Framing is
removed before the topic cap; topical words are retained. Other intents use the
unchanged lexical rule. This is bounded lexical behavior, not arbitrary semantic
understanding, calibrated importance, global popularity or X capability.

Astra reviewed the actual patch and evidence, including permission-before-query,
rights, suppression, freshness, deterministic ordering, caps and unrelated edits.
One correction cycle fixed the overly broad framing-word filter and the order of
framing removal versus topic truncation. Final code hashes matched the gate evidence;
only coordinator documentation changed afterward. Failed probes and the superseded
2,014-test pass remain in `validation/`; final gate is `validation/check-api-2.log`.
No full test rerun was duplicated by Astra. No local PostgreSQL or browser test is
claimed for this bundle. No thresholds, source policies or gates were weakened.

## Routing and ownership

Astra root session `01a0ee39-3884-77d1-99e4-b32a4c5a16cf` stayed on GPT-6 Astra.
Native worker `/root/spec006_trending`, session
`01a0ee3c-ea9d-78e0-81f8-d98ceaa7e01c`, used
`deepseek/deepseek-v4.1-flash`. Router usage metadata confirmed successful DeepSeek
provider requests. The child was marked done after returning its final evidence.
No provider setup smoke test, fallback model or additional writer was used.

## Remaining work and resume action

Read this checkpoint, current Git/PR head, and the reconciled
`docs/evidence/spec-006/README.md`; do not restart from the stale PR body.
Publication is done at `5573ba4`. The next acceptance step for this bundle is
reading the hosted jobs on that exact SHA and recording the result; the earlier
green run covers only `9b36ee44`. Do not merge or mark SPEC-006 accepted from test
success alone.

For the next implementation/acceptance phase, establish the independent clustering
and material-extraction evaluation corpus and its source/provenance contract before
calibration or semantic activation. Keep trusted evidence adjudication and
news-specific provider qualification as separate dependent gates. Then finish
refresh orchestration, related-story/wider research and calibrated ranking, plus
actual accessibility/usability and A–H demonstrations. A dedicated Trending web tab
is a possible UI extension, not evidence that the required chat flow is missing.
X access remains explicitly unavailable; do not fabricate an adapter qualification.

The current draft's implemented features include consumer summaries, timeline/source
comparison/change views, followed-story material updates, observed trending API and
explicit entity follows. Their presence does not establish independent quality
metrics, live-provider behavior or full end-to-end acceptance. Preserve SPEC-007.
