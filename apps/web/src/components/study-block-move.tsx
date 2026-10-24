"use client";
import { useEffect, useRef, useState } from "react";
import {
  readStudyMove,
  routineRequest,
  type StudyBlock,
  type StudyMove,
  studyDeadlineChoices,
  studyLocalDateTime,
} from "../lib/student-planning";
import styles from "./today-study-actions.module.css";
export function StudyBlockMove({
  block,
  accountKey,
  timezone,
  now,
  onSaved,
  suggestedStart,
  onKeepCurrent,
  requireCalendar = false,
  windowEndAt,
  onReviewChange,
}: {
  block: StudyBlock;
  accountKey: string;
  timezone: string;
  now: number;
  onSaved: () => void;
  suggestedStart?: string;
  onKeepCurrent?: () => void;
  requireCalendar?: boolean;
  windowEndAt?: string;
  onReviewChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [local, setLocal] = useState(
    studyLocalDateTime(suggestedStart ?? block.start_at, timezone),
  );
  const [choice, setChoice] = useState(
      suggestedStart ? new Date(suggestedStart).toISOString() : "",
    ),
    [preview, setPreview] = useState<StudyMove | null>(null);
  const request = useRef(crypto.randomUUID()),
    alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onReviewChange?.(open);
    return () => onReviewChange?.(false);
  }, [onReviewChange, open]);
  const choices = (() => {
    try {
      return studyDeadlineChoices(local, timezone);
    } catch {
      return [];
    }
  })();
  const format = (value: string) =>
    new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(value));
  async function submit(confirm: boolean) {
    const start = choices.length === 1 ? choices[0] : choice;
    if (busy || !start || !choices.includes(start)) {
      setNotice(
        "Choose a valid time, including which occurrence if the clock changes.",
      );
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const value = await routineRequest(
        accountKey,
        `/blocks/${block.id}/move/${confirm ? "confirm" : "preview"}`,
        readStudyMove,
        "POST",
        {
          request_id: request.current,
          revision: block.revision,
          start_at: start,
          ...(requireCalendar ? { require_calendar: true } : {}),
          ...(windowEndAt ? { window_end_at: windowEndAt } : {}),
          ...(confirm && preview ? { preview_hash: preview.preview_hash } : {}),
        },
      );
      if (!alive.current) return;
      if (confirm) {
        setOpen(false);
        setPreview(null);
        onSaved();
      } else setPreview(value);
    } catch (error) {
      if (alive.current) {
        if (!confirm) setPreview(null);
        else setUncertain(true);
        setNotice(
          confirm
            ? "Couldn’t confirm the move. Retry this approval to check the same change. Closing this review will not undo a move that may have saved."
            : error instanceof Error
              ? error.message
              : "Couldn’t check this move. Try again.",
        );
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div className={styles.moveRoot}>
      {!open ? (
        <div>
          <button
            type="button"
            disabled={Date.parse(block.start_at) <= now}
            aria-label={`Move: ${block.title}`}
            onClick={() => {
              request.current = crypto.randomUUID();
              setOpen(true);
              setNotice("");
              setUncertain(false);
              if (suggestedStart) void submit(false);
            }}
          >
            {suggestedStart ? "Review suggested move" : "Move"}
          </button>
          {suggestedStart && onKeepCurrent && (
            <button type="button" onClick={onKeepCurrent}>
              Keep it
            </button>
          )}
        </div>
      ) : (
        <form
          className={styles.form}
          aria-label={`Move ${block.title}`}
          onSubmit={(e) => {
            e.preventDefault();
            void submit(!!preview);
          }}
        >
          <strong>Move {block.title}</strong>
          {!preview ? (
            <>
              <label>
                New start time
                <input
                  type="datetime-local"
                  required
                  value={local}
                  disabled={busy}
                  onChange={(e) => {
                    setLocal(e.target.value);
                    setChoice("");
                    request.current = crypto.randomUUID();
                  }}
                />
              </label>
              {choices.length === 2 && (
                <label>
                  This time happens twice
                  <select
                    required
                    value={choice}
                    onChange={(e) => setChoice(e.target.value)}
                  >
                    <option value="">Choose one</option>
                    {choices.map((value) => (
                      <option key={value} value={value}>
                        {format(value)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <p>
                The block stays {block.minutes} minutes long. NavoX will check
                classes, calendar events, travel, sleep and breaks.
              </p>
            </>
          ) : (
            <section aria-label="Review move">
              <p>
                From {format(preview.before.start_at)}–
                {format(preview.before.end_at)}
              </p>
              <p>
                To {format(preview.after.start_at)}–
                {format(preview.after.end_at)}
              </p>
              <p>
                Other study blocks, alarms and calendar events stay unchanged.
              </p>
              {preview.warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </section>
          )}
          <div className={styles.actions}>
            <button type="submit" disabled={busy}>
              {busy ? "Checking…" : preview ? "Approve move" : "Review move"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setPreview(null);
                onKeepCurrent?.();
              }}
            >
              {uncertain ? "Close review" : "Keep current time"}
            </button>
          </div>
        </form>
      )}
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
