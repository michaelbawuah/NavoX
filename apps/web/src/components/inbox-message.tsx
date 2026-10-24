"use client";
import { useEffect, useRef, useState } from "react";
import type { GmailMeetingSource } from "../lib/inbox-meeting-proposal";
import {
  studentClock,
  studentDateInZone,
  studentDayLabel,
} from "../lib/student-schedule";
import styles from "./inbox-workspace.module.css";

type Message = {
  subject: string;
  sender: string | null;
  body: string;
  truncated: boolean;
  sent_at: string;
  open_url: string;
  source_id: string;
};
type Source = { id: string; label: string };
const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
interface Props {
  id: string;
  accountKey: string;
  timezone: string;
  onReadSource?: (source: GmailMeetingSource | null) => void;
}
export function InboxMessage(props: Props) {
  return (
    <InboxMessageContent key={`${props.accountKey}:${props.id}`} {...props} />
  );
}
function InboxMessageContent({
  id,
  accountKey,
  timezone,
  onReadSource,
}: Props) {
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState<Message | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const readSource = useRef(onReadSource);
  readSource.current = onReadSource;
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry reloads the exact selected request.
  useEffect(() => {
    const abort = new AbortController();
    let live = true;
    setSources([]);
    setSelected("");
    setMessage(null);
    readSource.current?.(null);
    setError("");
    void fetch(`${api}/communication-drafts/sources/${id}`, {
      credentials: "include",
      cache: "no-store",
      headers: { "X-NavoX-Account": accountKey },
      signal: abort.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw Error();
        const rows = await r.json();
        if (
          !Array.isArray(rows) ||
          rows.some(
            (v) => typeof v.id !== "string" || typeof v.label !== "string",
          )
        )
          throw Error();
        if (live) {
          setSources(rows);
          if (rows.length === 1) setSelected(rows[0].id);
          if (!rows.length)
            setError(
              "This message is no longer available. Check the connection or open your mailbox.",
            );
        }
      })
      .catch(() => {
        if (live) setError("This message couldn’t load.");
      });
    return () => {
      live = false;
      abort.abort();
    };
  }, [id, accountKey, retry]);
  useEffect(() => {
    const abort = new AbortController();
    let live = true;
    setMessage(null);
    readSource.current?.(null);
    if (!selected) return;
    setError("");
    void fetch(
      `${api}/communication-drafts/sources/${id}/${selected}/message`,
      {
        credentials: "include",
        cache: "no-store",
        headers: { "X-NavoX-Account": accountKey },
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35000)]),
      },
    )
      .then(async (r) => {
        if (!r.ok) throw Error();
        const value = await r.json();
        if (
          typeof value.subject !== "string" ||
          typeof value.body !== "string" ||
          typeof value.truncated !== "boolean" ||
          typeof value.sent_at !== "string" ||
          !Number.isFinite(Date.parse(value.sent_at)) ||
          typeof value.open_url !== "string" ||
          !value.open_url.startsWith("https://mail.google.com/") ||
          value.source_id !== selected
        )
          throw Error();
        if (live) {
          setMessage(value);
          readSource.current?.({
            accountKey,
            commitmentId: id,
            sourceId: selected,
            key: `google:${id}:${selected}`,
            subject: value.subject,
            body: value.body,
            sentAt: value.sent_at,
            truncated: value.truncated,
          });
        }
      })
      .catch(() => {
        if (live) setError("This message couldn’t load.");
      });
    return () => {
      live = false;
      abort.abort();
      readSource.current?.(null);
    };
  }, [id, selected, accountKey]);
  return (
    <section className={styles.message} aria-label="Selected email">
      {sources.length > 1 && (
        <label>
          Email
          <select
            value={selected}
            onChange={(e) => {
              setMessage(null);
              readSource.current?.(null);
              setSelected(e.target.value);
            }}
          >
            <option value="">Choose the message</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {error ? (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={() => setRetry((n) => n + 1)}>
            Retry
          </button>
        </p>
      ) : !message ? (
        <p role="status">
          {selected ? "Opening your email…" : "Choose an email to read."}
        </p>
      ) : (
        <>
          <p className={styles.sender}>
            {message.sender || "Sender not provided"}
          </p>
          <h2>{message.subject}</h2>
          <p>
            {studentDayLabel(studentDateInZone(message.sent_at, timezone))} ·{" "}
            {studentClock(message.sent_at, timezone)}
          </p>
          <div className={styles.body}>
            {message.body ||
              "This message has no plain text. Open it in Gmail to read it."}
          </div>
          {message.truncated && <p>The message continues in Gmail.</p>}
          <a href={message.open_url} target="_blank" rel="noopener noreferrer">
            Open in Gmail ↗
          </a>
        </>
      )}
    </section>
  );
}
