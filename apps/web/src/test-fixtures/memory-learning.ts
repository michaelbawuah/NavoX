import type { MemoryLearning } from "../lib/memory-learning";
export const suggestion = {
  id: "a1000000-0000-4000-8000-000000000001",
  label: "Family schedule",
  content: "My family schedule changes every Friday.",
  source_conversation_id: "b1000000-0000-4000-8000-000000000001",
  source_kind: "chat" as const,
  sensitive: true as const,
  created_at: "2026-10-26T12:00:00Z",
  expires_at: "2026-11-02T12:00:00Z",
};
export function memoryLearningFixture(): MemoryLearning {
  return {
    enabled: true,
    configured: true,
    generation: 4,
    status_reason: "ready",
    pending: [{ ...suggestion }],
    has_more: false,
  };
}
export function savedSuggestionFixture() {
  const { expires_at: _, ...fact } = suggestion;
  return { ...fact, updated_at: suggestion.created_at };
}
