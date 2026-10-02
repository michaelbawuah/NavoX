# SPEC-008 broad News integration — 2026-10-02

This is an integration checkpoint on `spec-008-navoxbot`, not final SPEC-008
acceptance, a main merge, or a public `navox.net` deployment.

## Delivered

- Perigon connects through the existing owner-consented REST setup and encrypted,
  tenant-bound SecretBroker. No token is stored in a source definition, URL, Git
  file, workflow payload or raw feed archive.
- A bounded News API reader uses the current approved Perigon profile, selected
  read capability, DNS-pinned HTTPS transport, header authentication, deadlines,
  response-size limits and fixed error codes. It rechecks current connection
  authority after retrieval; normal News rights and retention checks remain.
- News-only REST credentials are excluded from generic operational extraction
  and reconciliation. Existing operational connector IDs and existing RSS/Atom
  source fingerprints retain their previous identity.
- Five publisher feeds are enabled for the existing owner: AP, Reuters, BBC,
  CNN and CNBC. Initial ingestion completed once for each: 50 items, zero
  rejected, and both Latest and Trending returned 50 stories.
- Current classifications include World, U.S., Business and Technology. The
  Science tab exists; this initial sample did not contain a Science article.
- Article bodies, descriptions/snippets, images, generated summaries and
  provider verdicts are not stored. Publisher names and original links remain.
  There is one conservative independence group and no automatic verification
  authority. Trending remains an observed-activity view.
- The live check caught a general-trending request incorrectly asking for a
  named topic. Exact general News question spans are normalized only after a
  valid qualified planner envelope. Planner failures, authority checks, named
  topics, follow-up references and write/approval boundaries remain enforced.
- Updated live checks returned READY for both "What's trending today?" (VOICE)
  and "What's on the news today?" (TEXT), each with three headline items.
  The voice turn returned HTTP 200 MPEG audio (127,104 bytes); the text turn did
  not request speech. Both had zero action references; test sessions were deleted.
  This is server speech verification, not observed microphone/audio playback.

## Deployment and policy

The Mac integration API, worker and web images were rebuilt and updated. Liveness,
readiness, owner authentication and the News page returned HTTP 200. Alembic is
`0034_knowledge_email_drafts`; this change adds no migration. News reconciliation
is RUNNING on `navox-foundation`; that queue and `navox-assistant-goals` each
have an active workflow poller.

The existing six owner/synthetic PLAN, STT and TTS scopes are unchanged.
Provider-policy SHA-256:
`0f45cb583ba4c08d563089e5184edaf3d052bff899676014a3a6277be0210103`.
No News AI/DRAFT provider grant was installed. Gemini remains excluded where
unqualified and Grok remains deferred.

The free trial uses one refresh per publisher per 24 hours, one-day metadata
retention and a rights expiry of 2026-10-30. This is a personal/integration trial,
not an ongoing commercial production license. Named-topic narrative synthesis
still needs its existing rights and qualified route. X trends are not connected.

## Validation

- API lint and strict mypy passed; 54 targeted News checks passed.
- The full preflight found a legacy fingerprint fixture that included newly
  introduced null API-binding fields. Its legacy-payload calculation was
  corrected, preserving the original fingerprint; all 37 relation/API-reader
  regression checks then passed. The full locked preflight rerun passed: 2,772
  tests, zero skips, five retained warnings; lint, formatter, strict mypy,
  migrations/schema, measured safety metrics and deterministic release passed.
- Assistant runtime: 391 tests passed, zero skips, including real isolated
  PostgreSQL and Temporal checks. Web: 416 tests passed. Both lint/type checks
  and the web production build passed.
- Hosted CI on this new commit remains a separate gate. The previous head
  `411382342e9c3127d407ec4522f8fcc607118446` passed all six hosted jobs
  in run 37034137602.

## Remaining release gates

Current-revision DRAFT qualification still requires genuine review of the exact
saved 16-case report; no controlled Gmail send was performed in this checkpoint.
Canvas institution OAuth credentials and an actual public NavoX hosting/DNS/OAuth
deployment remain pending. PR #22 remains draft until mandatory acceptance is
complete. No Team functionality is included.
