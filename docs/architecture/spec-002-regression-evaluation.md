# SPEC-002 regression evidence

The acceptance suite runs the actual extraction validator, evidence persistence,
entity and temporal resolution, commitment resolution, lifecycle inference,
attention calculation, Today projection, and bounded feedback adjustment. SQLite
foreign-key enforcement is enabled in the end-to-end demonstrations.

The three required demonstrations are executable tests in
`services/api/tests/test_intelligence_demos.py`:

1. Calendar meeting plus a budget request resolves one budget commitment, appears
   in Today with evidence and ranking factors, accepts idempotent feedback, and
   completes only after sent evidence.
2. An approval request enters waiting after it is sent. An unrelated sender's
   response cannot finish it; the expected counterparty's approval can.
3. Promotional content, replayed source versions, injected instructions, and
   forged permission fields produce no extra commitment or external action.

A fourth demonstration tests cross-workspace source, Today, and feedback
isolation. All model responses and input documents are synthetic test fixtures;
these are application regression results, **not measured live model accuracy**.
The SPEC-002 precision/recall targets remain unmeasured until a separately labeled
live-provider evaluation has been run. No production credentials are required or
used by this regression suite.

From `services/api`:

```bash
uv run pytest tests/test_intelligence_*.py --junitxml=/tmp/navox-spec002-tests.xml
uv run python -m navox.evaluation.intelligence_report \
  --junit /tmp/navox-spec002-tests.xml \
  --output /tmp/navox-spec002-regression.json
```

`evals/intelligence/gates.json` requires every demonstration, zero failures and
zero skips. The report records executed counts from JUnit instead of hardcoding
success counts. CI retains the test evidence and report as artifacts. Mocked
connector protocol tests also cover read-scope checks, delta handling, replay and
error boundaries; successful synthetic tests do not prove live watch delivery,
OAuth configuration, or real model quality.
