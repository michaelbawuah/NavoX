# SPEC-006 closure window (29 September 2026, three hours)

Historical plan: its X integration item was removed from scope by the owner on
2026-09-30. Current acceptance is tracked in `../spec-006-007/ACCEPTANCE-MATRIX.md`.

Goal: close every SPEC-006 acceptance item that can be closed with offline code and
evidence, and record the ones that cannot with their exact external dependency.

Baseline: branch `spec-006-news-intelligence`, HEAD `5573ba4`, working tree clean
apart from untracked `CODEX-HANDOFF-20260929-1304.md` and `docs/agent-work/`.
PR #21 stays draft. Publication of anything produced here needs a fresh user-run
commit and push, because this sandbox keeps `.git` read-only and the approval
reviewer is unavailable.

## What cannot be closed in this window

- Item 3, X integration: the owner confirmed there is no authorized X API access.
- Item 2's live half, news-specific provider qualification and canary evidence:
  needs a real provider request, spend authorization and network access. This
  sandbox cannot resolve DNS.
- Item 1's measurement half, real clustering and extraction quality: needs article
  text with a recorded rights basis, which is the owner's decision.
- Item 6's measured usability: needs human participants. Automated keyboard and
  accessibility checks can be added, an unassisted usability study cannot.

## Bundle S006-SURF-1: news discovery surfaces

Closes the two listed gaps in items 4 and 5 that need no external input.

- Trending feed in the web News workspace, wired to the existing
  `GET /api/v1/news/stories/trending`, with honest observed-activity labeling.
- Related stories: read-only `GET /api/v1/news/stories/{story_id}/related`, bounded
  to five, defined only by shared claim text digests in the same owner scope, with
  suppressed and no-longer-permitted stories excluded, plus the story-screen section.

## Bundle S006-EVAL-1: measurement machinery for item 1

- Material-extraction measurement module beside `cluster_evaluation.py`, with
  precision and recall over source-backed spans and an explicit unmeasured state.
- Corpus provenance fields (source identity, capture timestamp, rights basis, text
  digest, label provenance) extended into the existing corpus contract.
- Red probes for leakage, missing candidates, wrong-target joins and abstentions.

## Astra close-out

Reconcile `docs/evidence/spec-006/README.md` against the accepted code, record what
remains open with its external dependency, run the unchanged gate on the final tree
and hand the owner one coherent commit command.
