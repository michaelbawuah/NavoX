# SPEC-008 M3C — exact email selection

Status: locally implemented on `spec-008-navoxbot`; uncommitted and unpushed.
The owner explicitly deferred SPEC-006/007 PR #21's merge until GitHub billing
allows its blocked hosted job to run.

When connected search returns several email messages, the assistant now offers
an explicit Select control for each exact `EMAIL` item. The click submits a
bounded TypeScript turn with the source-turn and resource selectors. The runtime
checks that the source turn belongs to the current session, was a complete
ambiguous email result, and listed that exact message once. It then rechecks
the account and repeats SPEC-007 search; missing, thread-only, duplicate or
incomplete current evidence cannot become a `READY` turn. A valid choice gets
one selected-message turn that may use the existing draft/review/approval path.
No model call is needed for the click, and no new send or approval mechanism
was added. The existing communication service still reauthorizes the Gmail
source at draft and execution boundaries.

Focused runtime tests cover successful choice, forged unlisted resource,
vanished result and incomplete refresh, with no draft generation on failure.
Web rendering tests cover showing the Select control only for complete
ambiguity. Root lint and TypeScript typecheck passed; root tests passed with
280 Web tests and 160 runtime tests (five DB-only tests skipped with the
disposable PostgreSQL stack down), and the Web production build passed. The
only lint warnings are existing Web CSS specificity and controller optional
chain suggestions. No Python, migration, or database schema changed in M3C.

Still open: natural-language follow-up selection such as “the second one,”
thread-to-message resolution, live authorized Gmail/provider observation,
voice read-aloud of the draft, other domains and the full SPEC-008 acceptance
matrix. This local result does not establish live send or hosted CI success.
