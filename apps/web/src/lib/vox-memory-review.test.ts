// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import {
  consumeMemoryForgetDraft,
  memoryForgetDraftKey,
  parseVoxMemoryReview,
  saveMemoryForgetDraft,
} from "./vox-memory-review";

const review = {
  status: "review_required",
  applied: false,
  query: "morning routine",
  destination: "/settings?section=memory&forget=morning+routine",
  matches: [
    {
      kind: "fact",
      id: "11111111-1111-4111-8111-111111111111",
      label: "Morning preference",
      source_kind: "vox",
      source_conversation_id: "22222222-2222-4222-8222-222222222222",
    },
  ],
  ambiguous: false,
  has_more_matches: false,
  search_complete: true,
  searched_items: 1,
  message: "Review the match before forgetting it.",
};
const bytes = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value));
afterEach(() => sessionStorage.clear());
it("accepts the bounded review as metadata only and fixes the navigation target", () => {
  const parsed = parseVoxMemoryReview(
    bytes({
      ...review,
      transcript: "must not retain",
      matches: [{ ...review.matches[0], content: "must not retain" }],
    }),
  );
  expect(parsed?.destination).toBe("/settings?section=memory#memory-privacy");
  expect(parsed?.matches).toEqual(review.matches);
  expect(JSON.stringify(parsed)).not.toContain("must not retain");
});
it("rejects execution claims, external or extra navigation parameters and oversized matches", () => {
  for (const value of [
    { ...review, applied: true },
    {
      ...review,
      destination:
        "https://other.test/settings?section=memory&forget=morning+routine",
    },
    { ...review, destination: `${review.destination}&next=https://other.test` },
    { ...review, destination: `${review.destination}#delete` },
    { ...review, matches: Array(5).fill(review.matches[0]) },
    { ...review, searched_items: 101 },
    { ...review, query: "x".repeat(201) },
  ])
    expect(parseVoxMemoryReview(bytes(value))).toBeNull();
});
it("carries a query only for its owner, once, for five minutes", () => {
  saveMemoryForgetDraft(
    sessionStorage,
    "school:student",
    "morning routine",
    1000,
  );
  expect(
    consumeMemoryForgetDraft(sessionStorage, "school:other", 1100),
  ).toBeNull();
  expect(consumeMemoryForgetDraft(sessionStorage, "school:student", 1100)).toBe(
    "morning routine",
  );
  expect(
    consumeMemoryForgetDraft(sessionStorage, "school:student", 1100),
  ).toBeNull();
  saveMemoryForgetDraft(
    sessionStorage,
    "school:student",
    "morning routine",
    1000,
  );
  expect(
    consumeMemoryForgetDraft(sessionStorage, "school:student", 302000),
  ).toBeNull();
  expect(
    sessionStorage.getItem(memoryForgetDraftKey("school:student")),
  ).toBeNull();
});
