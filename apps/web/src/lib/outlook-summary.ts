import {
  canReadOutlook,
  type OutlookMessage,
  outlookRequest,
  readOutlookMessages,
  readOutlookStatus,
} from "./outlook";

export const OUTLOOK_SUMMARY_MAILBOX_LIMIT = 3;
export interface OutlookReplyCandidate extends OutlookMessage {
  connection_id: string;
}
export interface OutlookReplySummary {
  connected: boolean;
  messages: OutlookReplyCandidate[];
  checked_mailboxes: number;
  available_mailboxes: number;
  limited: boolean;
  unavailable: boolean;
}
export interface OutlookReplyTarget {
  connectionId: string;
  messageId: string;
}
const mailboxId =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function messageId(value: string): boolean {
  return (
    value.length > 0 &&
    value.length <= 512 &&
    !Array.from(value).some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  );
}
function active(signal?: AbortSignal) {
  if (signal?.aborted) throw new Error("This Outlook check was cancelled.");
}
function page(value: unknown) {
  const parsed = readOutlookMessages(value);
  if (
    parsed.messages.length > 50 ||
    parsed.messages.some((message) => !messageId(message.id))
  )
    throw new Error("Outlook returned an unreadable message list.");
  return parsed;
}

/** Metadata-only, bounded reads. No message bodies, drafts, mutations or pagination. */
export async function loadOutlookReplySummary(
  accountKey: string,
  signal?: AbortSignal,
): Promise<OutlookReplySummary> {
  active(signal);
  const status = await outlookRequest(
    accountKey,
    "/status",
    readOutlookStatus,
    "GET",
    undefined,
    signal,
  );
  active(signal);
  const readable = status.configured
    ? [
        ...new Map(
          status.connections
            .filter(canReadOutlook)
            .map((connection) => [connection.id, connection]),
        ).values(),
      ]
    : [];
  if (!readable.length)
    return {
      connected: false,
      messages: [],
      checked_mailboxes: 0,
      available_mailboxes: 0,
      limited: false,
      unavailable: false,
    };
  const selected = readable.slice(0, OUTLOOK_SUMMARY_MAILBOX_LIMIT);
  const results = await Promise.allSettled(
    selected.map((connection) =>
      outlookRequest(
        accountKey,
        `/connections/${encodeURIComponent(connection.id)}/messages`,
        page,
        "GET",
        undefined,
        signal,
      ),
    ),
  );
  active(signal);
  // Recheck consent after the bounded provider reads; a revoked mailbox loses its rows.
  const latest = await outlookRequest(
    accountKey,
    "/status",
    readOutlookStatus,
    "GET",
    undefined,
    signal,
  );
  active(signal);
  const allowed = new Set(
    latest.configured
      ? latest.connections
          .filter(canReadOutlook)
          .map((connection) => connection.id)
      : [],
  );
  const messages: OutlookReplyCandidate[] = [];
  const seen = new Set<string>();
  let checked = 0,
    limited = allowed.size > selected.length,
    unavailable = false;
  for (let index = 0; index < results.length; index++) {
    const connection = selected[index];
    if (!allowed.has(connection.id)) continue;
    const result = results[index];
    if (result.status === "rejected") {
      unavailable = true;
      continue;
    }
    checked++;
    limited ||= result.value.has_more;
    for (const message of result.value.messages) {
      const key = `${connection.id}:${message.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      messages.push({ ...message, connection_id: connection.id });
    }
  }
  const checkedIds = new Set(selected.map((connection) => connection.id));
  limited ||= [...allowed].some((id) => !checkedIds.has(id));
  return {
    connected: allowed.size > 0,
    messages,
    checked_mailboxes: checked,
    available_mailboxes: allowed.size,
    limited,
    unavailable,
  };
}

export function outlookReplyHref(
  accountKey: string,
  connectionId: string,
  sourceId: string,
): string {
  const query = new URLSearchParams({
    outlook_owner: accountKey,
    outlook_mailbox: connectionId,
    outlook_reply: sourceId,
  });
  return `/inbox?${query.toString()}#outlook-inbox`;
}

/** A navigation hint grants no authority; the destination still verifies the mailbox. */
export function readOutlookReplyTarget(
  search: string,
  accountKey: string,
): OutlookReplyTarget | null {
  const query = new URLSearchParams(search);
  if (
    ["outlook_owner", "outlook_mailbox", "outlook_reply"].some(
      (key) => query.getAll(key).length !== 1,
    ) ||
    query.get("outlook_owner") !== accountKey
  )
    return null;
  const connectionId = query.get("outlook_mailbox") ?? "",
    sourceId = query.get("outlook_reply") ?? "";
  return mailboxId.test(connectionId) && messageId(sourceId)
    ? { connectionId, messageId: sourceId }
    : null;
}
