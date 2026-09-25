"use client";

import type { CancellationAttempt } from "@navox/contracts";
import { useEffect, useRef, useState } from "react";
import {
  canConfirmCancellation,
  cancellationLabel,
  intervalLabel,
  safeManagementUrl,
  subscriptionDate,
  subscriptionMoney,
} from "../lib/subscriptions";
import styles from "./subscriptions-dashboard.module.css";

export function CancellationReview({
  attempt,
  busy,
  error,
  onConfirm,
  onAbort,
  onRefresh,
  onClose,
}: {
  attempt: CancellationAttempt;
  busy: boolean;
  error: string;
  onConfirm: () => Promise<void>;
  onAbort: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [acceptedKey, setAcceptedKey] = useState<string | null>(null);
  const [clock, setClock] = useState(Date.now());
  const { preview } = attempt;
  const target = preview.target;
  const economics = preview.economics ?? target;
  const approvalKey = `${attempt.id}:${attempt.payload_hash}`;
  const accepted = acceptedKey === approvalKey;
  const managementUrl = safeManagementUrl(preview.management_url);
  const verified =
    attempt.status === "VERIFIED_CANCELLED" &&
    attempt.verification_status === "VERIFIED_CANCELLED";
  const awaitingApproval = attempt.status === "AWAITING_CONFIRMATION";
  const expired =
    !!attempt.expires_at && new Date(attempt.expires_at).getTime() <= clock;
  const canAbort =
    ["REQUESTED", "AWAITING_CONFIRMATION"].includes(attempt.status) ||
    (attempt.status === "AWAITING_USER" && attempt.method === "UNSUPPORTED");

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="cancellation-heading"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className={styles.dialogHeading}>
        <div>
          <p className={styles.eyebrow}>
            {verified ? "Verified result" : "Your decision · cancellation"}
          </p>
          <h2 id="cancellation-heading">{cancellationLabel(attempt)}</h2>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={onClose}
          aria-label="Close cancellation review"
        >
          ×
        </button>
      </div>
      <div className={styles.target}>
        <span className={styles.monogram} aria-hidden="true">
          {target.name.slice(0, 1)}
        </span>
        <div>
          <h3>{target.name}</h3>
          <p>{economics.plan_name ?? "Plan name unknown"}</p>
        </div>
        <span
          className={styles.status}
          data-status={verified ? "CANCELLED" : "CANCEL_PENDING"}
        >
          {verified ? "Verified cancelled" : "Not yet cancelled"}
        </span>
      </div>
      <dl className={styles.facts}>
        <div>
          <dt>Current price</dt>
          <dd>
            {subscriptionMoney(
              economics.billing_amount,
              economics.billing_currency,
            )}{" "}
            /{" "}
            {economics.billing_interval_count > 1
              ? `${economics.billing_interval_count} `
              : ""}
            {intervalLabel(economics.billing_interval)}
          </dd>
        </div>
        <div>
          <dt>Next renewal</dt>
          <dd>{subscriptionDate(economics.next_renewal_at)}</dd>
        </div>
        <div>
          <dt>Cancellation method</dt>
          <dd>{attempt.method.replaceAll("_", " ").toLowerCase()}</dd>
        </div>
        <div>
          <dt>Provider state checked</dt>
          <dd>{subscriptionDate(preview.inspected_at)}</dd>
        </div>
        <div>
          <dt>Access ends</dt>
          <dd>{subscriptionDate(preview.access_ends_at)}</dd>
        </div>
        <div>
          <dt>Cancellation fee</dt>
          <dd>
            {preview.fee === null
              ? "Unknown"
              : subscriptionMoney(preview.fee, economics.billing_currency)}
          </dd>
        </div>
        <div>
          <dt>Refund information</dt>
          <dd>{preview.refund ?? "Unknown"}</dd>
        </div>
        <div>
          <dt>Verification</dt>
          <dd>
            {attempt.verification_status.replaceAll("_", " ").toLowerCase()}
          </dd>
        </div>
      </dl>
      <div className={styles.consequences}>
        <strong>Expected effect</strong>
        <p>{preview.expected_effect}</p>
      </div>
      {preview.warnings.length > 0 && (
        <ul className={styles.warnings}>
          {[...new Set(preview.warnings)].map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      {verified ? (
        <p className={styles.success}>
          Independent provider evidence confirms cancellation. This result does
          not promise a refund.
        </p>
      ) : (
        <p className={styles.muted}>
          A request or submitted cancellation is not proof of success. NavoX
          keeps this subscription pending until the result is independently
          verified.
        </p>
      )}
      {attempt.status === "AWAITING_USER" && (
        <div className={styles.consequences}>
          <strong>Continue with your provider</strong>
          <p>
            Complete any sign-in, MFA or security challenge directly with the
            provider. Return here to check verification. NavoX cannot bypass
            these steps.
          </p>
        </div>
      )}
      {managementUrl && (
        <a
          className={styles.linkButton}
          href={managementUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open verified provider page ↗
        </a>
      )}
      {attempt.method === "UNSUPPORTED" && (
        <p className={styles.muted}>
          No supported cancellation method is available for this subscription.
          Contact the provider using your existing account or official website.
        </p>
      )}
      {awaitingApproval && (
        <fieldset className={styles.approval} disabled={busy || expired}>
          <legend>Explicit cancellation approval</legend>
          <label className={styles.checkbox}>
            <input
              type="checkbox"
              checked={accepted}
              onChange={(event) =>
                setAcceptedKey(event.target.checked ? approvalKey : null)
              }
            />
            <span>
              I authorize cancellation of{" "}
              <strong>
                {target.name}
                {economics.plan_name ? ` — ${economics.plan_name}` : ""}
              </strong>{" "}
              with the exact price, renewal and consequences shown above.
            </span>
          </label>
          <p>
            This cancels the paid or service subscription. It is separate from
            unsubscribing from marketing email.
          </p>
          <button
            className={styles.dangerButton}
            disabled={!canConfirmCancellation(attempt, accepted, clock) || busy}
            onClick={() => void onConfirm()}
            type="button"
          >
            {busy
              ? "Submitting your decision…"
              : "Confirm this exact cancellation"}
          </button>
        </fieldset>
      )}
      {expired && awaitingApproval && (
        <p role="alert" className={styles.error}>
          This preview has expired. Withdraw this request and prepare a fresh
          preview before confirming.
        </p>
      )}
      {attempt.failure_code && (
        <p className={styles.error}>
          The request stopped safely:{" "}
          {attempt.failure_code.replaceAll("_", " ")}. Review the provider state
          before retrying.
        </p>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      <div className={styles.actions}>
        {!awaitingApproval && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onRefresh()}
          >
            {busy ? "Checking…" : "Refresh verification status"}
          </button>
        )}
        {canAbort && (
          <button type="button" disabled={busy} onClick={() => void onAbort()}>
            Withdraw cancellation request
          </button>
        )}
        <button type="button" disabled={busy} onClick={onClose}>
          {canAbort ? "Close review" : "Done"}
        </button>
      </div>
      {!canAbort && !verified && (
        <p className={styles.finePrint}>
          Closing this window does not undo a request already submitted to your
          provider.
        </p>
      )}
    </dialog>
  );
}
