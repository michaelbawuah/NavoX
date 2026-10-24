"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef, useState } from "react";
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
import { outlookDraftGroups } from "../lib/outlook-replies";
import {
  type OutlookReplyTarget,
  readOutlookReplyTarget,
} from "../lib/outlook-summary";
import { productFeatures } from "../lib/product-features";
import inbox from "./inbox-workspace.module.css";
import styles from "./outlook.module.css";
import { OutlookReplyEditor, outlookSendKey } from "./outlook-reply-editor";
import { OutlookReplyTracking } from "./outlook-reply-tracking";
import { OutlookTaskCapture } from "./outlook-task-capture";
import {
  mergeOutlookReplyReads,
  useOutlookReplyChecks,
} from "./use-outlook-reply-checks";

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
  const [target, setTarget] = useState<OutlookReplyTarget | null>(null);
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
        const hint = readOutlookReplyTarget(window.location.search, accountKey);
        const hintedConnection =
          hint &&
          value.configured &&
          value.connections.some(
            (c) => c.id === hint.connectionId && canReadOutlook(c),
          )
            ? hint
            : null;
        setTarget(hintedConnection);
        setSelected((current) =>
          hintedConnection
            ? hintedConnection.connectionId
            : value.connections.some(
                  (c) => c.id === current && canReadOutlook(c),
                )
              ? current
              : (value.connections.find(canReadOutlook)?.id ?? ""),
        );
        if (new URLSearchParams(window.location.search).has("outlook_reply")) {
          const url = new URL(window.location.href);
          for (const key of [
            "outlook_owner",
            "outlook_mailbox",
            "outlook_reply",
          ])
            url.searchParams.delete(key);
          window.history.replaceState(
            window.history.state,
            "",
            url.pathname + url.search + url.hash,
          );
        }
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
  const connections = status.configured
    ? status.connections.filter(canReadOutlook)
    : [];
  const connection = connections.find((c) => c.id === selected);
  if (!connection) return fallback;
  return (
    <section
      id="outlook-inbox"
      className={styles.card}
      aria-label="Outlook inbox"
    >
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
        initialMessageId={
          target?.connectionId === connection.id ? target.messageId : null
        }
      />
    </section>
  );
}
function OutlookMailbox({
  accountKey,
  connection,
  timezone,
  initialMessageId,
}: {
  accountKey: string;
  connection: OutlookConnection;
  timezone: string;
  initialMessageId: string | null;
}) {
  const [messages, setMessages] = useState<OutlookMessage[]>([]);
  const [drafts, setDrafts] = useState<OutlookDraft[]>([]);
  const [selected, setSelected] = useState<OutlookMessageDetail | null>(null);
  const [draft, setDraft] = useState<OutlookDraft | null>(null);
  const [tab, setTab] = useState<"needs" | "drafts" | "waiting" | "sent">(
    "needs",
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [messageId, setMessageId] = useState("");
  const [messageRetry, setMessageRetry] = useState(0);
  const targetOpened = useRef<string | null>(null);
  const incompleteReplyCheck = useOutlookReplyChecks({
    accountKey,
    connection,
    drafts,
    editorOpen: Boolean(messageId || draft || selected),
    onChecked: (value) =>
      setDrafts((current) =>
        current.map((saved) => (saved.id === value.id ? value : saved)),
      ),
  });
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
        const ownedDrafts = saved
          .filter((d) => d.connection_id === connection.id)
          .map((d) => pendingReply(accountKey, d));
        setMessages(mail.messages);
        setDrafts((current) => mergeOutlookReplyReads(ownedDrafts, current));
        if (initialMessageId && targetOpened.current !== initialMessageId) {
          const related = ownedDrafts.filter(
            (d) =>
              d.source_message_id === initialMessageId &&
              d.status !== "discarded",
          );
          const existing =
            related.find((d) =>
              ["executing", "uncertain", "completed"].includes(d.status),
            ) ??
            related[0] ??
            null;
          setDraft(existing);
          setMessageId(initialMessageId);
          targetOpened.current = initialMessageId;
          if (existing?.status === "completed" && existing.verified_at)
            setTab(existing.reply_state === "replied" ? "sent" : "waiting");
        }
        setNotice(
          [
            mail.notice,
            mail.has_more
              ? "Some messages remain outside this check. Open Outlook for the complete mailbox."
              : "",
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
  }, [accountKey, connection.id, retry, initialMessageId]);
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
  const { editable, waiting, pending, unchecked, replied, sentSources } =
    outlookDraftGroups(drafts);
  const needsReply = messages.filter((message) => !sentSources.has(message.id));
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
    if (value?.status === "completed" && value.verified_at)
      setTab(value.reply_state === "replied" ? "sent" : "waiting");
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
        <button
          type="button"
          aria-pressed={tab === "sent"}
          onClick={() => setTab("sent")}
        >
          Sent replies · {replied.length}
        </button>
      </nav>
      {loading && <p role="status">Checking your Outlook messages…</p>}
      {notice && <p className={styles.hint}>{notice}</p>}
      <p className={styles.hint}>
        While this Inbox is visible and no reply editor is open, up to two
        current sent replies are checked each minute. Each check covers at most
        50 later messages. Older or cleared records need an explicit check.
      </p>
      {incompleteReplyCheck && (
        <p className={styles.hint} role="status">
          Some reply checks could not finish. Open a sent reply and choose Check
          for replies to review its status.
        </p>
      )}
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={() => setRetry((v) => v + 1)}>
            Try again
          </button>
        </p>
      )}
      {pending.length > 0 && (
        <section
          className={styles.connection}
          aria-label="Unconfirmed Outlook sends"
        >
          <h3>Unconfirmed sends · {pending.length}</h3>
          <p className={styles.hint}>
            Check the send outcome before taking another action. These messages
            are not counted as Waiting.
          </p>
          {pending.map((value) => (
            <button
              key={value.id}
              type="button"
              className={inbox.row}
              aria-pressed={draft?.id === value.id}
              onClick={() => chooseDraft(value)}
            >
              <strong>{value.subject}</strong>
              <span>Check send outcome</span>
            </button>
          ))}
        </section>
      )}
      {unchecked.length > 0 && (
        <section
          className={styles.connection}
          aria-label="Outlook reply checks needed"
        >
          <h3>Reply status needs checking · {unchecked.length}</h3>
          <p className={styles.hint}>
            Sending was confirmed. A later recipient reply has not been checked
            or could not be confirmed.
          </p>
          {unchecked.map((value) => (
            <button
              key={value.id}
              type="button"
              className={inbox.row}
              aria-pressed={draft?.id === value.id}
              onClick={() => chooseDraft(value)}
            >
              <strong>{value.subject}</strong>
              <span>
                {value.reply_state === "unknown"
                  ? "Incomplete reply check"
                  : "Check for a later reply"}
              </span>
            </button>
          ))}
        </section>
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
            : (tab === "drafts"
                ? editable
                : tab === "waiting"
                  ? waiting
                  : replied
              ).map((value) => (
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
                      ? `No matching reply at the last check · ${when(value.reply_checked_at ?? null)}`
                      : tab === "sent"
                        ? `Reply received · ${when(value.reply_received_at ?? null)}`
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
            <p>No replies are marked Waiting after the latest checks.</p>
          )}
          {tab === "sent" && (
            <p className={styles.hint}>
              These are replies sent through NavoX with a matching later
              recipient response. This list covers NavoX reply records, not your
              full Outlook Sent folder.
            </p>
          )}
          {!loading && tab === "sent" && !replied.length && (
            <p>No matching later replies have been found yet.</p>
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
          {selected && productFeatures.studentEmail && (
            <OutlookTaskCapture
              accountKey={accountKey}
              connectionId={connection.id}
              message={selected}
              timezone={timezone}
            />
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
          {draft && (
            <OutlookReplyTracking
              key={`${accountKey}:${draft.id}`}
              accountKey={accountKey}
              draft={draft}
              timezone={timezone}
              onChanged={updated}
            />
          )}
        </div>
      </div>
    </>
  );
}
