import { readMemoryFacts } from "./conversation-memory";

export type PrivateMemorySuggestion = {
  id: string;
  label: string;
  content: string;
  source_conversation_id: string;
  source_kind: "vox" | "chat";
  sensitive: true;
  created_at: string;
  expires_at: string;
};
export type MemoryLearning = {
  enabled: boolean;
  configured: boolean;
  generation: number;
  status_reason:
    | "disabled"
    | "history_required"
    | "off_record"
    | "unconfigured"
    | "ready";
  pending: PrivateMemorySuggestion[];
  has_more: boolean;
};
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function invalid(): never {
  throw new Error("Memory suggestions could not be checked.");
}
function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== keys.length ||
    Object.keys(value).some((key) => !keys.includes(key))
  )
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max: number): string {
  if (
    typeof value !== "string" ||
    !value.length ||
    value.length > max ||
    [...value].some((character) => {
      const code = character.charCodeAt(0);
      return (
        (code < 32 && ![9, 10, 13].includes(code)) ||
        (code >= 127 && code <= 159)
      );
    })
  )
    return invalid();
  return value;
}
function id(value: unknown): string {
  const result = text(value, 36);
  if (!UUID.test(result)) return invalid();
  return result;
}
function instant(value: unknown): string {
  const result = text(value, 64);
  if (
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(
      result,
    ) ||
    !Number.isFinite(Date.parse(result))
  )
    return invalid();
  return result;
}
export function readMemoryLearning(value: unknown): MemoryLearning {
  const row = object(value, [
    "enabled",
    "configured",
    "generation",
    "status_reason",
    "pending",
    "has_more",
  ]);
  if (
    typeof row.enabled !== "boolean" ||
    typeof row.configured !== "boolean" ||
    !Number.isSafeInteger(row.generation) ||
    Number(row.generation) < 0 ||
    typeof row.has_more !== "boolean" ||
    ![
      "disabled",
      "history_required",
      "off_record",
      "unconfigured",
      "ready",
    ].includes(String(row.status_reason)) ||
    !Array.isArray(row.pending) ||
    row.pending.length > 20
  )
    return invalid();
  if (
    (row.status_reason === "ready" && (!row.enabled || !row.configured)) ||
    (["history_required", "off_record"].includes(String(row.status_reason)) &&
      row.enabled) ||
    (row.status_reason === "unconfigured" && row.configured)
  )
    return invalid();
  const pending = row.pending.map((value): PrivateMemorySuggestion => {
    const item = object(value, [
      "id",
      "label",
      "content",
      "source_conversation_id",
      "source_kind",
      "sensitive",
      "created_at",
      "expires_at",
    ]);
    const created_at = instant(item.created_at),
      expires_at = instant(item.expires_at);
    if (
      item.sensitive !== true ||
      !["vox", "chat"].includes(String(item.source_kind)) ||
      Date.parse(expires_at) <= Date.parse(created_at) ||
      Date.parse(expires_at) - Date.parse(created_at) > 7 * 86400000
    )
      return invalid();
    return {
      id: id(item.id),
      label: text(item.label, 80),
      content: text(item.content, 500),
      source_conversation_id: id(item.source_conversation_id),
      source_kind: item.source_kind as "vox" | "chat",
      sensitive: true,
      created_at,
      expires_at,
    };
  });
  if (new Set(pending.map((item) => item.id)).size !== pending.length)
    return invalid();
  return {
    enabled: row.enabled,
    configured: row.configured,
    generation: Number(row.generation),
    status_reason: row.status_reason as MemoryLearning["status_reason"],
    pending,
    has_more: row.has_more,
  };
}
export function readSavedSuggestion(
  value: unknown,
  suggestion: PrivateMemorySuggestion,
) {
  const fact = readMemoryFacts([value])[0];
  if (
    fact.id !== suggestion.id ||
    fact.label !== suggestion.label ||
    fact.content !== suggestion.content ||
    fact.source_kind !== suggestion.source_kind ||
    fact.source_conversation_id !== suggestion.source_conversation_id ||
    !fact.sensitive
  )
    return invalid();
  return fact;
}
