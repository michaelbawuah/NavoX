export const VOX_MEMORY_REVIEW_TOPIC = "navox.vox.memory-review.v1";
const uuid =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const maximumAge = 5 * 60_000;

export type VoxMemoryMatch = {
  kind: "fact" | "conversation" | "legacy_fact";
  id: string;
  label: string;
  source_kind: "user_saved" | "vox" | "chat";
  source_conversation_id: string | null;
};
export type VoxMemoryReview = {
  status: "review_required";
  applied: false;
  query: string;
  destination: string;
  matches: VoxMemoryMatch[];
  ambiguous: boolean;
  has_more_matches: boolean;
  search_complete: boolean;
  searched_items: number;
  message: string;
};

export function validMemoryForgetQuery(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= 200 &&
    !Array.from(value).some(
      (c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127,
    )
  );
}

export function parseVoxMemoryReview(data: Uint8Array): VoxMemoryReview | null {
  if (data.byteLength > 8192) return null;
  try {
    const value = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(data),
    );
    if (
      value?.status !== "review_required" ||
      value.applied !== false ||
      !validMemoryForgetQuery(value.query) ||
      typeof value.destination !== "string" ||
      !value.destination.startsWith("/settings?") ||
      value.destination.length > 2000 ||
      !Array.isArray(value.matches) ||
      value.matches.length > 4 ||
      typeof value.ambiguous !== "boolean" ||
      typeof value.has_more_matches !== "boolean" ||
      typeof value.search_complete !== "boolean" ||
      !Number.isSafeInteger(value.searched_items) ||
      value.searched_items < 0 ||
      value.searched_items > 100 ||
      typeof value.message !== "string" ||
      value.message.length > 800
    )
      return null;
    const destination = new URL(value.destination, "https://navox.invalid");
    if (
      destination.origin !== "https://navox.invalid" ||
      destination.pathname !== "/settings" ||
      destination.hash ||
      destination.searchParams.get("section") !== "memory" ||
      destination.searchParams.get("forget") !== value.query ||
      [...destination.searchParams.keys()].length !== 2
    )
      return null;
    const matches: VoxMemoryMatch[] = [];
    const seen = new Set<string>();
    for (const row of value.matches) {
      if (
        !row ||
        typeof row !== "object" ||
        !["fact", "conversation", "legacy_fact"].includes(row.kind) ||
        typeof row.id !== "string" ||
        !uuid.test(row.id) ||
        typeof row.label !== "string" ||
        row.label.length > 80 ||
        !["user_saved", "vox", "chat"].includes(row.source_kind) ||
        !(
          row.source_conversation_id === null ||
          (typeof row.source_conversation_id === "string" &&
            uuid.test(row.source_conversation_id))
        ) ||
        seen.has(`${row.kind}:${row.id}`)
      )
        return null;
      seen.add(`${row.kind}:${row.id}`);
      matches.push({
        kind: row.kind,
        id: row.id,
        label: row.label,
        source_kind: row.source_kind,
        source_conversation_id: row.source_conversation_id,
      });
    }
    // Keep metadata only; unsolicited content fields never reach call state or storage.
    return {
      status: "review_required",
      applied: false,
      query: value.query,
      destination: "/settings?section=memory#memory-privacy",
      matches,
      ambiguous: value.ambiguous,
      has_more_matches: value.has_more_matches,
      search_complete: value.search_complete,
      searched_items: value.searched_items,
      message: value.message,
    };
  } catch {
    return null;
  }
}

export function memoryForgetDraftKey(accountKey: string): string {
  return `navox:memory-forget-review:${accountKey}`;
}

export function saveMemoryForgetDraft(
  storage: Storage,
  accountKey: string,
  query: string,
  now = Date.now(),
): void {
  if (!accountKey || !validMemoryForgetQuery(query) || !Number.isFinite(now))
    throw new Error("Invalid memory review");
  storage.setItem(
    memoryForgetDraftKey(accountKey),
    JSON.stringify({
      schema: "navox-memory-forget-review.v1",
      accountKey,
      query,
      createdAt: now,
    }),
  );
}

export function consumeMemoryForgetDraft(
  storage: Storage,
  accountKey: string,
  now = Date.now(),
): string | null {
  try {
    const key = memoryForgetDraftKey(accountKey),
      raw = storage.getItem(key);
    if (!raw) return null;
    storage.removeItem(key);
    if (raw.length > 2000) return null;
    const value = JSON.parse(raw);
    if (
      value?.schema !== "navox-memory-forget-review.v1" ||
      value.accountKey !== accountKey ||
      !validMemoryForgetQuery(value.query) ||
      !Number.isFinite(value.createdAt) ||
      now - value.createdAt < -30_000 ||
      now - value.createdAt > maximumAge
    )
      return null;
    return value.query;
  } catch {
    return null;
  }
}
