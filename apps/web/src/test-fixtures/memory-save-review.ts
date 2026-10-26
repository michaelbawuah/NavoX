import type { VoxMemorySaveReview } from "../lib/vox-memory-save-review";

/** Labelled synthetic proposal; no student/provider data. */
export function memorySaveReviewFixture(now = Date.now()): VoxMemorySaveReview {
  return {
    status: "review_required",
    applied: false,
    review: {
      id: "10000000-0000-4000-8000-000000000001",
      proposal: "synthetic-encrypted-proof",
      label: "Synthetic private preference",
      content: "Synthetic QA private statement, for review only.",
      source_conversation_id: "20000000-0000-4000-8000-000000000001",
      source_kind: "vox",
      source_turn: 1,
      expires_at: new Date(now + 120000).toISOString(),
      sensitive: true,
    },
    destination: "/settings#memory-privacy",
    message: "Review in Settings. Nothing saved as a fact.",
  };
}
