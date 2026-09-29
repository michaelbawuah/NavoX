# SPEC-006 — explicit updates for followed stories

Status: local implementation, not release acceptance or full M8 completion.
Based on the published UI checkpoint 99a8b14. The specification's page 5
signature features include following a StoryCluster and evaluating material updates.
This batch supplies an on-demand, in-app evidence-change inbox, not email/push alerts.

## Behavior

- Following a story explicitly establishes the current version as the starting point.
  Repeating Follow does not reset that marker; Unfollow clears it.
- GET `/api/v1/news/following/updates` checks current owner, source rights, retention,
  suppression and dismissal. Merely opening a feed, story or inbox never marks it read.
- Changes count only when a recorded claim assessment moves into/out of verified,
  corroborated, disputed, contradicted or retracted states. Copies, source volume,
  new unconfirmed statements and title-only changes do not independently qualify.
- The alert reports that evidence changed; it does not promote a current claim.
  The card's current verification label still comes from the existing evidence view.
- Missing history is explicit. Legacy follows do not acquire an invented last visit.
- POST `/api/v1/news/following/{story_id}/seen` takes an exact observed version.
  It requires the existing authenticated owner and same-origin protection. Future or
  absent versions are refused. A delayed acknowledgement cannot mark newer changes
  seen, and the marker never moves backward. PostgreSQL story locking serializes
  acknowledgements with the existing follow path; concurrent behavior requires its
  own real-database validation before release.
- The existing last_read_version column is reused only as an explicit follow/seen
  marker. This does not turn on implicit reading history or sensitive personalization.

## Bounded history and UI

At most 25 followed stories are scanned per page (the UI requests five). The cursor
advances past scanned stories, including quiet ones, so later updates cannot starve.
At most 50 history transitions are assessed per story. The returned through_version
never jumps a missing version or the unexamined tail. The UI acknowledges only that
examined boundary, not an unseen newer version. Partial history remains labeled.

News now includes a progressive-disclosure Stories you follow section with explicit
Check followed stories and Mark shown updates seen controls. Pending requests clear
old results, abort on unmount, and never auto-acknowledge. Source headlines are escaped
and uncertainty stays textual. This is static-tested UI, not measured accessibility.

## Validation and limits

31 authored backend/ASGI tests and seven static-render/pure UI tests passed in the
existing offline process sandbox. Owner dotenv reads and outbound networking were
denied. Focused Ruff/formatter checks and strict mypy on the two new backend modules
passed. Biome passed for the five new/modified UI files.

The initial check found three unused test imports, which were removed using Ruff's
safe fixes. A Biome invocation from the repository root found nested configurations;
using the web workspace's existing command location resolved it without changing
configuration. No assertion, threshold, or security boundary was weakened.

The earlier five-file capability fix is preserved separately. Its previously blocked
full validation request is not retried here. The complete current-tree API/web/build,
real PostgreSQL follow tests and new-head hosted CI remain required before publication.
No source/provider flag, ordinary worker, account, deployment or merge was changed.

## Follow-update review and hardening

Two new negative acknowledgement cases were first run against the local draft:
acknowledging across a missing history version and acknowledging beyond the bounded
history page both incorrectly succeeded. The initial run's two failures are retained.
The server now recomputes the examined history boundary before advancing a marker.
It also rejects malformed saved markers and rechecks the current preference owner,
follow flag and dismissal state before constructing a follow-update projection.

The News panel now removes the stateful followed-update subtree while the parent
refreshes its catalog or performs a source/preference operation. This uses component
identity reset rather than retaining old results through a refresh. The existing
unmount cleanup aborts an outstanding read; it does not undo an acknowledged write.
No request is automatically sent merely because the panel mounts again.

After the fixes, 41 authored SQLite/ASGI tests across follow domain, follow API and
story API files passed; nine static/pure UI tests passed. Focused Ruff, formatting,
Biome and strict mypy on the two backend modules passed. These are scoped checks,
not the full locked API/Web/build gate, PostgreSQL concurrency or browser acceptance.
The earlier five-file capability fix and all local follow work remain unpublished.
The prior blocked full-validation operation has not been retried or rerouted.
