export const VOX_MEMORY_SAVE_REVIEW_TOPIC = "navox.vox.memory-save-review.v1";
const MAX_AGE = 5 * 60_000;
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export type MemorySaveCandidate = {
  id: string;
  proposal: string;
  label: string;
  content: string;
  source_conversation_id: string;
  source_kind: "vox";
  source_turn: number;
  expires_at: string;
  sensitive: boolean;
};
export type VoxMemorySaveReview = {
  status: "review_required";
  applied: false;
  review: MemorySaveCandidate;
  destination: "/settings#memory-privacy";
  message: string;
};
function exact(
  value: unknown,
  keys: string[],
): value is Record<string, unknown> {
  return (
    !!value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === keys.length &&
    Object.keys(value).every((key) => keys.includes(key))
  );
}
function text(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= maximum &&
    !value.includes("\u0000")
  );
}
export function readMemorySaveCandidate(
  value: unknown,
  now = Date.now(),
): MemorySaveCandidate | null {
  if (
    !exact(value, [
      "id",
      "proposal",
      "label",
      "content",
      "source_conversation_id",
      "source_kind",
      "source_turn",
      "expires_at",
      "sensitive",
    ]) ||
    !text(value.id, 36) ||
    !UUID.test(value.id) ||
    !text(value.proposal, 8192) ||
    !text(value.label, 80) ||
    !text(value.content, 500) ||
    !text(value.source_conversation_id, 36) ||
    !UUID.test(value.source_conversation_id) ||
    value.source_kind !== "vox" ||
    !Number.isSafeInteger(value.source_turn) ||
    Number(value.source_turn) < 1 ||
    Number(value.source_turn) > 2000 ||
    typeof value.sensitive !== "boolean" ||
    !text(value.expires_at, 64) ||
    !Number.isFinite(now) ||
    !Number.isFinite(Date.parse(value.expires_at)) ||
    Date.parse(value.expires_at) <= now ||
    Date.parse(value.expires_at) > now + MAX_AGE
  )
    return null;
  return value as MemorySaveCandidate;
}
export function parseVoxMemorySaveReview(
  data: Uint8Array,
  now = Date.now(),
): VoxMemorySaveReview | null {
  if (data.byteLength > 16384) return null;
  try {
    const value = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(data),
    );
    if (
      !exact(value, [
        "status",
        "applied",
        "review",
        "destination",
        "message",
      ]) ||
      value.status !== "review_required" ||
      value.applied !== false ||
      value.destination !== "/settings#memory-privacy" ||
      !text(value.message, 800)
    )
      return null;
    const review = readMemorySaveCandidate(value.review, now);
    return review
      ? {
          status: "review_required",
          applied: false,
          review,
          destination: "/settings#memory-privacy",
          message: value.message,
        }
      : null;
  } catch {
    return null;
  }
}
export function memorySaveDraftKey(accountKey: string): string {
  return `navox:memory-save-review:${accountKey}`;
}
export function saveMemoryCandidateDraft(
  storage: Storage,
  accountKey: string,
  review: MemorySaveCandidate,
  now = Date.now(),
): void {
  if (
    !accountKey ||
    accountKey.length > 300 ||
    !readMemorySaveCandidate(review, now)
  )
    throw new Error("This memory review is unavailable.");
  storage.setItem(
    memorySaveDraftKey(accountKey),
    JSON.stringify({
      schema: "navox-memory-save-review.v1",
      accountKey,
      createdAt: now,
      review,
    }),
  );
}
export function consumeMemoryCandidateDraft(
  storage: Storage,
  accountKey: string,
  now = Date.now(),
): MemorySaveCandidate | null {
  try {
    const key = memorySaveDraftKey(accountKey),
      raw = storage.getItem(key);
    if (!raw) return null;
    storage.removeItem(key);
    if (raw.length > 20000) return null;
    const value = JSON.parse(raw);
    if (
      !exact(value, ["schema", "accountKey", "createdAt", "review"]) ||
      value.schema !== "navox-memory-save-review.v1" ||
      value.accountKey !== accountKey ||
      typeof value.createdAt !== "number" ||
      !Number.isFinite(value.createdAt) ||
      now - value.createdAt < -30000 ||
      now - value.createdAt > MAX_AGE
    )
      return null;
    return readMemorySaveCandidate(value.review, now);
  } catch {
    return null;
  }
}
