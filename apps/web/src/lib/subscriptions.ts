import type {
  CancellationAttempt,
  RecurringSubscription,
  SubscriptionInput,
  SubscriptionInterval,
} from "@navox/contracts";

export type SubscriptionFilter = "All" | "Upcoming" | "Active" | "Cancelled";

export function subscriptionCorrections(
  item: RecurringSubscription,
  input: SubscriptionInput,
): Partial<SubscriptionInput> {
  return Object.fromEntries(
    Object.entries(input).filter(([field, value]) => {
      const previous = item[field as keyof RecurringSubscription];
      return value !== previous;
    }),
  );
}

export function subscriptionDate(value: string | null | undefined): string {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Unknown";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
    date,
  );
}

export function subscriptionMoney(
  amount: string | null | undefined,
  currency: string | null | undefined,
): string {
  if (amount == null || !currency || !/^\d+(\.\d+)?$/.test(amount)) {
    return "Unknown cost";
  }
  try {
    return `${new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
    }).format(amount as unknown as number)} ${currency}`;
  } catch {
    return `${amount} ${currency}`;
  }
}

export function intervalLabel(interval: SubscriptionInterval | null): string {
  const labels: Record<SubscriptionInterval, string> = {
    DAY: "day",
    WEEK: "week",
    MONTH: "month",
    QUARTER: "quarter",
    YEAR: "year",
    UNKNOWN: "unknown interval",
  };
  return interval
    ? (labels[interval] ?? "unknown interval")
    : "unknown interval";
}

export function subscriptionInFilter(
  item: RecurringSubscription,
  filter: SubscriptionFilter,
  now: number = Date.now(),
): boolean {
  if (item.review_state === "NOT_MINE") return false;
  if (filter === "All") return true;
  if (filter === "Cancelled")
    return ["CANCELLED", "EXPIRED"].includes(item.status);
  if (filter === "Active")
    return [
      "ACTIVE",
      "TRIAL",
      "PAUSED",
      "CANCELLATION_REQUESTED",
      "CANCEL_PENDING",
    ].includes(item.status);
  const next = item.trial_ends_at ?? item.next_renewal_at;
  return (
    !!next &&
    !["CANCELLED", "EXPIRED"].includes(item.status) &&
    new Date(next).getTime() <= now + 30 * 86_400_000
  );
}

export function subscriptionAttention(
  item: RecurringSubscription,
  now = Date.now(),
): string[] {
  if (item.review_state === "NOT_MINE") return [];
  if (item.review_after && new Date(item.review_after).getTime() > now)
    return [];
  const labels: Record<string, string> = {
    "obligation.discovered": "Confirm discovery",
    "obligation.renewal_approaching": "Renewal approaching",
    "obligation.price_changed": "Price changed",
    "trial.ending": "Trial ending",
    "trial.ended": "Check trial outcome",
    "cancellation.verification_pending": "Cancellation not verified",
    "cancellation.failed": "Cancellation needs review",
    "cancellation.contradicted": "Conflicting cancellation evidence",
  };
  if (item.attention_reasons?.length)
    return item.attention_reasons.map(
      (reason) => labels[reason] ?? "Review subscription",
    );
  const reasons: string[] = [];
  if (item.status === "CANDIDATE" || item.status === "UNKNOWN")
    reasons.push("Confirm discovery");
  if (["CANCELLATION_REQUESTED", "CANCEL_PENDING"].includes(item.status))
    reasons.push("Cancellation not verified");
  if (
    item.review_state !== "KEPT" &&
    !["CANCELLED", "EXPIRED"].includes(item.status)
  ) {
    if (
      item.trial_ends_at &&
      new Date(item.trial_ends_at).getTime() <= now + 7 * 86_400_000
    )
      reasons.push("Trial ending");
    else if (
      item.next_renewal_at &&
      new Date(item.next_renewal_at).getTime() <= now + 7 * 86_400_000
    )
      reasons.push("Renewal approaching");
  }
  return reasons;
}

export function cancellationLabel(attempt: CancellationAttempt): string {
  if (
    attempt.status === "VERIFIED_CANCELLED" &&
    attempt.verification_status === "VERIFIED_CANCELLED"
  )
    return "Cancellation independently verified";
  if (attempt.verification_status === "CONTRADICTED")
    return "New evidence contradicts cancellation";
  if (attempt.status === "AWAITING_CONFIRMATION")
    return "Review your exact cancellation";
  if (attempt.status === "AWAITING_USER")
    return "Your action is needed at the provider";
  if (
    ["SUBMITTED", "VERIFICATION_PENDING", "IN_PROGRESS"].includes(
      attempt.status,
    )
  )
    return "Cancellation is not yet verified";
  if (attempt.status === "FAILED") return "Cancellation could not be completed";
  if (attempt.status === "ABORTED") return "Cancellation request withdrawn";
  return "Preparing cancellation review";
}

export function canConfirmCancellation(
  attempt: CancellationAttempt,
  accepted: boolean,
  now = Date.now(),
): boolean {
  return (
    accepted &&
    attempt.status === "AWAITING_CONFIRMATION" &&
    !!attempt.payload_hash &&
    ["PROVIDER_API", "CONNECTED_PROVIDER_ACTION", "BROWSER_ASSISTED"].includes(
      attempt.method,
    ) &&
    !!attempt.expires_at &&
    new Date(attempt.expires_at).getTime() > now
  );
}

export function safeManagementUrl(
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export async function subscriptionRequest<T>(
  apiBase: string,
  path: string,
  body?: object,
  method = "POST",
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...(signal ? { signal } : {}),
    credentials: "include",
    cache: "no-store",
    ...(body === undefined
      ? {}
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  if (!response.ok) {
    if (response.status === 401)
      throw new Error(
        "Your session ended. Sign in again to open subscriptions.",
      );
    if (response.status === 409)
      throw new Error(
        "This subscription or approval changed. Refresh and review the current details before trying again.",
      );
    if (response.status === 403)
      throw new Error(
        "This action is not permitted for your account or connected provider.",
      );
    if (response.status === 422)
      throw new Error(
        "Check the amount, currency, dates and required fields, then try again.",
      );
    throw new Error("NavoX could not complete this request. Please try again.");
  }
  return (await response.json()) as T;
}
