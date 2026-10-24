"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  canReadOutlook,
  type OutlookStatus,
  outlookAuthorizationUrl,
  outlookRequest,
  readOutlookStatus,
} from "../lib/outlook";
import styles from "./outlook.module.css";

export function OutlookConnectionPanel({ accountKey }: { accountKey: string }) {
  return <OutlookConnectionAccount key={accountKey} accountKey={accountKey} />;
}
function OutlookConnectionAccount({ accountKey }: { accountKey: string }) {
  const [status, setStatus] = useState<OutlookStatus | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const [disconnect, setDisconnect] = useState<string | null>(null);
  const live = useRef(false),
    operation = useRef(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry rechecks the owned connection status.
  useEffect(() => {
    live.current = true;
    const abort = new AbortController();
    setError("");
    outlookRequest(
      accountKey,
      "/status",
      readOutlookStatus,
      "GET",
      undefined,
      abort.signal,
    )
      .then((value) => {
        if (!abort.signal.aborted) setStatus(value);
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setError("Outlook connection status couldn’t load.");
      });
    const outcome = new URLSearchParams(window.location.search).get(
      "outlook_connection",
    );
    if (outcome) {
      setNotice(
        outcome === "connected" || outcome === "reconnected"
          ? "Outlook authorization returned. Check the saved permissions below."
          : "Outlook authorization did not complete. You can try connecting again.",
      );
      const url = new URL(window.location.href);
      url.searchParams.delete("outlook_connection");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
    return () => {
      live.current = false;
      abort.abort();
    };
  }, [accountKey, retry]);
  async function connect(id?: string, send = false) {
    if (operation.current || !status?.configured) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      const url = await outlookRequest(
        accountKey,
        "/connect",
        (value) => {
          if (
            !value ||
            typeof value !== "object" ||
            !("authorization_url" in value)
          )
            throw new Error("Outlook authorization could not start.");
          return outlookAuthorizationUrl(value.authorization_url);
        },
        "POST",
        { confirmed: true, ...(id ? { connection_id: id } : {}), send },
      );
      if (live.current) window.location.assign(url);
    } catch (cause) {
      if (live.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "Outlook authorization could not start.",
        );
    } finally {
      operation.current = false;
      if (live.current) setBusy(false);
    }
  }
  async function remove() {
    if (!disconnect || operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      await outlookRequest(
        accountKey,
        `/connections/${encodeURIComponent(disconnect)}`,
        () => undefined,
        "DELETE",
      );
      if (!live.current) return;
      setDisconnect(null);
      setNotice(
        "Outlook disconnected. NavoX can no longer read or send through this connection.",
      );
      setStatus(null);
      setRetry((v) => v + 1);
    } catch (cause) {
      if (live.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "Outlook could not disconnect. Check its saved status before trying again.",
        );
    } finally {
      operation.current = false;
      if (live.current) setBusy(false);
    }
  }
  return (
    <section
      id="outlook"
      className={styles.card}
      aria-label="Outlook connection"
    >
      <h2>Outlook</h2>
      <p>
        Connect your school or personal Microsoft mailbox. Reading and sending
        use separate permissions.
      </p>
      {!status && !error && <p role="status">Checking Outlook setup…</p>}
      {status?.notice && <p>{status.notice}</p>}
      {status && !status.configured && (
        <p>
          Outlook setup is pending for this release. The connection button will
          be available after setup is complete.
        </p>
      )}
      {status?.connections.map((connection) => (
        <div className={styles.connection} key={connection.id}>
          <strong>{connection.email}</strong>
          <p>
            {["CONNECTED", "DEGRADED"].includes(connection.status)
              ? "Connection saved"
              : "Reconnect needed"}{" "}
            ·{" "}
            {connection.read_enabled
              ? "Reading available"
              : "Reading unavailable"}{" "}
            ·{" "}
            {connection.send_enabled
              ? "Send permission available"
              : "Sending not enabled"}
          </p>
          <div className={styles.actions}>
            {canReadOutlook(connection) && (
              <Link href="/inbox">Open Inbox</Link>
            )}
            <button
              type="button"
              disabled={busy || !status.configured}
              onClick={() =>
                void connect(connection.id, connection.send_enabled)
              }
            >
              Reconnect
            </button>
            {!connection.send_enabled && (
              <button
                type="button"
                disabled={busy || !status.configured}
                onClick={() => void connect(connection.id, true)}
              >
                Enable sending permission
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => setDisconnect(connection.id)}
            >
              Disconnect
            </button>
          </div>
        </div>
      ))}
      {status?.configured && (
        <button type="button" disabled={busy} onClick={() => void connect()}>
          Connect Outlook
        </button>
      )}
      {disconnect && (
        <div className={styles.confirm}>
          <p>
            Disconnect{" "}
            {status?.connections.find((c) => c.id === disconnect)?.email}? New
            reading and sending will stop. Your messages remain in Outlook.
          </p>
          <button type="button" disabled={busy} onClick={() => void remove()}>
            Confirm disconnect
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setDisconnect(null)}
          >
            Keep connected
          </button>
        </div>
      )}
      <p className={styles.hint}>
        Sending permission does not send a message. Every reply still needs an
        exact review and your Send approval.
      </p>
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            type="button"
            disabled={busy}
            onClick={() => setRetry((v) => v + 1)}
          >
            Try again
          </button>
        </p>
      )}
    </section>
  );
}
