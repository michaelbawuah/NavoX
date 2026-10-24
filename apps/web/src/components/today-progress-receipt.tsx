"use client";
import { Button, Toast } from "@navox/ui";
import { useEffect, useRef, useState } from "react";
import {
  readStudyPlan,
  type StudyBlock,
  type StudyOutcomePreview,
  type StudyTask,
} from "../lib/student-planning";
import styles from "./today-study-actions.module.css";
export type ProgressReceipt = {
  result: StudyOutcomePreview;
  task: StudyTask;
  block: StudyBlock;
  savedAt: number;
};
const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export function TodayProgressReceipt({
  receipt,
  timezone,
  accountKey,
  onClose,
}: {
  receipt: ProgressReceipt;
  timezone: string;
  accountKey: string;
  onClose: () => void;
}) {
  const [review, setReview] = useState(false),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const section = useRef<HTMLElement>(null),
    alive = useRef(true),
    requestId = useRef(crypto.randomUUID());
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    alive.current = true;
    const timer = window.setTimeout(
      () => close.current(),
      Math.max(0, receipt.savedAt + 300000 - Date.now()),
    );
    return () => {
      alive.current = false;
      window.clearTimeout(timer);
    };
  }, [receipt.savedAt]);
  useEffect(() => {
    if (review) {
      section.current?.focus();
      section.current?.scrollIntoView?.({ block: "start" });
    }
  }, [review]);
  const format = (value: string) =>
    new Intl.DateTimeFormat("en", {
      timeZone: timezone,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(value));
  async function undo() {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch(`${base}/student/planning/undo`, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        signal: AbortSignal.timeout(12000),
        headers: {
          "Content-Type": "application/json",
          "X-NavoX-Account": accountKey,
        },
        body: JSON.stringify({
          request_id: requestId.current,
          outcome_request_id: receipt.result.undo_request_id,
          revision: receipt.result.plan.revision,
        }),
      });
      if (!response.ok)
        throw Error(
          response.status === 409
            ? "Undo is no longer available: the plan changed, a block started, or five minutes passed. Close this review to refresh your plan."
            : "We couldn’t confirm Undo. Retry to check the same request safely.",
        );
      readStudyPlan(await response.json());
      if (alive.current) close.current();
    } catch (error) {
      if (alive.current)
        setNotice(
          error instanceof Error
            ? error.message
            : "Couldn’t confirm Undo. Please try again.",
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  if (!review)
    return (
      <Toast onUndo={() => setReview(true)}>
        Progress saved.
        <Button
          variant="quiet"
          aria-label="Dismiss progress confirmation"
          onClick={onClose}
        >
          Close
        </Button>
      </Toast>
    );
  return (
    <section
      ref={section}
      tabIndex={-1}
      className="nx-card"
      aria-labelledby="undo-heading"
    >
      <div className={styles.form}>
        <h2 id="undo-heading">Review Undo</h2>
        <p>
          Return “{receipt.block.title}” to planned. Restore “
          {receipt.task.title}” to {receipt.task.actual_minutes} minutes studied
          and {receipt.task.remaining_minutes} minutes remaining.
        </p>
        <p>Remove these replacement study blocks:</p>
        {receipt.result.result.blocks.length ? (
          <ul>
            {receipt.result.result.blocks.map((row) => (
              <li key={`${row.task_id}:${row.start_at}`}>
                {row.title} · {format(row.start_at)}–{format(row.end_at)}
              </li>
            ))}
          </ul>
        ) : (
          <p>None.</p>
        )}
        <p>Restore these study blocks:</p>
        <ul>
          {[receipt.block, ...receipt.result.replaced_blocks].map((row) => (
            <li key={row.id}>
              {row.title} · {format(row.start_at)}–{format(row.end_at)}
            </li>
          ))}
        </ul>
        <p>
          Classes, calendar events and alarms will stay as they are. Undo is
          available for five minutes while this plan stays unchanged.
        </p>
        {notice && <p role="alert">{notice}</p>}
        <div className={styles.actions}>
          <button type="button" disabled={busy} onClick={() => void undo()}>
            {busy ? "Restoring…" : "Approve Undo"}
          </button>
          <button type="button" disabled={busy} onClick={onClose}>
            Close review
          </button>
        </div>
      </div>
    </section>
  );
}
