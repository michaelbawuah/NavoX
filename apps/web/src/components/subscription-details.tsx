"use client";

import type {
  RecurringSubscription,
  SubscriptionCancellationProfile,
  SubscriptionEvidence,
  SubscriptionPrice,
} from "@navox/contracts";
import { useEffect, useRef, useState } from "react";
import {
  intervalLabel,
  subscriptionDate,
  subscriptionMoney,
} from "../lib/subscriptions";
import styles from "./subscriptions-dashboard.module.css";

export function SubscriptionDetails({
  item,
  evidence,
  history,
  profiles,
  loading,
  error,
  busy,
  onClose,
  onEdit,
  onCancel,
  onBind,
}: {
  item: RecurringSubscription;
  evidence: SubscriptionEvidence[];
  history: SubscriptionPrice[];
  profiles: SubscriptionCancellationProfile[];
  loading: boolean;
  error: string;
  busy: boolean;
  onClose: () => void;
  onEdit: () => void;
  onCancel: () => Promise<void>;
  onBind: (
    profile: SubscriptionCancellationProfile,
    connectionId: string,
    resourceId: string,
  ) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [selection, setSelection] = useState("");
  const [resourceId, setResourceId] = useState(
    item.cancellation_external_resource_id ?? "",
  );
  const [consent, setConsent] = useState(false);
  const choice = profiles
    .flatMap((profile) =>
      profile.connection_ids.map((connectionId) => ({
        profile,
        connectionId,
        key: `${profile.id}:${connectionId}`,
      })),
    )
    .find((option) => option.key === selection);
  const pendingCancellation = [
    "CANCELLATION_REQUESTED",
    "CANCEL_PENDING",
  ].includes(item.status);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="subscription-details-heading"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className={styles.dialogHeading}>
        <div>
          <p className={styles.eyebrow}>Subscription record</p>
          <h2 id="subscription-details-heading">{item.name}</h2>
          <p className={styles.muted}>
            {item.plan_name ?? "Plan name unknown"}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          aria-label="Close subscription details"
        >
          ×
        </button>
      </div>
      <span className={styles.status} data-status={item.status}>
        {item.status.replaceAll("_", " ").toLowerCase()}
      </span>
      <dl className={styles.facts}>
        <div>
          <dt>Actual billing</dt>
          <dd>
            {subscriptionMoney(item.billing_amount, item.billing_currency)} /{" "}
            {item.interval_count > 1 ? `${item.interval_count} ` : ""}
            {intervalLabel(item.billing_interval)}
          </dd>
        </div>
        <div>
          <dt>Monthly equivalent</dt>
          <dd>
            {subscriptionMoney(item.monthly_equivalent, item.billing_currency)}
          </dd>
        </div>
        <div>
          <dt>Next renewal</dt>
          <dd>{subscriptionDate(item.next_renewal_at)}</dd>
        </div>
        <div>
          <dt>Last verified</dt>
          <dd>{subscriptionDate(item.last_verified_at)}</dd>
        </div>
        <div>
          <dt>Trial ends</dt>
          <dd>{subscriptionDate(item.trial_ends_at)}</dd>
        </div>
        <div>
          <dt>After trial</dt>
          <dd>
            {subscriptionMoney(
              item.trial_conversion_amount,
              item.billing_currency,
            )}{" "}
            /{" "}
            {item.trial_conversion_interval_count > 1
              ? `${item.trial_conversion_interval_count} `
              : ""}
            {intervalLabel(item.trial_conversion_interval)}
          </dd>
        </div>
        <div>
          <dt>Auto-renew</dt>
          <dd>
            {item.auto_renew === null
              ? "Unknown"
              : item.auto_renew
                ? "Enabled"
                : "Disabled"}
          </dd>
        </div>
        <div>
          <dt>Your review</dt>
          <dd>{item.review_state.replaceAll("_", " ").toLowerCase()}</dd>
        </div>
      </dl>
      {pendingCancellation && (
        <p className={styles.notice}>
          Cancellation has been requested, but success is not yet verified.
        </p>
      )}
      <div className={styles.actions}>
        <button type="button" disabled={busy} onClick={onEdit}>
          Correct details
        </button>
        <button
          type="button"
          disabled={
            busy ||
            item.review_state === "NOT_MINE" ||
            item.status === "CANDIDATE" ||
            item.status === "UNKNOWN"
          }
          onClick={() => void onCancel()}
        >
          {pendingCancellation || ["CANCELLED", "EXPIRED"].includes(item.status)
            ? "Cancellation status"
            : "Review cancellation"}
        </button>
      </div>
      <section
        className={styles.detailSection}
        aria-labelledby="subscription-evidence-heading"
      >
        <h3 id="subscription-evidence-heading">Evidence & provenance</h3>
        <p className={styles.muted}>
          Sources explain this record. Source content cannot authorize
          cancellation.
        </p>
        {loading ? (
          <p role="status">Loading evidence…</p>
        ) : evidence.length === 0 ? (
          <p className={styles.muted}>No evidence is available.</p>
        ) : (
          <ol className={styles.timeline}>
            {evidence.map((entry) => (
              <li key={entry.id}>
                <div>
                  <strong>
                    {entry.evidence_type.replaceAll("_", " ").toLowerCase()}
                  </strong>
                  <time dateTime={entry.observed_at}>
                    {subscriptionDate(entry.observed_at)}
                  </time>
                </div>
                <p>
                  {entry.source_type.replaceAll("_", " ")} ·{" "}
                  {entry.merchant_text ?? item.name}
                  {entry.plan_text ? ` · ${entry.plan_text}` : ""}
                </p>
                {entry.amount !== null && (
                  <p>
                    {subscriptionMoney(entry.amount, entry.currency)} /{" "}
                    {entry.interval_count > 1 ? `${entry.interval_count} ` : ""}
                    {intervalLabel(entry.billing_interval)}
                  </p>
                )}
                {entry.renewal_at && (
                  <p>Renewal: {subscriptionDate(entry.renewal_at)}</p>
                )}
                <small>
                  Confidence {Math.round(Number(entry.confidence) * 100)}% ·{" "}
                  {entry.connection_id
                    ? "Connected source"
                    : "Manual or independent source"}
                </small>
              </li>
            ))}
          </ol>
        )}
      </section>
      <section
        className={styles.detailSection}
        aria-labelledby="subscription-history-heading"
      >
        <h3 id="subscription-history-heading">Price history</h3>
        {loading ? (
          <p role="status">Loading price history…</p>
        ) : history.length === 0 ? (
          <p className={styles.muted}>No known price history yet.</p>
        ) : (
          <ol className={styles.timeline}>
            {history.map((entry) => (
              <li key={entry.id}>
                <strong>
                  {subscriptionMoney(entry.amount, entry.currency)} /{" "}
                  {entry.interval_count > 1 ? `${entry.interval_count} ` : ""}
                  {intervalLabel(entry.billing_interval)}
                </strong>
                <p>
                  {subscriptionDate(entry.effective_from)} →{" "}
                  {entry.effective_until
                    ? subscriptionDate(entry.effective_until)
                    : "Present"}
                </p>
                <small>Preserved from source evidence</small>
              </li>
            ))}
          </ol>
        )}
      </section>
      <details className={styles.disclosure}>
        <summary>Provider cancellation connection</summary>
        <p className={styles.muted}>
          Choose an available provider cancellation profile and the exact
          subscription ID from that account. Enabling this permission never
          approves a cancellation; each action requires its own exact
          confirmation.
        </p>
        {item.cancellation_connection_id && (
          <p className={styles.notice}>
            A provider connection is linked to this record.
          </p>
        )}
        {profiles.length === 0 ? (
          <p className={styles.muted}>
            No compatible cancellation profiles are available. You can still
            review available provider guidance.
          </p>
        ) : (
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              if (choice && consent && resourceId.trim())
                void onBind(
                  choice.profile,
                  choice.connectionId,
                  resourceId.trim(),
                );
            }}
          >
            <label>
              Provider profile
              <select
                value={selection}
                disabled={busy}
                required
                onChange={(event) => {
                  setSelection(event.target.value);
                  setConsent(false);
                }}
              >
                <option value="">Choose a provider account</option>
                {profiles.flatMap((profile) =>
                  profile.connection_ids.map((connectionId) => (
                    <option
                      key={`${profile.id}:${connectionId}`}
                      value={`${profile.id}:${connectionId}`}
                    >
                      {profile.name} · {profile.merchant_domain} · account{" "}
                      {connectionId.slice(0, 8)}
                    </option>
                  )),
                )}
              </select>
            </label>
            <label>
              Exact provider subscription ID
              <input
                value={resourceId}
                disabled={busy}
                required
                maxLength={512}
                onChange={(event) => {
                  setResourceId(event.target.value);
                  setConsent(false);
                }}
                autoComplete="off"
              />
            </label>
            <label className={styles.checkbox}>
              <input
                checked={consent}
                disabled={busy || !choice}
                type="checkbox"
                onChange={(event) => setConsent(event.target.checked)}
              />
              <span>
                I authorize this provider connection to inspect and perform
                explicitly approved paid-subscription cancellations, and confirm
                that this ID refers to {item.name}.
              </span>
            </label>
            <button
              className={styles.primaryButton}
              disabled={busy || !choice || !consent || !resourceId.trim()}
              type="submit"
            >
              {busy ? "Linking…" : "Enable permission and link subscription"}
            </button>
          </form>
        )}
      </details>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </dialog>
  );
}
