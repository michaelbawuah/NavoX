"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";
import {
  canReadOutlook,
  type OutlookConnection,
  type OutlookDraft,
  type OutlookMessage,
  type OutlookMessageDetail,
  type OutlookStatus,
  outlookRequest,
  readOutlookDrafts,
  readOutlookMessageDetail,
  readOutlookMessages,
  readOutlookStatus,
} from "../lib/outlook";
import inbox from "./inbox-workspace.module.css";
import styles from "./outlook.module.css";
import { OutlookReplyEditor, outlookSendKey } from "./outlook-reply-editor";

function pendingReply(accountKey: string, draft: OutlookDraft): OutlookDraft {
  if (draft.status !== "review") return draft;
  // A lost response can leave this browser uncertain before the server updates its record.
  try {
    if (sessionStorage.getItem(outlookSendKey(accountKey, draft.id)) !== null)
      return {
        ...draft,
        status: "uncertain",
        notice:
          "The send outcome remains unconfirmed. Check the outcome before taking another action.",
      };
  } catch {}
  return draft;
}

export function OutlookInbox({
  accountKey,
  timezone,
  fallback = null,
}: {
  accountKey: string;
  timezone?: string;
  fallback?: ReactNode;
}) {
  return (
    <OutlookInboxAccount
      key={accountKey}
      accountKey={accountKey}
      timezone={timezone ?? "UTC"}
      fallback={fallback}
    />
  );
}
function OutlookInboxAccount({
  accountKey,
  timezone,
  fallback,
}: {
  accountKey: string;
  timezone: string;
  fallback: ReactNode;
}) {
  const [status, setStatus] = useState<OutlookStatus | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState("");
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry rechecks connection status without inventing a connection.
  useEffect(() => {
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
        if (abort.signal.aborted) return;
        setStatus(value);
        setSelected((current) =>
          value.connections.some((c) => c.id === current && canReadOutlook(c))
            ? current
            : (value.connections.find(canReadOutlook)?.id ?? ""),
        );
      })
      .catch(() => {
        if (!abort.signal.aborted) setError("Outlook status couldn’t load.");
      });
    return () => abort.abort();
  }, [accountKey, retry]);
  if (error)
    return (
      <>
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={() => setRetry((v) => v + 1)}>
            Try again
          </button>
        </p>
        <Link href="/connections?source=outlook#outlook">
          Check Outlook connection
        </Link>
      </>
    );
  if (!status)
    return fallback ? (
      <p role="status">Checking your email connections…</p>
    ) : null;
  const connections = status.connections.filter(canReadOutlook);
  const connection = connections.find((c) => c.id === selected);
  if (!connection) return fallback;
  return (
    <section className={styles.card} aria-label="Outlook inbox">
      <div className={styles.actions}>
        <h2>Outlook</h2>
        {connections.length > 1 ? (
          <label>
            Mailbox{" "}
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              {connections.map((c) => (
                <option value={c.id} key={c.id}>
                  {c.email}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span>{connection.email}</span>
        )}
        <Link href="/connections?source=outlook#outlook">
          Manage connection
        </Link>
      </div>
      <OutlookMailbox
        key={connection.id}
        accountKey={accountKey}
        connection={connection}
        timezone={timezone}
      />
    </section>
  );
}
function OutlookMailbox({
  accountKey,
  connection,
  timezone,
}: {
  accountKey: string;
  connection: OutlookConnection;
  timezone: string;
}) {
  const [messages, setMessages] = useState<OutlookMessage[]>([]);
  const [drafts, setDrafts] = useState<OutlookDraft[]>([]);
  const [selected, setSelected] = useState<OutlookMessageDetail | null>(null);
  const [draft, setDraft] = useState<OutlookDraft | null>(null);
  const [tab, setTab] = useState<"needs" | "drafts" | "waiting">("needs");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [messageId, setMessageId] = useState("");
  const [messageRetry, setMessageRetry] = useState(0);
  useEffect(() => {
    const refreshVisible = () => {
      if (document.visibilityState === "visible") setRetry((v) => v + 1);
    };
    window.addEventListener("focus", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    const timer = window.setInterval(refreshVisible, 60_000);
    return () => {
      window.removeEventListener("focus", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
      window.clearInterval(timer);
    };
  }, []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry refreshes read-only mailbox data after an error or confirmed action.
  useEffect(() => {
    const abort = new AbortController();
    setError("");
    setLoading(true);
    Promise.all([
      outlookRequest(
        accountKey,
        `/connections/${encodeURIComponent(connection.id)}/messages`,
        readOutlookMessages,
        "GET",
        undefined,
        abort.signal,
      ),
      outlookRequest(
        accountKey,
        "/drafts",
        readOutlookDrafts,
        "GET",
        undefined,
        abort.signal,
      ),
    ])
      .then(([mail, saved]) => {
        if (abort.signal.aborted) return;
        setMessages(mail.messages);
        setDrafts(
          saved
            .filter((d) => d.connection_id === connection.id)
            .map((d) => pendingReply(accountKey, d)),
        );
        setNotice(
          [
            mail.notice,
            mail.has_more ? "More messages are available in Outlook." : "",
          ]
            .filter(Boolean)
            .join(" "),
        );
      })
      .catch((cause) => {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Your Outlook inbox couldn’t load.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [accountKey, connection.id, retry]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Reselecting a failed message retries only its read, never a send.
  useEffect(() => {
    const abort = new AbortController();
    setSelected(null);
    if (!messageId) return;
    outlookRequest(
      accountKey,
      `/connections/${encodeURIComponent(connection.id)}/messages/${encodeURIComponent(messageId)}`,
      readOutlookMessageDetail,
      "GET",
      undefined,
      abort.signal,
    )
      .then((value) => {
        if (!abort.signal.aborted && value.id === messageId) setSelected(value);
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setError(
            "This email couldn’t load. Choose it again or open Outlook.",
          );
      });
    return () => abort.abort();
  }, [accountKey, connection.id, messageId, messageRetry]);
  const editable = drafts.filter((d) => d.status === "review");
  const waiting = drafts.filter((d) =>
    ["executing", "uncertain", "completed"].includes(d.status),
  );
  const waitingSources = new Set(waiting.map((d) => d.source_message_id));
  const needsReply = messages.filter(
    (message) => !waitingSources.has(message.id),
  );
  function updated(value: OutlookDraft | null) {
    setDrafts((current) =>
      value
        ? [
            pendingReply(accountKey, value),
            ...current.filter((saved) => saved.id !== value.id),
          ]
        : current.filter((saved) => saved.id !== draft?.id),
    );
    setDraft(value);
    setRetry((v) => v + 1);
  }
  function chooseDraft(value: OutlookDraft) {
    setSelected(null);
    setDraft(value);
    setMessageId(value.source_message_id);
    setMessageRetry((v) => v + 1);
    setError("");
  }
  function chooseMessage(value: OutlookMessage) {
    // Reopening the source must not bypass a saved or uncertain reply.
    const related = drafts.filter(
      (d) => d.source_message_id === value.id && d.status !== "discarded",
    );
    const existing =
      related.find(
        (d) => d.status === "executing" || d.status === "uncertain",
      ) ??
      related.find((d) => d.status === "completed") ??
      related[0] ??
      null;
    setSelected(null);
    setDraft(existing);
    setMessageId(value.id);
    setMessageRetry((v) => v + 1);
    setError("");
  }
  function when(value: string | null) {
    return value
      ? new Intl.DateTimeFormat("en", {
          dateStyle: "medium",
          timeStyle: "short",
          timeZone: timezone,
        }).format(new Date(value))
      : "Date not supplied";
  }
  return (
    <>
      <nav className={inbox.tabs} aria-label="Outlook sections">
        <button
          type="button"
          aria-pressed={tab === "needs"}
          onClick={() => setTab("needs")}
        >
          Needs reply · {needsReply.length}
        </button>
        <button
          type="button"
          aria-pressed={tab === "drafts"}
          onClick={() => setTab("drafts")}
        >
          Drafts · {editable.length}
        </button>
        <button
          type="button"
          aria-pressed={tab === "waiting"}
          onClick={() => setTab("waiting")}
        >
          Waiting · {waiting.length}
        </button>
      </nav>
      {loading && <p role="status">Checking your Outlook messages…</p>}
      {notice && <p className={styles.hint}>{notice}</p>}
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={() => setRetry((v) => v + 1)}>
            Try again
          </button>
        </p>
      )}
      <div className={inbox.layout}>
        <div className={inbox.list}>
          {tab === "needs"
            ? needsReply.map((message) => (
                <button
                  key={message.id}
                  type="button"
                  className={inbox.row}
                  aria-pressed={messageId === message.id}
                  onClick={() => chooseMessage(message)}
                >
                  <strong>{message.sender}</strong>
                  <span>{message.subject}</span>
                  <span>{message.preview}</span>
                  <small>{message.reason}</small>
                </button>
              ))
            : (tab === "drafts" ? editable : waiting).map((value) => (
                <button
                  key={value.id}
                  type="button"
                  className={inbox.row}
                  aria-pressed={draft?.id === value.id}
                  onClick={() => chooseDraft(value)}
                >
                  <strong>{value.subject}</strong>
                  <span>{value.to.join(", ")}</span>
                  <small>
                    {tab === "waiting"
                      ? value.status === "completed" && value.verified_at
                        ? "Sent confirmed · later replies are not monitored"
                        : "Check the outcome before any further action"
                      : "Saved draft — not sent"}
                  </small>
                </button>
              ))}
          {!loading && !error && tab === "needs" && !needsReply.length && (
            <p>
              No messages needing a reply were found in the checked Outlook
              messages.
            </p>
          )}
          {!loading && tab === "drafts" && !editable.length && (
            <p>No saved Outlook drafts.</p>
          )}
          {!loading && tab === "waiting" && !waiting.length && (
            <p>No Outlook replies are waiting.</p>
          )}
        </div>
        <div className={inbox.detail}>
          {selected ? (
            <article className={inbox.message}>
              <p className={inbox.sender}>{selected.sender}</p>
              <h3>{selected.subject}</h3>
              <p>{when(selected.sent_at)}</p>
              <div className={styles.body}>
                {selected.body || "Open Outlook to read this message."}
              </div>
              {selected.truncated && (
                <p>
                  This message was shortened here. Read the complete email in
                  Outlook before replying.
                </p>
              )}
              {selected.open_url && (
                <a
                  href={selected.open_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open in Outlook ↗
                </a>
              )}
            </article>
          ) : messageId ? (
            <p role="status">Opening the selected email…</p>
          ) : (
            <p>Choose an email to read and reply.</p>
          )}
          {(selected || draft) && (
            <OutlookReplyEditor
              key={`${draft?.id ?? selected?.id}:${messageRetry}`}
              accountKey={accountKey}
              connection={connection}
              timezone={timezone}
              message={selected}
              initialDraft={draft}
              onChanged={updated}
            />
          )}
        </div>
      </div>
    </>
  );
}
