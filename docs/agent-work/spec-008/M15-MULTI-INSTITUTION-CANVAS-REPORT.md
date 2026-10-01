# SPEC-008 M15 — multi-institution Canvas connection

Students now choose a school from a server-reviewed Canvas catalog before
starting OAuth. The setup endpoint exposes only institution ID, name and exact
HTTPS origin; the OAuth client secret stays in deployment configuration. The
single-school configuration remains a fallback when no catalog is configured.

The selected deployment fingerprint is saved in one-use OAuth state. The
callback resolves that exact deployment before code exchange and rechecks it
after I/O. Existing connections retain an origin and fingerprint, so reconnect,
refresh and sync cannot move a student to another institution or silently use a
rotated key. Canvas account IDs are distinguished by origin. The Web school
selector verifies the returned authorization URL against the chosen origin.

The operator must register an institution-approved developer key or an enabled
Instructure global key before a school can appear in the catalog. No Cornell
setting is required for students elsewhere. There is no live school OAuth
acceptance yet because no approved institution key is configured locally.

Focused validation: Canvas API/transport tests and multi-school configuration
tests pass; the Web school-catalog and redirect tests pass. The full repository
gate and exact-head hosted CI remain required before this checkpoint is pushed.
