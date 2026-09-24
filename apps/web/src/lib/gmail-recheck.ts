export interface RecheckItem {
  commitment_id: string;
  title: string;
  outcome: string;
  reason: string;
  preview_id: string | null;
  checked_at: string | null;
  evidence_ids?: string[];
}

export interface RecheckPage {
  items: RecheckItem[];
  next_after: string | null;
}

export const recheckReasons: Record<string, string> = {
  not_checked: "Ready to recheck",
  checking_sources: "Checking the supporting emails…",
  no_action_found:
    "No required action or important alert found. Review before removing.",
  matching_action: "The supporting email contains this saved action.",
  action_not_verified:
    "The email contains possible work, but this saved action could not be verified.",
  possible_state_change:
    "The email may describe a completion or waiting update. Review this item's status.",
  source_unavailable: "The email is unavailable. This item needs your review.",
  source_changed:
    "The email changed since this item was saved. This item needs your review.",
  item_changed:
    "This item changed during the check. Refresh results before continuing.",
  untrusted_source:
    "The source could not be safely assessed. This item needs your review.",
  unverified_evidence:
    "The saved evidence could not be verified. This item needs your review.",
  multiple_source_types:
    "This item has other supporting sources. Kept for your review.",
  too_many_sources:
    "This item has several supporting emails. Kept for your review.",
  invalid_extraction:
    "The AI response could not be verified. This item needs your review.",
  timeout: "The check timed out. You can retry this item.",
  google_access_failed: "Reconnect Gmail read access before retrying.",
  google_read_failed: "Gmail could not provide the email. Try again later.",
  ai_request_failed:
    "The AI provider could not finish the check. Try again later.",
  check_unavailable: "The check could not finish. This item needs your review.",
  user_decision_or_inactive: "Your existing choice is preserved.",
  already_current_policy: "This item already uses the current email filter.",
  owner_selected: "Your choice is saved.",
};

export function removalSelection(items: RecheckItem[], selected: Set<string>) {
  return items
    .filter(
      (item) =>
        selected.has(item.commitment_id) &&
        item.outcome === "remove_suggested" &&
        item.preview_id,
    )
    .slice(0, 25)
    .map((item) => ({
      commitment_id: item.commitment_id,
      preview_id: item.preview_id,
    }));
}

export async function runRecheckBatch({
  items,
  preview,
  onStart,
  onResult,
  shouldStop,
}: {
  items: RecheckItem[];
  preview: (item: RecheckItem) => Promise<RecheckItem>;
  onStart: (item: RecheckItem, position: number, total: number) => void;
  onResult: (item: RecheckItem) => void;
  shouldStop: () => boolean;
}) {
  const batch = items
    .filter((item) => item.outcome === "unchecked")
    .slice(0, 10);
  for (const [index, item] of batch.entries()) {
    if (shouldStop()) break;
    onStart(item, index + 1, batch.length);
    const result = await preview(item);
    onResult(result);
    // Do not multiply provider failures or compete with another browser's check.
    if (result.outcome === "failed" || result.outcome === "checking") break;
  }
}

export async function recheckRequest<T>(
  path: string,
  signal: AbortSignal,
  body?: unknown,
): Promise<T> {
  const base =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
  const response = await fetch(`${base}/intelligence/gmail-recheck${path}`, {
    credentials: "include",
    cache: "no-store",
    signal,
    ...(body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      typeof payload?.detail === "string"
        ? payload.detail
        : "The recheck could not finish. Refresh results and try again.",
    );
  }
  return payload as T;
}
