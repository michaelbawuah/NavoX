"use client";

import type {
  RecurringSubscription,
  SubscriptionQuery,
  SubscriptionQueryResult,
} from "@navox/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  subscriptionDate,
  subscriptionMoney,
  subscriptionRequest,
} from "../lib/subscriptions";
import { NavoXAskBox } from "./navox-ui";
import styles from "./subscriptions-dashboard.module.css";

export function subscriptionQueryFromQuestion(
  value: string,
): SubscriptionQuery {
  const text = value.trim().slice(0, 200);
  const normalized = text.toLowerCase();
  let intent: SubscriptionQuery["intent"] = "SEARCH";
  if (/\b(trial|trials)\b/.test(normalized)) intent = "TRIALS";
  else if (/\b(upcoming|renew|renews|renewal|renewals|next)\b/.test(normalized))
    intent = "UPCOMING";
  else if (
    /\b(cost|costs|spend|spending|total|summary|monthly|yearly)\b/.test(
      normalized,
    )
  )
    intent = "SUMMARY";
  const listAll =
    /\b(?:all subscriptions|subscriptions do i have|my subscriptions)\b/.test(
      normalized,
    );
  const searchText =
    intent === "SEARCH" && !listAll
      ? text
          .replace(
            /^(?:what about|show me|find|tell me about|do i have|is there)\s+/i,
            "",
          )
          .replace(/\?+$/, "")
          .trim()
      : "";
  const duration = normalized.match(/\b(\d+)\s*(days?|weeks?)\b/);
  const days = duration
    ? Number(duration[1]) * (duration[2].startsWith("week") ? 7 : 1)
    : /\b(tomorrow|next day)\b/.test(normalized)
      ? 1
      : /\bnext week\b/.test(normalized)
        ? 7
        : /\bnext year\b/.test(normalized)
          ? 365
          : 30;
  if (!Number.isSafeInteger(days) || days < 1 || days > 366)
    throw new Error("Choose a renewal window from 1 to 366 days.");
  return { intent, text: searchText, currency: null, days };
}
function resultHeading(intent: SubscriptionQueryResult["intent"]): string {
  const headings: Record<SubscriptionQueryResult["intent"], string> = {
    SUMMARY: "Known recurring cost",
    UPCOMING: "Upcoming renewals",
    TRIALS: "Trials",
    SEARCH: "Matching subscriptions",
  };
  return headings[intent];
}

export function SubscriptionQueryResultView({
  result,
  onOpen,
  days = 30,
}: {
  result: SubscriptionQueryResult;
  days?: number;
  onOpen: (item: RecurringSubscription) => void;
}) {
  if (result.summary) {
    return (
      <div className={styles.askAnswer} aria-live="polite">
        <h3>{resultHeading(result.intent)}</h3>
        {result.summary.currency_totals.length ? (
          <ul className={styles.askList}>
            {result.summary.currency_totals.map((total) => (
              <li key={total.currency}>
                <strong>
                  ~{subscriptionMoney(total.monthly_equivalent, total.currency)}
                  {" / month"}
                </strong>
                <span>
                  ~{subscriptionMoney(total.yearly_equivalent, total.currency)}
                  {" / year"} · {total.obligation_count} known
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p>No known prices are available yet.</p>
        )}
        <p className={styles.muted}>
          {result.summary.unknown_cost_count} subscription
          {result.summary.unknown_cost_count === 1 ? "" : "s"} with unknown
          cost. Currencies stay separate.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.askAnswer} aria-live="polite">
      <h3>{resultHeading(result.intent)}</h3>
      {result.intent === "UPCOMING" && <p>Renewals in the next {days} days.</p>}
      {result.subscriptions.length > 6 && (
        <p>
          Showing 6 of {result.subscriptions.length} matches. Search by name to
          narrow the list.
        </p>
      )}
      {result.subscriptions.length ? (
        <ol className={styles.askList}>
          {result.subscriptions.slice(0, 6).map((item) => (
            <li key={item.id}>
              <div>
                <strong>{item.name}</strong>
                <span>
                  {subscriptionMoney(
                    item.billing_amount,
                    item.billing_currency,
                  )}
                  {item.trial_ends_at
                    ? ` · Trial ends ${subscriptionDate(item.trial_ends_at)}`
                    : item.next_renewal_at
                      ? ` · Renews ${subscriptionDate(item.next_renewal_at)}`
                      : " · Renewal date unknown"}
                </span>
              </div>
              <button type="button" onClick={() => onOpen(item)}>
                Open details
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p>No matching subscriptions are in your current registry.</p>
      )}
    </div>
  );
}

export function SubscriptionsAsk({
  apiBase,
  onOpen,
}: {
  apiBase: string;
  onOpen: (item: RecurringSubscription) => void;
}) {
  const [question, setQuestion] = useState("What renews in the next 30 days?");
  const [result, setResult] = useState<SubscriptionQueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [days, setDays] = useState(30);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim() || busy || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const command = subscriptionQueryFromQuestion(question);
      setDays(command.days);
      const answer = await subscriptionRequest<SubscriptionQueryResult>(
        apiBase,
        "/subscriptions/query",
        command,
        "POST",
        controller.signal,
      );
      if (!controller.signal.aborted) setResult(answer);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof Error
            ? cause.message
            : "NavoX could not answer that subscription question.",
        );
    } finally {
      if (pending.current === controller) pending.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <section
      className={styles.askPanel}
      aria-labelledby="subscriptions-ask-heading"
    >
      <div className={styles.sectionHeading}>
        <div>
          <p className={styles.eyebrow}>Ask NavoX</p>
          <h2 id="subscriptions-ask-heading">Ask about your subscriptions</h2>
        </div>
        <span className={styles.coverage}>Read-only</span>
      </div>
      <p className={styles.muted}>
        Ask about known costs, upcoming renewals, trials, or a subscription by
        name. Answers use only your current subscription registry.
      </p>
      <NavoXAskBox
        id="subscription-question"
        label="Ask NavoX about your subscriptions"
        value={question}
        placeholder="What renews in the next 30 days?"
        busy={busy}
        onChange={setQuestion}
        onSubmit={(event) => void ask(event)}
        hint="Questions only read your current registry. Changes remain separate."
      />
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {result && (
        <SubscriptionQueryResultView
          result={result}
          onOpen={onOpen}
          days={days}
        />
      )}
    </section>
  );
}
