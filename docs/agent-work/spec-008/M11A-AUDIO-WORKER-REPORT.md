# M11A audio qualification review — 1 October 2026

Status: accepted as a local foundation, not an accepted voice path. The Flash
worker implemented the code and focused tests; its final messages ended before
writing the requested report, so Astra completed this record after review.

`foundation/contracts.py` adds explicit transcription/synthesis capabilities,
task types and profiles. `ModelDefinition` gains positive audio prices per minute
and per 1000 characters; unset fields are omitted from canonical JSON so old
registry revisions keep their digest. `speech_routing.py` selects only exact
published audio task/profile bindings with fresh matching evaluation, enabled
rollout/health availability, `SENSITIVE` task/model/effective grants, known
audio-unit pricing and enough intersected budget. Text token pricing cannot
qualify speech. The default catalog has no speech profiles or assignments.
The review correction required an exact `SENSITIVE` task label and exact
transcription/synthesis profile pairing, even when broader grants exist.

Verification on the corrected tree from `services/api`:

- `UV_CACHE_DIR=/tmp/navox-spec008-uv-cache uv run pytest -q
  tests/test_ai_speech_routing.py tests/test_ai_evaluation.py` — 79 passed.
- `uv run python -m ruff check` on the five affected Python files — passed.
- `uv run python -m ruff format --check` on those files — 5 formatted.
- `uv run python -m mypy --strict` on the three affected source files — passed.
- `git diff --check` — passed.

The tests include canonical old-style snapshot load/digest compatibility, unit
cost arithmetic, missing price, text-model denial, task/profile mismatch,
stale/weak evidence, disabled rollout/health, grants, request/policy budget,
and deterministic ranking. These are controlled fixtures; no provider request,
HTTP endpoint, live qualification/publication, or paid call occurred. The full
API quality gate remains due at the integrated M11 boundary and before push.
The next phase must implement bounded audio transport/provider adapters, scoped
authorization and no-retention behavior before voice can use this selector.
