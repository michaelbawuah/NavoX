"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  type OutlookConnection,
  type OutlookDraft,
  type OutlookMessageDetail,
  outlookRequest,
  readOutlookDraft,
  readOutlookGenerated,
} from "../lib/outlook";
import { productFeatures } from "../lib/product-features";
import { InboxMeetingCheck } from "./inbox-meeting-check";
import styles from "./outlook.module.css";

export function outlookSendKey(accountKey: string, draftId: string) {
  return `navox.outlook.send.v1:${accountKey}:${draftId}`;
}
/** A prepared draft is never a send. Uncertain sends expose only receipt checking. */
export function OutlookReplyEditor({
  accountKey,
  connection,
  timezone,
  message,
  initialDraft,
  onChanged,
}: {
  accountKey: string;
  connection: OutlookConnection;
  timezone: string;
  message: OutlookMessageDetail | null;
  initialDraft: OutlookDraft | null;
  onChanged: (draft: OutlookDraft | null) => void;
}) {
  const [draft, setDraft] = useState(initialDraft);
  const [subject, setSubject] = useState(
    initialDraft?.subject ??
      (message?.subject ? `Re: ${message.subject}`.slice(0, 256) : ""),
  );
  const [body, setBody] = useState(initialDraft?.body ?? "");
  const [instructions, setInstructions] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(() => {
    if (!initialDraft) return false;
    try {
      return (
        sessionStorage.getItem(outlookSendKey(accountKey, initialDraft.id)) !==
        null
      );
    } catch {
      return false;
    }
  });
  const [discard, setDiscard] = useState(false);
  const live = useRef(false),
    operation = useRef(false);
  const previewRequest = useRef<{ content: string; id: string } | null>(null);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const verified = draft?.status === "completed" && !!draft.verified_at;
  const waiting =
    !verified &&
    (uncertain ||
      (!!draft &&
        ["executing", "uncertain", "completed"].includes(draft.status)));
  const contentAvailable = draft?.content_available !== false;
  const editable =
    contentAvailable &&
    !waiting &&
    !verified &&
    (!draft || draft.status === "review");
  const exact = !!draft && draft.subject === subject && draft.body === body;
  const unexpired =
    !!draft?.expires_at && Date.parse(draft.expires_at) > Date.now();
  const sourceId = draft?.source_message_id ?? message?.id;
  function apply(value: OutlookDraft) {
    setDraft(value);
    setSubject(value.subject);
    setBody(value.body);
    setConfirmed(false);
    if (value.status === "completed" && value.verified_at) {
      setUncertain(false);
      try {
        sessionStorage.removeItem(outlookSendKey(accountKey, value.id));
      } catch {}
    }
    onChanged(value);
  }
  async function run(action: () => Promise<void>, failed: string) {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      if (live.current)
        setError(cause instanceof Error ? cause.message : failed);
    } finally {
      operation.current = false;
      if (live.current) setBusy(false);
    }
  }
  async function generate(shorten = false) {
    if (!sourceId || !editable) return;
    await run(async () => {
      const value = await outlookRequest(
        accountKey,
        "/generate",
        readOutlookGenerated,
        "POST",
        {
          connection_id: connection.id,
          source_message_id: sourceId,
          instructions: shorten
            ? "Shorten this reply while keeping its meaning. Do not add commitments, availability or personal reasons."
            : instructions.trim() ||
              "Draft a brief reply using only the email. Ask for missing details; do not invent availability or personal reasons.",
          ...(body.trim() ? { previous: { subject, body } } : {}),
        },
      );
      if (!live.current) return;
      setSubject(value.subject);
      setBody(value.body);
      setConfirmed(false);
      setNotice("Suggested reply ready to edit. Nothing has been sent.");
    }, "A draft could not be generated. You can write your reply below.");
  }
  async function review() {
    if (!sourceId || !editable || !subject.trim() || !body.trim()) return;
    await run(async () => {
      const content = JSON.stringify({
        connection_id: connection.id,
        source_message_id: sourceId,
        subject,
        body,
      });
      if (previewRequest.current?.content !== content)
        previewRequest.current = { content, id: crypto.randomUUID() };
      const value = draft
        ? await outlookRequest(
            accountKey,
            `/drafts/${encodeURIComponent(draft.id)}`,
            readOutlookDraft,
            "PATCH",
            { expected_revision: draft.revision, subject, body },
          )
        : await outlookRequest(
            accountKey,
            "/drafts/preview",
            readOutlookDraft,
            "POST",
            {
              request_id: previewRequest.current.id,
              connection_id: connection.id,
              source_message_id: sourceId,
              subject,
              body,
            },
          );
      if (live.current) {
        apply(value);
        setNotice(
          "Review the exact recipient and reply, then approve Send when ready.",
        );
      }
    }, "Your draft could not be saved. Reopen it before trying again.");
  }
  async function send() {
    if (
      !draft ||
      !editable ||
      !exact ||
      !confirmed ||
      !unexpired ||
      !connection.send_enabled
    )
      return;
    if (!draft.expires_at || Date.parse(draft.expires_at) <= Date.now()) {
      setConfirmed(false);
      setError("This review expired. Review the reply again before sending.");
      return;
    }
    const value = draft;
    await run(async () => {
      const requestId = crypto.randomUUID();
      try {
        sessionStorage.setItem(outlookSendKey(accountKey, value.id), requestId);
      } catch {
        throw new Error(
          "This browser cannot remember a pending send. Open Outlook to send safely.",
        );
      }
      setUncertain(true);
      setConfirmed(false);
      onChanged({
        ...value,
        status: "uncertain",
        notice:
          "The send request is pending. Check the outcome before taking another action.",
      });
      try {
        const result = await outlookRequest(
          accountKey,
          `/drafts/${encodeURIComponent(value.id)}/send`,
          readOutlookDraft,
          "POST",
          {
            request_id: requestId,
            expected_revision: value.revision,
            expected_payload_hash: value.payload_hash,
            confirmed: true,
          },
        );
        if (live.current) apply(result);
      } catch {
        throw new Error(
          "The send outcome is not confirmed. Check the outcome or your Sent folder before taking another action.",
        );
      }
    }, "The send outcome is not confirmed.");
  }
  async function check() {
    if (!draft) return;
    await run(async () => {
      const result = await outlookRequest(
        accountKey,
        `/drafts/${encodeURIComponent(draft.id)}/check`,
        readOutlookDraft,
        "POST",
        {},
      );
      if (live.current) {
        apply(result);
        if (result.status !== "completed" || !result.verified_at)
          setNotice(
            "Delivery is still unconfirmed. Checking the outcome does not send the message again.",
          );
      }
    }, "The send outcome could not be checked. Inspect your Sent folder in Outlook.");
  }
  async function remove() {
    if (!draft || !editable) return;
    await run(async () => {
      await outlookRequest(
        accountKey,
        `/drafts/${encodeURIComponent(draft.id)}`,
        () => undefined,
        "DELETE",
      );
      if (live.current) {
        setDraft(null);
        setBody("");
        setConfirmed(false);
        setDiscard(false);
        onChanged(null);
        setNotice("Draft discarded. No email was sent.");
      }
    }, "The draft could not be discarded.");
  }
  return (
    <section className={styles.editor} aria-label="Outlook reply review">
      <h3>
        {verified
          ? "Reply sent"
          : waiting
            ? "Check your reply’s outcome"
            : !contentAvailable
              ? "Reply content cleared"
              : "Prepare your reply"}
      </h3>
      {verified ? (
        <p role="status">
          Sent confirmed in Outlook. The sent receipt was verified. Later
          replies are not monitored.
        </p>
      ) : waiting ? (
        <div className={styles.confirm}>
          <p>
            The send outcome has not been verified. Check the receipt or your
            Sent folder before taking another action.
          </p>
          <button type="button" disabled={busy} onClick={() => void check()}>
            Check outcome
          </button>
        </div>
      ) : null}
      {editable && (
        <>
          {productFeatures.studentCalendarAvailability && (
            <InboxMeetingCheck
              accountKey={accountKey}
              timezone={timezone}
              disabled={busy}
              onUseTimes={(value) => {
                setInstructions(value);
                setConfirmed(false);
              }}
            />
          )}
          <label>
            What should the reply say?{" "}
            <input
              value={instructions}
              maxLength={2000}
              disabled={busy}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="Optional context for your reply"
            />
          </label>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={busy || !sourceId}
              onClick={() => void generate()}
            >
              Draft reply
            </button>
            <button
              type="button"
              disabled={busy || !body.trim()}
              onClick={() => void generate(true)}
            >
              Shorten
            </button>
          </div>
        </>
      )}
      {contentAvailable && (
        <>
          <p>
            <strong>To:</strong>{" "}
            {draft?.to.join(", ") ??
              message?.sender ??
              "Check the original email"}
          </p>
          <label>
            Subject{" "}
            <input
              value={subject}
              maxLength={256}
              disabled={busy || !editable}
              onChange={(e) => {
                setSubject(e.target.value);
                setConfirmed(false);
              }}
            />
          </label>
          <label>
            Reply{" "}
            <textarea
              value={body}
              maxLength={12000}
              disabled={busy || !editable}
              onChange={(e) => {
                setBody(e.target.value);
                setConfirmed(false);
              }}
            />
          </label>
        </>
      )}
      {draft?.notice && <p className={styles.hint}>{draft.notice}</p>}
      {editable && (
        <>
          <p className={styles.hint}>
            Only times you explicitly check are verified against your connected
            schedule. Check again if plans change before sending.
          </p>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={busy || !subject.trim() || !body.trim()}
              onClick={() => void review()}
            >
              {exact && unexpired ? "Review again" : "Save and review reply"}
            </button>
            {draft && (
              <button
                type="button"
                disabled={busy}
                onClick={() => setDiscard(true)}
              >
                Discard draft
              </button>
            )}
          </div>
          {draft && exact && unexpired && (
            <div className={styles.confirm}>
              <p>
                Send the exact reply above to{" "}
                <strong>{draft.to.join(", ")}</strong>?
              </p>
              <label className={styles.check}>
                <input
                  type="checkbox"
                  checked={confirmed}
                  disabled={busy}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                I checked the recipient and the complete reply.
              </label>
              {connection.send_enabled ? (
                <button
                  type="button"
                  className={styles.send}
                  disabled={busy || !confirmed}
                  onClick={() => void send()}
                >
                  Send this reply
                </button>
              ) : (
                <p>
                  <Link href="/connections?source=outlook#outlook">
                    Enable Outlook sending permission
                  </Link>
                  , then reopen this draft.
                </p>
              )}
            </div>
          )}
          {draft && !unexpired && (
            <p>This draft review expired. Review it again before sending.</p>
          )}
          {discard && (
            <div className={styles.confirm}>
              <p>Discard this saved reply?</p>
              <button
                type="button"
                disabled={busy}
                onClick={() => void remove()}
              >
                Confirm discard
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setDiscard(false)}
              >
                Keep draft
              </button>
            </div>
          )}
        </>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">Checking your request…</p>}
    </section>
  );
}
