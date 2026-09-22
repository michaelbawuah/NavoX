# NavoX evaluation suites

Milestone 9 makes product quality measurable instead of relying on demos.

The committed datasets are synthetic and contain no user data. The
`navox-reference / synthetic-reference-v1` provider snapshot is a deterministic
reference fixture used to prove the benchmark plumbing; it is **not** a claim
about the quality, latency, or cost of any external model.

## Suites

- `intelligence/extraction_cases.json`: SPEC-002 operational extraction labels.\n- `intelligence/adversarial_cases.json`: prompt-injection, authority, and evidence-boundary attacks.\n- `intelligence/reference-baseline.json`: deterministic SPEC-002 extraction plumbing snapshot.\n- `commitments/labeled_cases.json`: precision and recall labels.\n- `commitments/malicious_outputs.json`: strict-schema and prompt-injection rejection.
- `commitments/false_positive_outputs.json`: confidence-policy suppression.
- `briefing/cases.json`: attention-tier and signal-type policy.
- `planning/action_policy_cases.json`: tool/risk/permission decisions.
- `security/control_matrix.json`: evidence map for the security threats named in SPEC-001.
- `providers/*.json`: provider/model output snapshots on the same labeled cases.
- `gates.json`: release thresholds.

## Run

From `services/api`:

```bash
uv run python -m navox.evaluation --fail-on-gate \
  --output /tmp/navox-evaluation.json \
  --markdown /tmp/navox-evaluation.md
```

SPEC-002 operational extraction is gated on exact reference labels, strict schema validation, bounded source evidence, and adversarial rejection. The reference baseline proves evaluation plumbing only.\n\nA real provider comparison must use the same case IDs and snapshot schema. Keep\nprivate prompts, credentials, emails, and production content out of committed
evaluation artifacts.
