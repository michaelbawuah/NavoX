import type {
  KnowledgeResourceType,
  RecentSearch,
  ResourceDetail,
  SearchExclusion,
  SearchQueryBody,
  SearchResponse,
} from "@navox/contracts";

export class SearchRequestError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function messageFor(status: number): string {
  if (status === 401) return "Sign in to search your connected sources.";
  if (status === 403)
    return "This account can’t use connected search right now.";
  if (status === 404)
    return "Connected search isn’t enabled for this workspace.";
  if (status === 422)
    return "That search request wasn’t valid. Check the query and filters.";
  if (status === 429)
    return "Too many searches just now. Please try again shortly.";
  return "We couldn’t run that search. Please try again.";
}

export async function searchRequest<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const base =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
  const response = await fetch(`${base}${path}`, {
    ...options,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    throw new SearchRequestError(response.status, messageFor(response.status));
  }
  return response.json() as Promise<T>;
}

export async function runSearch(
  body: SearchQueryBody,
  signal?: AbortSignal,
  remember = true,
): Promise<SearchResponse> {
  return searchRequest<SearchResponse>(
    remember ? "/search/query" : "/search/query?remember=false",
    {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    },
  );
}

export async function runLiveSearch(
  body: SearchQueryBody,
  requestId: string,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return searchRequest<SearchResponse>("/search/live", {
    method: "POST",
    body: JSON.stringify({ request_id: requestId, search: body }),
    signal,
  });
}

export function needsFreshSearch(query: string): boolean {
  return /\b(latest|newest|current|today|now|recent|live)\b/i.test(query);
}

export function waitForSearchRefresh(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Search changed", "AbortError"));
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Search changed", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 2000);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function loadRecentSearches(
  signal?: AbortSignal,
): Promise<RecentSearch[]> {
  return searchRequest<RecentSearch[]>("/search/recent", { signal });
}

export async function clearRecentSearches(): Promise<{ cleared: number }> {
  return searchRequest<{ cleared: number }>("/search/recent", {
    method: "DELETE",
  });
}

export async function loadExclusions(
  signal?: AbortSignal,
): Promise<SearchExclusion[]> {
  return searchRequest<SearchExclusion[]>("/search/exclusions", { signal });
}

export async function addExclusion(payload: {
  scope: string;
  source_connection_id?: string | null;
  resource_id?: string | null;
  resource_type?: string | null;
  external_id?: string | null;
}): Promise<SearchExclusion> {
  return searchRequest<SearchExclusion>("/search/exclusions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function removeExclusion(
  exclusionId: string,
): Promise<{ removed: boolean }> {
  return searchRequest<{ removed: boolean }>(
    `/search/exclusions/${exclusionId}`,
    { method: "DELETE" },
  );
}

export async function loadResourceDetail(
  resourceId: string,
  signal?: AbortSignal,
): Promise<ResourceDetail> {
  return searchRequest<ResourceDetail>(`/knowledge/resources/${resourceId}`, {
    signal,
  });
}

export const resourceTypeLabels: Record<KnowledgeResourceType, string> = {
  EMAIL: "Email",
  EMAIL_THREAD: "Email thread",
  CALENDAR_EVENT: "Calendar",
  DOCUMENT: "Document",
  FILE: "File",
  CANVAS_ASSIGNMENT: "Assignment",
  CANVAS_ANNOUNCEMENT: "Announcement",
  MEETING: "Meeting",
  COMMITMENT: "Commitment",
  SUBSCRIPTION: "Subscription",
  NEWS_STORY: "News",
  TASK: "Task",
  OTHER: "Other",
};

export function resourceTypeLabel(
  value: KnowledgeResourceType | string,
): string {
  return resourceTypeLabels[value as KnowledgeResourceType] ?? "Other";
}

export function safeSourceUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function searchTime(value: string | null): string {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Time unavailable"
    : date.toLocaleString(undefined, {
        year: "numeric",
        timeZoneName: "short",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}

export function answerNotice(response: SearchResponse): string | null {
  if (response.answer_state === "UNAVAILABLE") {
    return "NavoX can’t write an answer yet. These are the sources it can show you.";
  }
  if (response.answer_state === "INCOMPLETE") {
    return "This answer would be incomplete with the sources available now.";
  }
  return null;
}

export function coverageNotes(response: SearchResponse): string[] {
  const notes: string[] = [];
  if (response.refresh?.queued) {
    notes.push(
      "Refreshing relevant sources. These results remain cached until syncing finishes.",
    );
  }
  if (response.refresh?.unavailable || response.refresh?.bounded) {
    notes.push(
      "Some sources could not be refreshed. Results may be incomplete.",
    );
  }
  for (const source of response.coverage.source_issues ?? []) {
    notes.push(
      `${source.source_label} could not refresh (${source.state.toLowerCase().replaceAll("_", " ")}). Its cached results may be incomplete.`,
    );
  }
  if (response.coverage.partial_reasons.includes("SOURCE_STALE")) {
    notes.push(
      "Some sources are past their freshness window. Refresh them before relying on current details.",
    );
  }
  const notSearchable = response.coverage.not_searchable ?? 0;
  if (notSearchable > 0) {
    notes.push(
      `${notSearchable} matching item${notSearchable === 1 ? "" : "s"} can’t be searched because the source didn’t keep its content.`,
    );
  }
  if (response.coverage.stale_dropped > 0) {
    notes.push(
      `${response.coverage.stale_dropped} item${response.coverage.stale_dropped === 1 ? "" : "s"} changed at the source and ${response.coverage.stale_dropped === 1 ? "was" : "were"} left out.`,
    );
  }
  if (response.coverage.truncated) {
    notes.push(
      "This search looked at a limited slice of your sources, so there may be more matches.",
    );
  }
  if (response.coverage.partial_reasons.includes("SEMANTIC_UNAVAILABLE")) {
    notes.push("Meaning-based search isn’t available yet.");
  }
  if (
    response.coverage.partial_reasons.includes(
      "CONNECTED_SOURCE_FILTER_EXCLUDES_NATIVE_DOMAINS",
    )
  ) {
    notes.push(
      "Filtering to specific connected sources leaves out NavoX records such as commitments and subscriptions.",
    );
  }
  return notes;
}

export function resultKey(resourceId: string, sourceType: string): string {
  return `${sourceType}:${resourceId}`;
}

/**
 * Monotonic guard for in-flight work. Only the newest token may publish state,
 * so a superseded response can never replace a newer query, and invalidating an
 * action (hiding a source, clearing history) makes any pending result inert.
 */
export class RequestGate {
  private counter = 0;

  next(): number {
    this.counter += 1;
    return this.counter;
  }

  isCurrent(token: number): boolean {
    return token === this.counter;
  }

  invalidate(): void {
    this.counter += 1;
  }
}

/** Start of the chosen calendar day, in UTC. */
export function startOfDay(value: string): string | null {
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.valueOf()) ? null : parsed.toISOString();
}

/**
 * The exclusive end of the chosen day: the next midnight, so the last second of
 * the selected day is never dropped by a `23:59:59` sentinel.
 */
export function exclusiveEndOfDay(value: string): string | null {
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return null;
  parsed.setUTCDate(parsed.getUTCDate() + 1);
  return parsed.toISOString();
}
