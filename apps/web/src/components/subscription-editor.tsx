"use client";

import type {
  RecurringSubscription,
  SubscriptionInput,
  SubscriptionInterval,
  SubscriptionType,
} from "@navox/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { SUBSCRIPTION_CURRENCIES } from "../lib/currencies";
import styles from "./subscriptions-dashboard.module.css";

function localDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

function IntervalOptions() {
  return (
    <>
      <option value="UNKNOWN">Unknown</option>
      <option value="DAY">Day</option>
      <option value="WEEK">Week</option>
      <option value="MONTH">Month</option>
      <option value="QUARTER">Quarter</option>
      <option value="YEAR">Year</option>
    </>
  );
}

export function SubscriptionEditor({
  item,
  onSave,
  onClose,
}: {
  item: RecurringSubscription | null;
  onSave: (input: SubscriptionInput) => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const saving = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState(item?.name ?? "");
  const [plan, setPlan] = useState(item?.plan_name ?? "");
  const [kind, setKind] = useState<SubscriptionType>(
    item?.obligation_type ?? "SUBSCRIPTION",
  );
  const [amount, setAmount] = useState(item?.billing_amount ?? "");
  const [currency, setCurrency] = useState(item?.billing_currency ?? "");
  const [interval, setInterval] = useState<SubscriptionInterval>(
    item?.billing_interval ?? "MONTH",
  );
  const [intervalCount, setIntervalCount] = useState(
    String(item?.interval_count ?? 1),
  );
  const [renewal, setRenewal] = useState(localDate(item?.next_renewal_at));
  const [trial, setTrial] = useState(localDate(item?.trial_ends_at));
  const [trialPrice, setTrialPrice] = useState(
    item?.trial_conversion_amount ?? "",
  );
  const [trialInterval, setTrialInterval] = useState<SubscriptionInterval>(
    item?.trial_conversion_interval ?? "UNKNOWN",
  );
  const [trialIntervalCount, setTrialIntervalCount] = useState(
    String(item?.trial_conversion_interval_count ?? 1),
  );
  const [autoRenew, setAutoRenew] = useState(
    item?.auto_renew == null ? "unknown" : item.auto_renew ? "yes" : "no",
  );
  const [status, setStatus] = useState<SubscriptionInput["status"]>("ACTIVE");

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving.current) return;
    if (
      !name.trim() ||
      ((amount || trialPrice) && !/^[A-Z]{3}$/.test(currency))
    ) {
      setError("Enter a name and select a currency for any known price.");
      return;
    }
    saving.current = true;
    setBusy(true);
    setError("");
    try {
      await onSave({
        name: name.trim(),
        plan_name: plan.trim() || null,
        obligation_type: kind,
        billing_amount: amount || null,
        billing_currency: currency || null,
        billing_interval: interval,
        interval_count: Number(intervalCount),
        next_renewal_at:
          item && renewal === localDate(item.next_renewal_at)
            ? item.next_renewal_at
            : renewal
              ? new Date(renewal).toISOString()
              : null,
        trial_ends_at:
          item && trial === localDate(item.trial_ends_at)
            ? item.trial_ends_at
            : trial
              ? new Date(trial).toISOString()
              : null,
        trial_conversion_amount: trialPrice || null,
        trial_conversion_interval:
          trialInterval === "UNKNOWN" ? null : trialInterval,
        trial_conversion_interval_count: Number(trialIntervalCount),
        auto_renew: autoRenew === "unknown" ? null : autoRenew === "yes",
        ...(!item ? { status } : {}),
      });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "NavoX could not save this subscription.",
      );
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="subscription-editor-heading"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className={styles.dialogHeading}>
        <div>
          <p className={styles.eyebrow}>Your records</p>
          <h2 id="subscription-editor-heading">
            {item ? "Correct subscription details" : "Add a subscription"}
          </h2>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={onClose}
          aria-label="Close subscription editor"
        >
          ×
        </button>
      </div>
      <p className={styles.muted}>
        Enter what you know. Unknown prices and dates stay unknown. Corrections
        are saved with their evidence history.
      </p>
      <form onSubmit={(event) => void submit(event)} className={styles.form}>
        <fieldset disabled={busy}>
          <div className={styles.formGrid}>
            <label>
              Name
              <input
                value={name}
                required
                maxLength={256}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Your streaming membership"
              />
            </label>
            <label>
              Plan name
              <input
                value={plan}
                maxLength={256}
                onChange={(event) => setPlan(event.target.value)}
                placeholder="Unknown if left blank"
              />
            </label>
            <label>
              Type
              <select
                value={kind}
                onChange={(event) =>
                  setKind(event.target.value as SubscriptionType)
                }
              >
                <option value="SUBSCRIPTION">Subscription</option>
                <option value="MEMBERSHIP">Membership</option>
                <option value="FREE_TRIAL">Free trial</option>
                <option value="SOFTWARE_LICENSE">Software license</option>
                <option value="DOMAIN_RENEWAL">Domain renewal</option>
                <option value="SERVICE_PLAN">Service plan</option>
                <option value="RECURRING_BILL">Recurring bill</option>
                <option value="OTHER_RECURRING">Other recurring</option>
              </select>
            </label>
            {!item && (
              <label>
                Current status
                <select
                  value={status}
                  onChange={(event) =>
                    setStatus(event.target.value as SubscriptionInput["status"])
                  }
                >
                  <option value="ACTIVE">Active</option>
                  <option value="TRIAL">Trial</option>
                  <option value="PAUSED">Paused</option>
                  <option value="UNKNOWN">Unknown</option>
                </select>
              </label>
            )}
            <label>
              Amount per billing cycle
              <input
                type="number"
                inputMode="decimal"
                min="0"
                step="0.0001"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="Unknown"
              />
            </label>
            <label>
              Currency
              <select
                name="billing_currency"
                value={currency}
                onChange={(event) => setCurrency(event.target.value)}
                required={!!amount || !!trialPrice}
              >
                <option value="">Select currency · Unknown</option>
                {currency &&
                  !SUBSCRIPTION_CURRENCIES.some(
                    ([code]) => code === currency,
                  ) && (
                    <option value={currency}>
                      {currency} — Saved currency
                    </option>
                  )}
                {SUBSCRIPTION_CURRENCIES.map(([code, label]) => (
                  <option key={code} value={code}>
                    {code} — {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Billing interval
              <select
                value={interval}
                onChange={(event) =>
                  setInterval(event.target.value as SubscriptionInterval)
                }
              >
                <IntervalOptions />
              </select>
            </label>
            <label>
              Bill every how many intervals?
              <input
                type="number"
                min="1"
                max="1200"
                step="1"
                required
                value={intervalCount}
                onChange={(event) => setIntervalCount(event.target.value)}
              />
            </label>
            <label>
              Next renewal · your device&apos;s local time
              <input
                type="datetime-local"
                value={renewal}
                onChange={(event) => setRenewal(event.target.value)}
              />
            </label>
            <label>
              Auto-renew
              <select
                value={autoRenew}
                onChange={(event) => setAutoRenew(event.target.value)}
              >
                <option value="unknown">Unknown</option>
                <option value="yes">Enabled</option>
                <option value="no">Disabled</option>
              </select>
            </label>
          </div>
          <details className={styles.disclosure}>
            <summary>Trial and conversion details</summary>
            <div className={styles.formGrid}>
              <label>
                Trial ends · your device&apos;s local time
                <input
                  type="datetime-local"
                  value={trial}
                  onChange={(event) => setTrial(event.target.value)}
                />
              </label>
              <label>
                Post-trial amount
                <input
                  type="number"
                  inputMode="decimal"
                  min="0"
                  step="0.0001"
                  value={trialPrice}
                  onChange={(event) => setTrialPrice(event.target.value)}
                  placeholder="Unknown"
                />
              </label>
              <label>
                Post-trial interval
                <select
                  value={trialInterval}
                  onChange={(event) =>
                    setTrialInterval(event.target.value as SubscriptionInterval)
                  }
                >
                  <IntervalOptions />
                </select>
              </label>
              <label>
                Post-trial interval count
                <input
                  type="number"
                  min="1"
                  max="1200"
                  step="1"
                  required
                  value={trialIntervalCount}
                  onChange={(event) =>
                    setTrialIntervalCount(event.target.value)
                  }
                />
              </label>
            </div>
          </details>
        </fieldset>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
        <div className={styles.actions}>
          <button
            className={styles.primaryButton}
            type="submit"
            disabled={busy}
          >
            {busy ? "Saving…" : item ? "Save correction" : "Add subscription"}
          </button>
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </dialog>
  );
}
