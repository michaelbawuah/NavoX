#!/usr/bin/env bash
# Export the current checkpoint and review-only catalog proposals. No DB writes,
# provider requests, traffic changes, mailbox reads or sends are performed.
set -euo pipefail
umask 077
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
if [ "$(git branch --show-current)" != "spec-005-ai-gateway" ]; then
  printf '%s\n' 'Switch to the reviewed spec-005-ai-gateway branch before running this script.' >&2
  exit 1
fi
if ! git diff --quiet HEAD -- services/api scripts/spec005-readiness.sh; then
  printf '%s\n' 'Local API changes need review before exporting the checkpoint.' >&2
  exit 1
fi
run_id="navox-spec005-readiness-$(date -u +%Y%m%dT%H%M%SZ)"
output_parent="${NAVOX_EVIDENCE_DIR:-$HOME/Desktop}"
output_dir="$output_parent/$run_id"
if [ ! -d "$output_parent" ] || [ -e "$output_dir" ] || [ -e "$output_dir.zip" ]; then
  printf '%s\n' 'Evidence directory is missing or this run already exists.' >&2
  exit 1
fi
mkdir -m 700 "$output_dir"
git rev-parse HEAD > "$output_dir/source-commit.txt"
# Use a one-off image from this checkout. Existing API/worker containers and
# their environment remain unchanged. The PostgreSQL service must be running.
docker compose build api
docker compose run --rm --no-deps -T api uv run --no-sync python -m navox.ai.readiness inventory \
  > "$output_dir/readiness.json"
expected_digest='3074bfb2edbc8c03a36ef6543ce646928fb9423a6ea5d4a6308b59b8c3e68dc1'
# Produce both review alternatives without publishing either. The second only
# proposes model capability ceilings; it does not change operator/user grants.
for variant in public-only personal-proposal; do
  proposal_flags=(--directory /tmp/review-proposal)
  if [ "$variant" = 'personal-proposal' ]; then
    proposal_flags+=(--personal-provider openai --personal-provider gemini --personal-provider anthropic)
  fi
  container_name="$run_id-$variant"
  docker compose run --name "$container_name" --no-deps -T api uv run --no-sync python \
    -m navox.ai.readiness prepare --expected-revision 4 --expected-digest "$expected_digest" \
    "${proposal_flags[@]}" > "$output_dir/$variant.log"
  docker cp "$container_name:/tmp/review-proposal" "$output_dir/$variant"
  docker rm "$container_name" > /dev/null
done
cp docs/architecture/spec-005-live-acceptance.md "$output_dir/acceptance-steps.md"
(cd "$output_parent" && zip -qr "$run_id.zip" "$run_id")
printf '\nReview archive: %s\n' "$output_dir.zip"
shasum -a 256 "$output_dir.zip"
printf '%s\n' 'No catalog was published. Upload this archive for review before live evaluation.'
