"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./inbox-workspace.module.css";

const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** Only visible, action-needed rows fetch live metadata; retained source text is never trusted. */
export function InboxEmailPreview({
  commitmentId,
  accountKey,
  title,
  description,
}: {
  commitmentId: string;
  accountKey: string;
  title: string;
  description: string | null;
}) {
  const element = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [summary, setSummary] = useState<{
    sender: string | null;
    subject: string;
    preview: string;
  } | null>(null);
  useEffect(() => {
    const target = element.current;
    if (!target) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setSummary(null);
    if (!visible) return;
    const controller = new AbortController();
    let live = true;
    const options = {
      credentials: "include" as const,
      cache: "no-store" as const,
      headers: { "X-NavoX-Account": accountKey },
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(35_000)]),
    };
    void fetch(`${api}/communication-drafts/sources/${commitmentId}`, options)
      .then(async (response) => {
        if (!response.ok) throw new Error();
        const sources: unknown = await response.json();
        if (
          !Array.isArray(sources) ||
          sources.length !== 1 ||
          typeof sources[0]?.id !== "string"
        )
          return null;
        const sourceId: string = sources[0].id;
        const detail = await fetch(
          `${api}/communication-drafts/sources/${commitmentId}/${sourceId}/message`,
          options,
        );
        if (!detail.ok) throw new Error();
        const message = await detail.json();
        if (
          message.source_id !== sourceId ||
          typeof message.subject !== "string" ||
          typeof message.body !== "string"
        )
          throw new Error();
        return {
          sender: typeof message.sender === "string" ? message.sender : null,
          subject: message.subject,
          preview: message.body.replace(/\s+/g, " ").slice(0, 160),
        };
      })
      .then((value) => {
        if (live) setSummary(value);
      })
      .catch(() => {
        if (live) setSummary(null);
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [commitmentId, accountKey, visible]);
  return (
    <span ref={element} className={styles.preview}>
      {summary?.sender && <small>{summary.sender}</small>}
      <strong>{summary?.subject || title}</strong>
      {(summary?.preview || description) && (
        <span>{summary?.preview || description?.slice(0, 180)}</span>
      )}
    </span>
  );
}
