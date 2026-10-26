export type MemoryPrivacy = {
  history_enabled: boolean;
  retention_days: 30 | 90 | null;
  off_the_record: boolean;
  generation: number;
  disclosure_version: 1 | null;
};
export type MemoryFact = {
  id: string;
  label: string;
  content: string;
  source_kind: "user_saved" | "vox" | "chat";
  source_conversation_id: string | null;
  created_at: string;
  updated_at: string;
  sensitive: boolean;
};
export type MemoryHistory = {
  id: string;
  title: string;
  source_kind: "vox" | "chat";
  created_at: string;
  updated_at: string;
  message_count: number;
};
export type MemoryConversation = MemoryHistory & {
  turns: { role: "user" | "assistant"; content: string; created_at: string }[];
  summary: string | null;
};
type Row = Record<string, unknown>;
function object(value: unknown): Row {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Your memory could not be read. Please try again.");
  return value as Row;
}
function text(value: unknown, max = 20000): value is string {
  return typeof value === "string" && value.length <= max;
}
function date(value: unknown): value is string {
  return text(value, 64) && Number.isFinite(Date.parse(value));
}
function integer(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}
function invalid(): never {
  throw new Error("Your memory could not be read. Please try again.");
}
export function readMemoryPrivacy(value: unknown): MemoryPrivacy {
  const row = object(value);
  if (
    typeof row.history_enabled !== "boolean" ||
    ![30, 90, null].includes(row.retention_days as number | null) ||
    typeof row.off_the_record !== "boolean" ||
    !integer(row.generation) ||
    ![1, null].includes(row.disclosure_version as number | null) ||
    (row.history_enabled && row.disclosure_version !== 1)
  )
    invalid();
  return row as MemoryPrivacy;
}
export function readMemoryFacts(value: unknown): MemoryFact[] {
  if (!Array.isArray(value) || value.length > 100) invalid();
  return value.map((value) => {
    const row = object(value);
    if (
      !text(row.id, 128) ||
      !row.id ||
      !text(row.label, 80) ||
      !text(row.content, 500) ||
      !["user_saved", "vox", "chat"].includes(String(row.source_kind)) ||
      !(
        row.source_conversation_id === null ||
        text(row.source_conversation_id, 128)
      ) ||
      !date(row.created_at) ||
      !date(row.updated_at) ||
      typeof row.sensitive !== "boolean"
    )
      invalid();
    return row as MemoryFact;
  });
}
function readHistoryRow(value: unknown): MemoryHistory {
  const row = object(value);
  if (
    !text(row.id, 128) ||
    !row.id ||
    !text(row.title, 500) ||
    !["vox", "chat"].includes(String(row.source_kind)) ||
    !date(row.created_at) ||
    !date(row.updated_at) ||
    !integer(row.message_count)
  )
    invalid();
  return row as MemoryHistory;
}
export function readMemoryHistory(value: unknown): MemoryHistory[] {
  if (!Array.isArray(value) || value.length > 100) invalid();
  return value.map(readHistoryRow);
}
export type MemoryHistoryPage = {
  items: MemoryHistory[];
  next_cursor: string | null;
  search_complete: boolean;
  scanned: number;
};
export function readMemoryHistoryPage(value: unknown): MemoryHistoryPage {
  const row = object(value);
  const items = readMemoryHistory(row.items);
  if (
    !(
      row.next_cursor === null ||
      (text(row.next_cursor, 8192) && row.next_cursor.length > 0)
    ) ||
    typeof row.search_complete !== "boolean" ||
    row.search_complete !== (row.next_cursor === null) ||
    !integer(row.scanned) ||
    Number(row.scanned) > 100 ||
    items.length > Number(row.scanned) ||
    (row.scanned === 0 && row.next_cursor !== null)
  )
    invalid();
  return {
    items,
    next_cursor: row.next_cursor,
    search_complete: row.search_complete,
    scanned: row.scanned,
  } as MemoryHistoryPage;
}
export function readMemoryConversation(value: unknown): MemoryConversation {
  const row = object(value);
  readHistoryRow(row);
  if (
    !(row.summary === null || text(row.summary)) ||
    !Array.isArray(row.turns) ||
    row.turns.length > 2000
  )
    invalid();
  for (const value of row.turns as unknown[]) {
    const turn = object(value);
    if (
      !["user", "assistant"].includes(String(turn.role)) ||
      !text(turn.content) ||
      !date(turn.created_at)
    )
      invalid();
  }
  return row as MemoryConversation;
}
export class MemoryClientError extends Error {
  constructor(
    message: string,
    readonly uncertain = false,
  ) {
    super(message);
  }
}
const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export async function conversationMemoryRequest<T>(
  accountKey: string,
  path: string,
  read: (value: unknown) => T,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`${api}/memory${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
      headers: {
        "X-NavoX-Account": accountKey,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const payload: unknown =
      response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      throw new MemoryClientError(
        response.status === 409
          ? "Your memory settings changed. Reload them before trying again."
          : response.status === 401 || response.status === 403
            ? "Check your sign-in before opening your memory."
            : response.status === 422
              ? "Check the details and any sensitive-information agreement, then try again."
              : "Your memory could not be updated. Please try again.",
        method !== "GET" && response.status >= 500,
      );
    }
    return read(payload);
  } catch (cause) {
    if (cause instanceof MemoryClientError) throw cause;
    throw new MemoryClientError(
      method === "GET"
        ? "Your memory could not load. Please try again."
        : "The result could not be checked. Reload your memory before making another change.",
      method !== "GET",
    );
  } finally {
    clearTimeout(timeout);
  }
}
export function memorySourceLabel(kind: MemoryFact["source_kind"]): string {
  return kind === "vox"
    ? "From a Vox call"
    : kind === "chat"
      ? "From a chat"
      : "Added by you";
}
