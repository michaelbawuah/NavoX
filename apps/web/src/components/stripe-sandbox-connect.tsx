"use client";

import type { RecurringSubscription } from "@navox/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { subscriptionRequest } from "../lib/subscriptions";
import styles from "./subscriptions-dashboard.module.css";

export function StripeSandboxConnect({
  apiBase,
  onConnected,
  onClose,
}: {
  apiBase: string;
  onConnected: (item: RecurringSubscription) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const keyInput = useRef<HTMLInputElement>(null);
  const sending = useRef(false);
  const requestId = useRef(crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [consent, setConsent] = useState(false);
  const [subscriptionId, setSubscriptionId] = useState("");

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const key = keyInput.current?.value ?? "";
    if (sending.current || !consent) return;
    if (!/^(rk|sk)_test_[A-Za-z0-9]{8,240}$/.test(key)) {
      setError(
        "Enter a Stripe sandbox key beginning with rk_test_ or sk_test_.",
      );
      return;
    }
    sending.current = true;
    setBusy(true);
    setError("");
    if (keyInput.current) keyInput.current.value = "";
    try {
      const item = await subscriptionRequest<RecurringSubscription>(
        apiBase,
        "/subscriptions/stripe-sandbox",
        {
          request_id: requestId.current,
          subscription_id: subscriptionId,
          token: key,
          confirmed: true,
        },
      );
      onConnected(item);
    } catch {
      // Never render a response that might include submitted credential fields.
      setError(
        "Could not connect. Check the sandbox key, its account-read and subscription-write permissions, and the subscription ID. Use an active USD plan without tax, discounts or a trial. Enter the key again to retry.",
      );
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="stripe-sandbox-heading"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className={styles.dialogHeading}>
        <h2 id="stripe-sandbox-heading">
          Connect a Stripe sandbox subscription
        </h2>
        <button
          type="button"
          aria-label="Close Stripe sandbox setup"
          disabled={busy}
          onClick={onClose}
        >
          ×
        </button>
      </div>
      <p>
        Connect one disposable test subscription from your Stripe sandbox. Its
        simulated costs and cancellations are excluded from spending and
        prevented-renewal totals.
      </p>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <label htmlFor="stripe-subscription-id">Subscription ID</label>
        <input
          id="stripe-subscription-id"
          value={subscriptionId}
          onChange={(event) => setSubscriptionId(event.target.value.trim())}
          placeholder="sub_…"
          pattern="sub_[A-Za-z0-9]{1,120}"
          required
          disabled={busy}
        />
        <label htmlFor="stripe-sandbox-key">Sandbox API key</label>
        <input
          id="stripe-sandbox-key"
          ref={keyInput}
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="rk_test_…"
          required
          disabled={busy}
        />
        <p className={styles.muted}>
          Use a restricted sandbox key with account read and subscription write
          permissions. NavoX encrypts the key. Live keys are rejected.
        </p>
        <label className={styles.checkbox}>
          <input
            type="checkbox"
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
            disabled={busy}
          />
          Allow NavoX to inspect this sandbox subscription and offer
          cancellation for my separate approval.
        </label>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
        <div className={styles.actions}>
          <button type="button" disabled={busy} onClick={onClose}>
            Close
          </button>
          <button
            type="submit"
            className={styles.primaryButton}
            disabled={busy || !consent}
          >
            {busy ? "Connecting…" : "Connect test subscription"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
