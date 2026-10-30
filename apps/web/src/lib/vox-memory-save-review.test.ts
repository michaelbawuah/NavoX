import { describe, expect, it } from "vitest";
import { memorySaveReviewFixture } from "../test-fixtures/memory-save-review";
import {
  consumeMemoryCandidateDraft,
  memorySaveDraftKey,
  parseVoxMemorySaveReview,
  saveMemoryCandidateDraft,
} from "./vox-memory-save-review";

const now = Date.parse("2026-10-26T18:00:00Z");
const encode = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value));
function storage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
    removeItem: (key) => {
      values.delete(key);
    },
    clear: () => values.clear(),
    key: (index) => [...values.keys()][index] ?? null,
  };
}
describe("private candidate-review metadata", () => {
  it("accepts only a current, bounded, explicit review payload", () => {
    const value = memorySaveReviewFixture(now);
    expect(parseVoxMemorySaveReview(encode(value), now)).toEqual(value);
    for (const changes of [
      { applied: true },
      { destination: "https://other.invalid" },
      { unexpected: "private content" },
      { message: "x".repeat(801) },
    ])
      expect(
        parseVoxMemorySaveReview(encode({ ...value, ...changes }), now),
      ).toBeNull();
    for (const changes of [
      { sensitive: undefined },
      { source_turn: 0 },
      { source_kind: "chat" },
      { proposal: "x".repeat(8193) },
      { content: "x".repeat(501) },
      { expires_at: new Date(now).toISOString() },
      { expires_at: new Date(now + 300001).toISOString() },
      { extra: "unverified" },
    ])
      expect(
        parseVoxMemorySaveReview(
          encode({ ...value, review: { ...value.review, ...changes } }),
          now,
        ),
      ).toBeNull();
    expect(parseVoxMemorySaveReview(new Uint8Array(16385), now)).toBeNull();
    expect(parseVoxMemorySaveReview(new Uint8Array([255]), now)).toBeNull();
  });
  it("keeps proof in a one-use, expiring, exact-owner browser handoff", () => {
    const target = storage(),
      value = memorySaveReviewFixture(now);
    saveMemoryCandidateDraft(target, "school:student", value.review, now);
    expect(consumeMemoryCandidateDraft(target, "school:other", now)).toBeNull();
    expect(consumeMemoryCandidateDraft(target, "school:student", now)).toEqual(
      value.review,
    );
    expect(
      consumeMemoryCandidateDraft(target, "school:student", now),
    ).toBeNull();
    saveMemoryCandidateDraft(target, "school:student", value.review, now);
    expect(
      consumeMemoryCandidateDraft(target, "school:student", now + 120001),
    ).toBeNull();
    expect(target.getItem(memorySaveDraftKey("school:student"))).toBeNull();
    target.setItem(
      memorySaveDraftKey("school:student"),
      JSON.stringify({
        schema: "navox-memory-save-review.v1",
        accountKey: "school:other",
        createdAt: now,
        review: value.review,
      }),
    );
    expect(
      consumeMemoryCandidateDraft(target, "school:student", now),
    ).toBeNull();
  });
});
