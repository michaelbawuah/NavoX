import { expect, it } from "vitest";
import {
  memoryLearningFixture,
  savedSuggestionFixture,
  suggestion,
} from "../test-fixtures/memory-learning";
import { readMemoryLearning, readSavedSuggestion } from "./memory-learning";

it("reads only bounded private, source-linked suggestion rows and a truthful capability", () => {
  expect(readMemoryLearning(memoryLearningFixture())).toEqual(
    memoryLearningFixture(),
  );
  for (const changes of [
    { pending: Array.from({ length: 21 }, () => suggestion) },
    { pending: [suggestion, suggestion] },
    { pending: [{ ...suggestion, source_kind: "user_saved" }] },
    { pending: [{ ...suggestion, source_conversation_id: "not-owned-proof" }] },
    { pending: [{ ...suggestion, sensitive: false }] },
    { pending: [{ ...suggestion, label: "x".repeat(81) }] },
    { pending: [{ ...suggestion, content: "x".repeat(501) }] },
    { pending: [{ ...suggestion, content: "Private\u0000value" }] },
    { pending: [{ ...suggestion, expires_at: "2026-11-03T12:00:00Z" }] },
    { pending: [{ ...suggestion, expires_at: suggestion.created_at }] },
    { pending: [{ ...suggestion, proof: "unexpected private field" }] },
    { status_reason: "connected" },
    { generation: -1 },
    { configured: "true" },
    { enabled: false, status_reason: "ready" },
    { enabled: true, status_reason: "off_record" },
  ])
    expect(() =>
      readMemoryLearning({ ...memoryLearningFixture(), ...changes }),
    ).toThrow();
});
it("requires the exact immutable private saved fact receipt", () => {
  expect(readSavedSuggestion(savedSuggestionFixture(), suggestion)).toEqual(
    savedSuggestionFixture(),
  );
  for (const changes of [
    { id: suggestion.source_conversation_id },
    { content: "Different private statement" },
    { label: "Different" },
    { source_kind: "vox" },
    { source_conversation_id: suggestion.id },
    { sensitive: false },
  ])
    expect(() =>
      readSavedSuggestion(
        { ...savedSuggestionFixture(), ...changes },
        suggestion,
      ),
    ).toThrow();
});
