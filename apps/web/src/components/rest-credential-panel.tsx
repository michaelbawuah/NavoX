"use client";

import { useEffect, useRef, useState } from "react";
import type { ManagedConnection } from "../lib/connection-management";
import styles from "./file-import-panel.module.css";

export function RestCredentialPanel({
  apiBaseUrl,
  connection,
  onClose,
  onReconnected,
}: {
  apiBaseUrl: string;
  connection: ManagedConnection;
  onClose: () => void;
  onReconnected: () => Promise<void>;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  const controller = useRef<AbortController | null>(null);
  const requestId = useRef<string | null>(null);
  const [hasToken, setHasToken] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => () => controller.current?.abort(), []);

  async function replaceCredential() {
    if (!connection.can_reauthorize || !hasToken || !confirmed || pending)
      return;
    if (!requestId.current) requestId.current = crypto.randomUUID();
    const token = input.current?.value;
    if (input.current) input.current.value = "";
    setHasToken(false);
    setPending(true);
    setError("");
    const abort = new AbortController();
    controller.current = abort;
    try {
      const response = await fetch(
        `${apiBaseUrl}/connectors/generic-rest-api/connections/${encodeURIComponent(connection.id)}/credential`,
        {
          method: "POST",
          credentials: "include",
          cache: "no-store",
          signal: abort.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            token,
            confirmed: true,
            request_id: requestId.current,
          }),
        },
      );
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? "Your session expired. Sign in again."
            : response.status === 409
              ? "This connection changed. Refresh its status before replacing credentials."
              : "Credential replacement failed. Check your token and try again.",
        );
      if (abort.signal.aborted) return;
      await onReconnected();
      if (!abort.signal.aborted) onClose();
    } catch (failure) {
      if (!abort.signal.aborted)
        setError(
          failure instanceof Error
            ? failure.message
            : "Credential replacement failed.",
        );
    } finally {
      if (!abort.signal.aborted) setPending(false);
    }
  }

  return (
    <section className={styles.panel} aria-labelledby="rest-credential-heading">
      <div className={styles.heading}>
        <h3 id="rest-credential-heading">Reconnect {connection.name}</h3>
        <button type="button" disabled={pending} onClick={onClose}>
          Close
        </button>
      </div>
      <p className={styles.help}>
        Replace the API token for{" "}
        {connection.account_label ?? "this connection"}. Existing read
        permissions stay the same. A paused connection stays paused. An active
        connection starts a sync after replacement.
      </p>
      <label className={styles.field}>
        New API token
        <input
          ref={input}
          type="password"
          autoComplete="off"
          disabled={pending || !connection.can_reauthorize}
          onChange={(event) =>
            setHasToken(event.target.value.trim().length > 0)
          }
        />
      </label>
      <label className={styles.consent}>
        <input
          type="checkbox"
          checked={confirmed}
          disabled={pending || !connection.can_reauthorize}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        I approve replacing the credential for this connection.
      </label>
      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
      <button
        type="button"
        disabled={
          !connection.can_reauthorize || !hasToken || !confirmed || pending
        }
        onClick={() => void replaceCredential()}
      >
        {pending ? "Replacing credential…" : "Reconnect service"}
      </button>
    </section>
  );
}
