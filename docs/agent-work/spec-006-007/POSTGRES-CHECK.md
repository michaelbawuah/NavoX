# PostgreSQL migration verification, 29 September 2026

Disposable container only: navox-spec006007-test-20260929, postgres17-alpine,
127.0.0.1:54705, database navox_spec_test; no host mounts or owner data.

Upgradehead -> downgrade0028_news_retrieval -> upgradehead succeeded.
0029 SHA256 5af332d351f09faa426bb498e053374a742697362665240c6bba012373688b9c
0030 SHA256 244e2e170bba6fb57d06a926ec900066fc1a7921e5076f6f943ad48af9cb0878
Hashes match before and after. Earlier attempt used a wrong0028 identifier and
stopped without rollback; corrected identifier verified from migration source.

Combined PG regressions pending: worker is fixing unique-schema isolation in the
new knowledge_search_support fixture; preexisting M1/conftest already isolate schemas.
This is migration evidence on these exactbytes, not final source-tree acceptance.

News PostgreSQL cohort completed: `pytest -q tests/test_news*.py` with the disposable
DSN and existing isolated `ai_database` schemas: **262 passed in119.28s**.
JUnit `/tmp/navox-news-postgres-20260929.xml`, log `.log` alongside. Pure unit cases
remain in-process; DB-backed fixtures used PostgreSQL. No provider calls. Knowledge
helper is now isolated and ready; final corrected source cohort still pending.

Corrected Search knowledge cohort: **185 passed, 1 warning in97.19s** on PostgreSQL.
JUnit `/tmp/navox-knowledge-postgres-20260929.xml`, log alongside. This includes the
new per-test isolated schemas. Root found and fixed a final detail moved-folder
publication edge after that run; its separate PostgreSQL regression follows.

Root research12tests and new News relation+intelligence34tests passed on isolated
PostgreSQL fixtures. A final additional two-source automatic corroboration fixture
passed separately. No live source/model qualification is inferred from these runs.

## 30 September 02:35UTC

0031+0032 upgraded, downgraded to0030, and upgraded back to0032 successfully on
the disposable PostgreSQL database. Log /tmp/navox-migrations-0032-roundtrip.log.
Final0033 clustering migration is a separate next check after workerfreeze.
0031_knowledge_intelligence.py SHA256 d1e6566bb3fcf99e42dfcc88b546fe47aa39fb5639388495d588a1153b334337
0032_news_importance.py SHA256 aaaae6902d58d11cb6a6d0ca53311fb4e791d6ced2678ef757175d9988f8f5ac
