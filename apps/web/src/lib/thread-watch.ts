import type { EmailConnection } from "./email-workflow";

export type ThreadWatchStatus =
  | "watching"
  | "due"
  | "replied"
  | "paused"
  | "cancelled"
  | "needs_access"
  | "check_failed";

export interface ThreadWatch {
  id: string;
  action_id: string;
  connection_id: string;
  commitment_id: string | null;
  compose_commitment_id: string | null;
  recipient_email: string;
  sender_email: string;
  subject: string;
  remind_at: string;
  status: ThreadWatchStatus;
  reason: string | null;
  last_checked_at: string | null;
  replied_at: string | null;
  next_check_at: string | null;
  provider_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface WatchableSendAction {
  id: string;
  status: string;
  verified_at?: string | null;
  payload: { connection_id?: string };
  result: { message_id?: string; thread_id?: string | null };
}

export type ReminderChoice = "1" | "2" | "7" | "custom";
const dayMilliseconds = 86_400_000;

export function watchEligibility(
  action: WatchableSendAction,
  connections: EmailConnection[],
): "eligible" | "unverified" | "missing_thread" | "needs_access" {
  if (action.status !== "completed" || !action.verified_at) return "unverified";
  if (!action.result.message_id || !action.result.thread_id)
    return "missing_thread";
  const connection = connections.find(
    (item) => item.id === action.payload.connection_id,
  );
  if (
    connection?.status !== "active" ||
    !connection.granted_scopes.includes(
      "https://www.googleapis.com/auth/gmail.readonly",
    )
  )
    return "needs_access";
  return "eligible";
}

export function isActiveThreadWatch(watch: ThreadWatch): boolean {
  return !["replied", "cancelled"].includes(watch.status);
}

export function threadWatchStatusLabel(status: ThreadWatchStatus): string {
  const labels: Record<ThreadWatchStatus, string> = {
    watching: "Watching for a reply",
    due: "No reply at last check",
    replied: "Reply detected",
    paused: "Watch paused",
    cancelled: "Watch cancelled",
    needs_access: "Gmail read access needed",
    check_failed: "Check needs attention",
  };
  return labels[status] ?? "Watch status unavailable";
}

// Only the server's Gmail thread links are opened; never accept a redirect URL.
export function safeThreadProviderUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.hostname !== "mail.google.com" ||
      url.port ||
      url.username ||
      url.password ||
      !url.pathname.startsWith("/mail/") ||
      !/^#(?:all|inbox|sent)\/[a-zA-Z0-9_-]+$/.test(url.hash) ||
      [...url.searchParams.keys()].some((key) => key !== "authuser")
    )
      return null;
    return url.href;
  } catch {
    return null;
  }
}

export function threadWatchReplyTarget(
  watch: ThreadWatch,
  eligibleCommitmentIds: string[],
): string | null {
  if (
    watch.status !== "due" ||
    !watch.compose_commitment_id ||
    !eligibleCommitmentIds.includes(watch.compose_commitment_id)
  )
    return null;
  return watch.compose_commitment_id;
}

export function reminderAt(
  choice: ReminderChoice,
  customLocalTime: string,
  now: number,
): string {
  let selected: number;
  if (choice === "custom") {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(
      customLocalTime,
    );
    if (!match) throw new Error("Choose a reminder date and time.");
    const [year, month, day, hour, minute] = match.slice(1).map(Number);
    const date = new Date(year, month - 1, day, hour, minute);
    // Reject invalid dates and local times skipped by a daylight-saving change.
    if (
      date.getFullYear() !== year ||
      date.getMonth() !== month - 1 ||
      date.getDate() !== day ||
      date.getHours() !== hour ||
      date.getMinutes() !== minute
    )
      throw new Error("Choose a valid local date and time.");
    selected = date.getTime();
  } else {
    selected = now + Number(choice) * dayMilliseconds;
  }
  if (!Number.isFinite(now) || !Number.isFinite(selected) || selected <= now)
    throw new Error("Choose a reminder time in the future.");
  if (selected > now + 30 * dayMilliseconds)
    throw new Error("Choose a reminder within the next 30 days.");
  return new Date(selected).toISOString();
}

export function threadWatchTime(value: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not checked yet";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function sortedThreadWatches(items: ThreadWatch[]): ThreadWatch[] {
  const statusOrder: Record<ThreadWatchStatus, number> = {
    due: 0,
    needs_access: 1,
    check_failed: 2,
    watching: 3,
    paused: 4,
    replied: 5,
    cancelled: 6,
  };
  return [...items].sort(
    (left, right) =>
      (statusOrder[left.status] ?? 7) - (statusOrder[right.status] ?? 7) ||
      Date.parse(left.remind_at) - Date.parse(right.remind_at) ||
      left.id.localeCompare(right.id),
  );
}
