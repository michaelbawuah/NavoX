"use client";

import { useEffect, useRef, useState } from "react";
import {
  type OutlookDraft,
  outlookRequest,
  readOutlookDraft,
} from "../lib/outlook";
import styles from "./outlook.module.css";

interface Props {
  accountKey: string;
  draft: OutlookDraft;
  timezone: string;
  onChanged: (value: OutlookDraft) => void;
}
export function OutlookReplyTracking(props: Props) {
  return (
    <ReplyTrackingAccount
      key={`${props.accountKey}:${props.draft.id}`}
      {...props}
    />
  );
}
function ReplyTrackingAccount({
  accountKey,
  draft,
  timezone,
  onChanged,
}: Props) {
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  if (draft.status !== "completed" || !draft.verified_at) return null;
  function when(value: string | null | undefined) {
    return value
      ? new Intl.DateTimeFormat("en", {
          dateStyle: "medium",
          timeStyle: "short",
          timeZone: timezone,
        }).format(new Date(value))
      : null;
  }
  async function check() {
    pending.current?.abort();
    const abort = new AbortController();
    pending.current = abort;
    setChecking(true);
    setError("");
    try {
      const result = await outlookRequest(
        accountKey,
        `/drafts/${encodeURIComponent(draft.id)}/check-replies`,
        readOutlookDraft,
        "POST",
        {},
        abort.signal,
      );
      if (abort.signal.aborted) return;
      if (
        result.id !== draft.id ||
        result.source_message_id !== draft.source_message_id ||
        result.status !== "completed" ||
        !result.verified_at
      )
        throw new Error(
          "The reply record changed. Reopen it before checking again.",
        );
      onChanged(result);
    } catch (cause) {
      if (!abort.signal.aborted)
        setError(
          cause instanceof Error
            ? cause.message
            : "This reply check could not finish. Try again.",
        );
    } finally {
      if (!abort.signal.aborted) setChecking(false);
    }
  }
  const state = draft.reply_state ?? "not_checked";
  return (
    <section className={styles.connection} aria-label="Outlook reply status">
      <h3>
        {state === "replied"
          ? "Reply found"
          : state === "waiting"
            ? "Waiting for a reply"
            : "Reply status needs checking"}
      </h3>
      <p>{draft.reply_notice ?? "Later replies have not been checked."}</p>
      {draft.sent_at && (
        <p className={styles.hint}>Sent {when(draft.sent_at)}</p>
      )}
      {state === "replied" && draft.reply_received_at && (
        <p>Reply received {when(draft.reply_received_at)}</p>
      )}
      {draft.reply_checked_at && (
        <p className={styles.hint}>
          Last checked {when(draft.reply_checked_at)}
          {draft.reply_scan_count !== null &&
          draft.reply_scan_count !== undefined
            ? ` · ${draft.reply_scan_count} message${draft.reply_scan_count === 1 ? "" : "s"} checked`
            : ""}
          {draft.reply_scan_complete === false ? " · incomplete check" : ""}
        </p>
      )}
      <p className={styles.hint}>
        Checks read up to 50 later messages in this conversation and require the
        exact reviewed recipient and a reference to this sent reply. NavoX
        checks after send confirmation and when you choose Check for replies.
        While Inbox is visible and no editor is open, up to two current sent
        replies are also checked each minute. Cleared records need an explicit
        check; there is no monitoring while NavoX is closed.
      </p>
      {error && <p role="alert">{error}</p>}
      {state !== "replied" && (
        <button type="button" disabled={checking} onClick={() => void check()}>
          {checking ? "Checking for replies…" : "Check for replies"}
        </button>
      )}
    </section>
  );
}
