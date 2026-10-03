"use client";

import type {
  CancellationAttempt,
  PreventedRenewals,
  RecurringSubscription,
  SubscriptionCancellationProfile,
  SubscriptionEvidence,
  SubscriptionInput,
  SubscriptionPrice,
  SubscriptionSummary,
} from "@navox/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  intervalLabel,
  type SubscriptionFilter,
  subscriptionAttention,
  subscriptionCorrections,
  subscriptionDate,
  subscriptionInFilter,
  subscriptionMoney,
  subscriptionRequest,
} from "../lib/subscriptions";
import { CancellationReview } from "./cancellation-review";
import { StripeSandboxConnect } from "./stripe-sandbox-connect";
import { SubscriptionDetails } from "./subscription-details";
import { SubscriptionEditor } from "./subscription-editor";
import { SubscriptionsAsk } from "./subscriptions-ask";
import styles from "./subscriptions-dashboard.module.css";

const apiBase =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const filters: SubscriptionFilter[] = [
  "All",
  "Upcoming",
  "Active",
  "Cancelled",
];
const pageSize = 12;

type Detail = {
  item: RecurringSubscription;
  evidence: SubscriptionEvidence[];
  history: SubscriptionPrice[];
  profiles: SubscriptionCancellationProfile[];
  loading: boolean;
  error: string;
};

function failure(cause: unknown): string {
  return cause instanceof Error
    ? cause.message
    : "NavoX could not complete that request.";
}

export function SubscriptionsDashboard() {
  const [items, setItems] = useState<RecurringSubscription[]>([]);
  const [summary, setSummary] = useState<SubscriptionSummary | null>(null);
  const [prevented, setPrevented] = useState<PreventedRenewals | null>(null);
  const [loading, setLoading] = useState(true);
  const [queryVersion, setQueryVersion] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState<SubscriptionFilter>("All");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [editor, setEditor] = useState<{
    item: RecurringSubscription | null;
    requestId: string;
  } | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [attempt, setAttempt] = useState<CancellationAttempt | null>(null);
  const [attemptError, setAttemptError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [stripeEnabled, setStripeEnabled] = useState(false);
  const [stripeOpen, setStripeOpen] = useState(false);
  const mutating = useRef(false);
  const requestNumber = useRef(0);
  const detailNumber = useRef(0);
  const requestKeys = useRef(new Map<string, string>());

  const load = useCallback(async () => {
    const sequence = ++requestNumber.current;
    setLoading(true);
    setQueryVersion((version) => version + 1);
    setError("");
    try {
      const [records, totals, preventedResult] = await Promise.all([
        subscriptionRequest<RecurringSubscription[]>(apiBase, "/subscriptions"),
        subscriptionRequest<SubscriptionSummary>(
          apiBase,
          "/subscriptions/summary",
        ),
        subscriptionRequest<PreventedRenewals>(
          apiBase,
          "/subscriptions/prevented-renewals",
        ),
      ]);
      if (sequence !== requestNumber.current) return;
      setItems(records);
      setSummary(totals);
      setPrevented(preventedResult);
    } catch (cause) {
      if (sequence === requestNumber.current) setError(failure(cause));
    } finally {
      if (sequence === requestNumber.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    let active = true;
    void subscriptionRequest<{ enabled: boolean }>(
      apiBase,
      "/subscriptions/stripe-sandbox",
    )
      .then((value) => {
        if (active) setStripeEnabled(value.enabled);
      })
      .catch(() => {});
    return () => {
      active = false;
      requestNumber.current++;
      detailNumber.current++;
    };
  }, [load]);

  const displayed = useMemo(
    () =>
      items.filter(
        (item) =>
          subscriptionInFilter(item, filter) &&
          `${item.name} ${item.plan_name ?? ""}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      ),
    [items, filter, search],
  );
  const attention = useMemo(
    () =>
      items.flatMap((item) => {
        const reasons = subscriptionAttention(item);
        return reasons.length ? [{ item, reasons }] : [];
      }),
    [items],
  );
  const pageCount = Math.max(1, Math.ceil(displayed.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const visible = displayed.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize,
  );

  function requestId(key: string) {
    let value = requestKeys.current.get(key);
    if (!value) {
      value = crypto.randomUUID();
      requestKeys.current.set(key, value);
    }
    return value;
  }

  async function review(
    item: RecurringSubscription,
    decision: "KEEP" | "CONFIRM" | "SNOOZE" | "NOT_MINE",
  ) {
    if (mutating.current) return;
    mutating.current = true;
    setPendingId(item.id);
    setError("");
    try {
      await subscriptionRequest(
        apiBase,
        `/subscriptions/${item.id}/${decision === "KEEP" ? "keep" : "review"}`,
        {
          request_id: requestId(`review:${item.id}:${decision}`),
          ...(decision !== "KEEP" ? { decision } : {}),
          ...(decision === "SNOOZE"
            ? {
                review_after: new Date(
                  Date.now() + 7 * 86_400_000,
                ).toISOString(),
              }
            : {}),
        },
      );
      requestKeys.current.delete(`review:${item.id}:${decision}`);
      const messages = {
        KEEP: "Kept for this cycle. Material changes can still ask for your attention.",
        CONFIRM: "Subscription confirmed.",
        SNOOZE: "Review deferred for seven days.",
        NOT_MINE:
          "Set aside as not your subscription. Its evidence history is preserved.",
      };
      setNotice(messages[decision]);
      await load();
    } catch (cause) {
      setError(failure(cause));
    } finally {
      mutating.current = false;
      setPendingId(null);
    }
  }

  async function openDetails(item: RecurringSubscription) {
    const sequence = ++detailNumber.current;
    setDetail({
      item,
      evidence: [],
      history: [],
      profiles: [],
      loading: true,
      error: "",
    });
    try {
      const [record, evidence, history, profiles] = await Promise.all([
        subscriptionRequest<RecurringSubscription>(
          apiBase,
          `/subscriptions/${item.id}`,
        ),
        subscriptionRequest<SubscriptionEvidence[]>(
          apiBase,
          `/subscriptions/${item.id}/evidence`,
        ),
        subscriptionRequest<SubscriptionPrice[]>(
          apiBase,
          `/subscriptions/${item.id}/history`,
        ),
        subscriptionRequest<SubscriptionCancellationProfile[]>(
          apiBase,
          "/subscriptions/cancellation-profiles",
        ),
      ]);
      if (sequence === detailNumber.current)
        setDetail({
          item: record,
          evidence,
          history,
          profiles,
          loading: false,
          error: "",
        });
    } catch (cause) {
      if (sequence === detailNumber.current)
        setDetail((current) =>
          current
            ? { ...current, loading: false, error: failure(cause) }
            : null,
        );
    }
  }

  async function save(input: SubscriptionInput) {
    if (!editor) return;
    await subscriptionRequest(
      apiBase,
      editor.item ? `/subscriptions/${editor.item.id}` : "/subscriptions",
      {
        ...(editor.item ? subscriptionCorrections(editor.item, input) : input),
        ...(!editor.item ? { request_id: editor.requestId } : {}),
      },
      editor.item ? "PATCH" : "POST",
    );
    setEditor(null);
    setNotice(
      editor.item
        ? "Correction saved with its provenance."
        : "Subscription added.",
    );
    await load();
  }

  async function startCancellation(item: RecurringSubscription) {
    if (mutating.current) return;
    mutating.current = true;
    setBusy(true);
    setPendingId(item.id);
    setError("");
    setAttemptError("");
    try {
      const existing = [
        "CANCELLATION_REQUESTED",
        "CANCEL_PENDING",
        "CANCELLED",
        "EXPIRED",
      ].includes(item.status);
      const next = await subscriptionRequest<CancellationAttempt>(
        apiBase,
        `/subscriptions/${item.id}/${existing ? "cancellation" : "cancel"}`,
        existing ? undefined : { request_id: requestId(`prepare:${item.id}`) },
      );
      setDetail(null);
      detailNumber.current++;
      setAttempt(next);
      if (!existing) requestKeys.current.delete(`prepare:${item.id}`);
      await load();
    } catch (cause) {
      setError(failure(cause));
      setDetail((current) =>
        current ? { ...current, error: failure(cause) } : null,
      );
    } finally {
      mutating.current = false;
      setBusy(false);
      setPendingId(null);
    }
  }

  const refreshAttempt = useCallback(
    async (id: string) => {
      try {
        const next = await subscriptionRequest<CancellationAttempt>(
          apiBase,
          `/cancellations/${id}`,
        );
        setAttempt((current) => (current?.id === id ? next : current));
        setAttemptError("");
        if (["VERIFIED_CANCELLED", "FAILED", "ABORTED"].includes(next.status))
          await load();
      } catch (cause) {
        setAttemptError(failure(cause));
      }
    },
    [load],
  );

  useEffect(() => {
    if (
      !attempt ||
      ![
        "REQUESTED",
        "IN_PROGRESS",
        "SUBMITTED",
        "VERIFICATION_PENDING",
      ].includes(attempt.status)
    )
      return;
    const id = attempt.id;
    const timer = window.setInterval(() => {
      if (!mutating.current) void refreshAttempt(id);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [attempt?.id, attempt?.status, attempt, refreshAttempt]);

  async function mutateAttempt(action: "confirm" | "abort") {
    if (!attempt || mutating.current) return;
    mutating.current = true;
    setBusy(true);
    setAttemptError("");
    try {
      const next = await subscriptionRequest<CancellationAttempt>(
        apiBase,
        `/cancellations/${attempt.id}/${action}`,
        action === "confirm"
          ? {
              request_id: requestId(
                `confirm:${attempt.id}:${attempt.payload_hash}`,
              ),
              preview_hash: attempt.payload_hash,
            }
          : {},
      );
      setAttempt(next);
      await load();
    } catch (cause) {
      setAttemptError(failure(cause));
      // Any uncertain result must be reconciled before another exact approval.
      try {
        const current = await subscriptionRequest<CancellationAttempt>(
          apiBase,
          `/cancellations/${attempt.id}`,
        );
        setAttempt(current);
      } catch {
        /* Preserve the same request identity for a safe server-checked retry. */
      }
    } finally {
      mutating.current = false;
      setBusy(false);
    }
  }

  async function bindProvider(
    profile: SubscriptionCancellationProfile,
    connectionId: string,
    resourceId: string,
  ) {
    if (!detail || mutating.current) return;
    mutating.current = true;
    setBusy(true);
    try {
      await subscriptionRequest(
        apiBase,
        `/connectors/generic-rest-api/connections/${connectionId}/cancellation-grant`,
        {
          profile_id: profile.id,
          confirmed: true,
          request_id: requestId(`grant:${profile.id}:${connectionId}`),
        },
      );
      const item = await subscriptionRequest<RecurringSubscription>(
        apiBase,
        `/subscriptions/${detail.item.id}/cancellation-binding`,
        {
          profile_id: profile.id,
          connection_id: connectionId,
          external_resource_id: resourceId,
          request_id: requestId(
            `binding:${detail.item.id}:${profile.id}:${connectionId}:${resourceId}`,
          ),
          confirmed: true,
        },
      );
      setDetail((current) =>
        current ? { ...current, item, error: "" } : null,
      );
      setNotice(
        "Provider permission enabled and exact subscription linked. Cancellation still requires your separate approval.",
      );
      await load();
    } catch (cause) {
      setDetail((current) =>
        current ? { ...current, error: failure(cause) } : null,
      );
    } finally {
      mutating.current = false;
      setBusy(false);
    }
  }

  return (
    <div className={styles.dashboard}>
      <header className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>Your subscriptions</p>
          <h1>
            Subscriptions.
            <br />
            <span>Know what comes next.</span>
          </h1>
          <p className={styles.heroCopy}>
            A clear record of recurring costs, upcoming renewals and the
            decisions that stay yours.
          </p>
        </div>
        <button
          type="button"
          className={styles.primaryButton}
          onClick={() =>
            setEditor({ item: null, requestId: crypto.randomUUID() })
          }
        >
          + Add subscription
        </button>
      </header>
      {stripeEnabled && (
        <div className={styles.actions}>
          <button type="button" onClick={() => setStripeOpen(true)}>
            Connect Stripe sandbox
          </button>
        </div>
      )}
      {error && (
        <div className={styles.error} role="alert">
          {error}{" "}
          <button type="button" disabled={loading} onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      <SubscriptionsAsk
        key={queryVersion}
        apiBase={apiBase}
        onOpen={(item) => void openDetails(item)}
      />
      <section
        className={styles.costPanel}
        aria-labelledby="known-cost-heading"
      >
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>Your recurring picture</p>
            <h2 id="known-cost-heading">Known recurring cost</h2>
          </div>
          <span className={styles.coverage}>Coverage incomplete</span>
        </div>
        {loading && !summary ? (
          <p role="status" className={styles.muted}>
            Loading known costs…
          </p>
        ) : summary && summary.currency_totals.length > 0 ? (
          <div className={styles.currencies}>
            {summary.currency_totals.map((total) => (
              <article key={total.currency}>
                <span className={styles.currencyCode}>{total.currency}</span>
                <p className={styles.total}>
                  ~{subscriptionMoney(total.monthly_equivalent, total.currency)}
                  <small>/ month equivalent</small>
                </p>
                <p className={styles.yearly}>
                  ~{subscriptionMoney(total.yearly_equivalent, total.currency)}{" "}
                  / year equivalent
                </p>
                <small>
                  {total.obligation_count} known recurring subscription
                  {total.obligation_count === 1 ? "" : "s"}
                </small>
              </article>
            ))}
          </div>
        ) : (
          <p className={styles.muted}>
            {summary
              ? "Add a known price to see your recurring cost equivalents."
              : "Your recurring totals are unavailable."}
          </p>
        )}
        <div className={styles.costFootnote}>
          <p>
            Based on known recurring subscriptions. Actual billing intervals
            stay unchanged; currencies are kept separate.
          </p>
          <span>{summary?.unknown_cost_count ?? "—"} with unknown cost</span>
        </div>
        {prevented && (
          <div className={styles.costFootnote}>
            <p>
              <strong>
                {prevented.count} unwanted renewal
                {prevented.count === 1 ? "" : "s"} prevented.
              </strong>{" "}
              {prevented.explanation}
            </p>
            <span>
              {prevented.currency_totals
                .map((total) =>
                  subscriptionMoney(total.renewal_amount, total.currency),
                )
                .join(" · ") || "No verified renewal amount yet"}
            </span>
          </div>
        )}
      </section>
      <section
        className={styles.attentionPanel}
        aria-labelledby="subscription-attention-heading"
      >
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>Decide with context</p>
            <h2 id="subscription-attention-heading">
              Needs attention <span>{attention.length}</span>
            </h2>
          </div>
          <a href="/">Open Today ↗</a>
        </div>
        <p className={styles.muted}>
          Trials, renewals, price changes and cancellation issues appear here
          and in your daily attention.
        </p>
        {attention.length > 0 ? (
          <div className={styles.attentionGrid}>
            {attention.slice(0, 4).map(({ item, reasons }) => (
              <button
                className={styles.attentionCard}
                key={item.id}
                type="button"
                onClick={() => void openDetails(item)}
              >
                <span>{reasons[0]}</span>
                <strong>{item.name}</strong>
                <small>
                  {item.trial_ends_at
                    ? subscriptionDate(item.trial_ends_at)
                    : subscriptionDate(item.next_renewal_at)}{" "}
                  · Review →
                </small>
              </button>
            ))}
          </div>
        ) : (
          <p className={styles.emptyAttention}>
            {loading
              ? "Checking your subscriptions…"
              : "No known subscriptions need attention right now."}
          </p>
        )}
        {attention.length > 4 && (
          <p className={styles.muted}>
            {attention.length - 4} more subscriptions to review below.
          </p>
        )}
      </section>
      <section
        className={styles.registry}
        aria-labelledby="subscription-registry-heading"
      >
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>Your subscriptions</p>
            <h2 id="subscription-registry-heading">
              Known recurring subscriptions
            </h2>
          </div>
          <button type="button" disabled={loading} onClick={() => void load()}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        <div className={styles.toolbar}>
          <nav className={styles.filters} aria-label="Filter subscriptions">
            {filters.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={filter === option}
                onClick={() => {
                  setFilter(option);
                  setPage(1);
                }}
              >
                {option}
              </button>
            ))}
          </nav>
          <label className={styles.search}>
            <span className={styles.visuallyHidden}>Find a subscription</span>
            <input
              type="search"
              placeholder="Find a subscription…"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
            />
          </label>
        </div>
        {loading && items.length === 0 ? (
          <div className={styles.empty} role="status">
            Loading your subscriptions…
          </div>
        ) : visible.length === 0 ? (
          <div className={styles.empty}>
            <span aria-hidden="true">↻</span>
            <h3>
              {items.length === 0
                ? "Your recurring picture starts here."
                : "No subscriptions match this view."}
            </h3>
            <p>
              {items.length === 0
                ? "Add a subscription you know, or discover recurring evidence from your connected sources. No banking access is required."
                : "Try another filter or search term."}
            </p>
            {items.length === 0 && (
              <button
                className={styles.primaryButton}
                type="button"
                onClick={() =>
                  setEditor({ item: null, requestId: crypto.randomUUID() })
                }
              >
                Add your first subscription
              </button>
            )}
          </div>
        ) : (
          <ul className={styles.list}>
            {visible.map((item) => {
              const reasons = subscriptionAttention(item);
              const pending = pendingId !== null;
              const candidate = ["CANDIDATE", "UNKNOWN"].includes(item.status);
              const ended = ["CANCELLED", "EXPIRED"].includes(item.status);
              const cancellationPending = [
                "CANCELLATION_REQUESTED",
                "CANCEL_PENDING",
              ].includes(item.status);
              return (
                <li key={item.id} className={styles.row}>
                  <span className={styles.monogram} aria-hidden="true">
                    {item.name.slice(0, 1)}
                  </span>
                  <div className={styles.record}>
                    <button
                      className={styles.nameButton}
                      type="button"
                      onClick={() => void openDetails(item)}
                    >
                      {item.name}
                    </button>
                    <p>
                      {item.plan_name ??
                        item.obligation_type.replaceAll("_", " ").toLowerCase()}
                    </p>
                    <div className={styles.badges}>
                      <span className={styles.status} data-status={item.status}>
                        {item.status.replaceAll("_", " ").toLowerCase()}
                      </span>
                      {reasons.map((reason) => (
                        <span key={reason} className={styles.reason}>
                          {reason}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className={styles.rowCost}>
                    <strong>
                      {subscriptionMoney(
                        item.billing_amount,
                        item.billing_currency,
                      )}
                    </strong>
                    <span>
                      every{" "}
                      {item.interval_count > 1 ? `${item.interval_count} ` : ""}
                      {intervalLabel(item.billing_interval)}
                    </span>
                    <small>
                      {item.trial_ends_at ? "Trial ends" : "Next renewal"}:{" "}
                      {subscriptionDate(
                        item.trial_ends_at ?? item.next_renewal_at,
                      )}
                    </small>
                  </div>
                  <div className={styles.rowActions}>
                    <button
                      type="button"
                      onClick={() => void openDetails(item)}
                    >
                      Review
                    </button>
                    {candidate ? (
                      <>
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => void review(item, "CONFIRM")}
                        >
                          This is mine
                        </button>
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => void review(item, "NOT_MINE")}
                        >
                          Not my subscription
                        </button>
                      </>
                    ) : !ended ? (
                      <>
                        <button
                          type="button"
                          disabled={pending || cancellationPending}
                          onClick={() => void review(item, "KEEP")}
                        >
                          Keep
                        </button>
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => void startCancellation(item)}
                        >
                          {pendingId === item.id
                            ? "Working…"
                            : cancellationPending
                              ? "Check cancellation"
                              : "Cancel subscription"}
                        </button>
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => void review(item, "SNOOZE")}
                        >
                          Review in 7 days
                        </button>
                      </>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {pageCount > 1 && (
          <nav className={styles.pagination} aria-label="Subscription pages">
            <button
              type="button"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
            >
              Previous
            </button>
            <span>
              Page {currentPage} of {pageCount}
            </span>
            <button
              type="button"
              disabled={currentPage === pageCount}
              onClick={() => setPage(currentPage + 1)}
            >
              Next
            </button>
          </nav>
        )}
      </section>
      <footer className={styles.footer}>
        <span>Evidence first. Decisions yours.</span>
        <p>
          Subscription cancellation stops a recurring service or renewal. Email
          unsubscribe only stops marketing messages.
        </p>
      </footer>
      {editor && (
        <SubscriptionEditor
          key={editor.requestId}
          item={editor.item}
          onSave={save}
          onClose={() => setEditor(null)}
        />
      )}
      {stripeOpen && (
        <StripeSandboxConnect
          apiBase={apiBase}
          onClose={() => setStripeOpen(false)}
          onConnected={() => {
            setStripeOpen(false);
            setNotice(
              "Sandbox subscription connected. Open its record to review cancellation.",
            );
            void load();
          }}
        />
      )}
      {detail && (
        <SubscriptionDetails
          key={detail.item.id}
          {...detail}
          busy={busy}
          onClose={() => {
            detailNumber.current++;
            setDetail(null);
          }}
          onEdit={() => {
            setEditor({ item: detail.item, requestId: crypto.randomUUID() });
            setDetail(null);
            detailNumber.current++;
          }}
          onCancel={() => startCancellation(detail.item)}
          onBind={bindProvider}
        />
      )}
      {attempt && (
        <CancellationReview
          key={attempt.id}
          attempt={attempt}
          busy={busy}
          error={attemptError}
          onConfirm={() => mutateAttempt("confirm")}
          onAbort={() => mutateAttempt("abort")}
          onRefresh={() => refreshAttempt(attempt.id)}
          onClose={() => {
            setAttempt(null);
            setAttemptError("");
          }}
        />
      )}
    </div>
  );
}
