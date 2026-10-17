"use client";

import Link from "next/link";
import {
  canPrepareDailyReply,
  type DailyFocusItem,
  type DailyFocusPayload,
  dailyFocusAction,
  dailyFocusCompletedCount,
  dailyFocusDate,
  dailyFocusFreshness,
  dailyFocusSourceContext,
  selectDailyFocus,
} from "../lib/daily-focus";
import styles from "./daily-focus.module.css";
import { SourceReferences } from "./source-references";

interface DailyFocusProps<T extends DailyFocusItem> {
  payload: DailyFocusPayload<T> | null;
  loading: boolean;
  error?: string;
  now: number;
  paused: boolean;
  busyItemId?: string | null;
  onPrepare: (item: T) => void | Promise<void>;
  onReview: (item: T) => void;
  onRefresh: () => void | Promise<void>;
  canDraftReply?: (item: T) => boolean;
}

/** A small, actionable view of saved Today facts. It does not generate a plan. */
export function DailyFocus<T extends DailyFocusItem>({
  payload,
  loading,
  error = "",
  now,
  paused,
  busyItemId = null,
  onPrepare,
  onReview,
  onRefresh,
  canDraftReply = canPrepareDailyReply,
}: DailyFocusProps<T>) {
  const items = selectDailyFocus(payload);
  const completed = dailyFocusCompletedCount(payload);
  const freshness = payload
    ? dailyFocusFreshness(payload.generated_at, now)
    : "unknown";
  const updated = payload
    ? dailyFocusDate(payload.generated_at, payload.timezone)
    : null;
  const blocked = loading || !!error || freshness !== "current";
  const statusCopy = error
    ? payload
      ? "Your priorities could not be refreshed. These are the last saved items."
      : "Your priorities could not be loaded. Try again."
    : freshness === "stale"
      ? "This briefing is more than 15 minutes old. Refresh before preparing your next step."
      : payload && freshness === "unknown"
        ? "The update time could not be verified. Refresh before preparing your next step."
        : "";

  return (
    <section
      className={styles.focus}
      aria-labelledby="daily-focus-heading"
      aria-busy={loading}
    >
      <div className={styles.heading}>
        <div>
          <p className={styles.kicker}>Your daily focus</p>
          <h2 id="daily-focus-heading">
            {payload && items.length > 0
              ? `${items.length} ${items.length === 1 ? "next step" : "next steps"}, ready to review`
              : "A clear place to start"}
          </h2>
          <p className={styles.intro}>
            Choose one thing to move forward. You stay in control of every
            action.
          </p>
        </div>
        <button
          className={styles.refresh}
          type="button"
          disabled={loading || busyItemId !== null}
          onClick={() => void onRefresh()}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {statusCopy && (
        <p className={styles.notice} role={error ? "alert" : "status"}>
          {statusCopy}
        </p>
      )}
      {paused && payload && items.length > 0 && (
        <p className={styles.notice} role="status">
          Assistance is paused. You can review your items; resume assistance to
          prepare a draft or plan.
        </p>
      )}

      {loading && !payload ? (
        <p className={styles.empty} role="status">
          Finding your saved priorities…
        </p>
      ) : !payload ? (
        !error && (
          <p className={styles.empty} role="status">
            Your priorities have not loaded yet.
          </p>
        )
      ) : items.length === 0 ? (
        <div className={styles.empty}>
          <h3>
            {blocked
              ? "No priorities to display"
              : "Nothing needs a next step here"}
          </h3>
          <p>
            {blocked
              ? "Refresh to check your latest saved items."
              : "No active priorities were found in this update. Review your planner or add something you want to get done."}
          </p>
          <Link href="/planner">Open planner</Link>
        </div>
      ) : (
        <ol className={styles.items}>
          {items.map((item, index) => {
            const action = dailyFocusAction(item, canDraftReply);
            const source = dailyFocusSourceContext(item);
            const due = dailyFocusDate(item.due_at, payload.timezone);
            const preparing = busyItemId === item.id;
            const prepareDisabled = blocked || paused || busyItemId !== null;
            const label = {
              review: "Review suggestion",
              renewal: "Review renewal",
              draft: "Draft reply",
              plan: "Plan next step",
            }[action];
            return (
              <li className={styles.item} key={item.id}>
                <span className={styles.number} aria-hidden="true">
                  {index + 1}
                </span>
                <div className={styles.itemBody}>
                  <p className={styles.meta}>
                    {source.label}
                    {item.status === "candidate" && " · Suggested"}
                  </p>
                  <h3>{item.title}</h3>
                  {due && (
                    <p className={styles.due}>
                      {item.type === "meeting" ? "Starts" : "Due"} {due}
                    </p>
                  )}
                  {item.description && (
                    <p className={styles.description}>{item.description}</p>
                  )}
                  {source.count > 0 && (
                    <details className={styles.sources}>
                      <summary>
                        View {source.count}{" "}
                        {source.count === 1 ? "source" : "sources"}
                      </summary>
                      <SourceReferences
                        sources={item.sources}
                        paused={paused}
                        formatDate={(value) =>
                          dailyFocusDate(value, payload.timezone) ??
                          "Date unavailable"
                        }
                      />
                    </details>
                  )}
                </div>
                {action === "renewal" ? (
                  <Link className={styles.primary} href="/subscriptions">
                    {label}
                  </Link>
                ) : (
                  <button
                    className={styles.primary}
                    type="button"
                    disabled={
                      action === "review"
                        ? loading || busyItemId !== null
                        : prepareDisabled
                    }
                    onClick={() => {
                      if (action === "review") onReview(item);
                      else void onPrepare(item);
                    }}
                    aria-label={`${label}: ${item.title}`}
                  >
                    {preparing ? "Preparing…" : label}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {payload && (
        <div className={styles.footer}>
          <p>
            {updated ? (
              <>
                Updated <time dateTime={payload.generated_at}>{updated}</time>
              </>
            ) : (
              "Update time unavailable"
            )}
          </p>
          {completed > 0 && (
            <Link href="/planner">
              {completed} {completed === 1 ? "item" : "items"} marked complete
              recently
            </Link>
          )}
        </div>
      )}
    </section>
  );
}
