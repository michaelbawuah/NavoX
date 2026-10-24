"use client";
import { useEffect, useRef, useState } from "react";
import {
  readStudyOutcomePreview,
  type StudyBlock,
  type StudyOutcomePreview,
  type StudyTask,
} from "../lib/student-planning";
import styles from "./today-study-actions.module.css";

type Kind = "done" | "took_longer" | "skipped";
const labels = {
  done: "Done",
  took_longer: "Took longer",
  skipped: "Skip",
} as const;
const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export function TodayStudyActions({
  block,
  task,
  accountKey,
  timezone,
  now,
  onSaved,
}: {
  block: StudyBlock;
  task: StudyTask;
  accountKey: string;
  timezone: string;
  now: number;
  onSaved: () => void;
}) {
  const [kind, setKind] = useState<Kind | null>(null);
  const [actual, setActual] = useState(block.minutes);
  const [remaining, setRemaining] = useState(
    Math.max(0, task.remaining_minutes - block.minutes),
  );
  const [complete, setComplete] = useState(false);
  const [preview, setPreview] = useState<StudyOutcomePreview | null>(null);
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const request = useRef(crypto.randomUUID());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const format = (value: string) =>
    new Intl.DateTimeFormat("en", {
      timeZone: timezone,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(value));
  async function submit(confirm: boolean) {
    if (!kind || busy) return;
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch(
        `${base}/student/planning/blocks/${block.id}/outcome/${confirm ? "confirm" : "preview"}`,
        {
          method: "POST",
          credentials: "include",
          cache: "no-store",
          signal: AbortSignal.timeout(12000),
          headers: {
            "Content-Type": "application/json",
            "X-NavoX-Account": accountKey,
          },
          body: JSON.stringify({
            request_id: request.current,
            revision: block.revision,
            task_revision: task.revision,
            outcome: kind,
            actual_minutes: kind === "skipped" ? 0 : actual,
            ...(kind === "skipped"
              ? {}
              : {
                  remaining_minutes:
                    complete && kind === "done" ? 0 : remaining,
                  complete_task: kind === "done" && complete,
                }),
            ...(confirm && preview
              ? { preview_hash: preview.preview_hash }
              : {}),
          }),
        },
      );
      if (!alive.current) return;
      if (!response.ok) {
        if (response.status === 409) {
          setPreview(null);
          throw Error(
            "Your plan changed. Review a fresh preview before approving.",
          );
        }
        if (response.status === 422)
          throw Error(
            "Check the minutes. ‘Took longer’ needs some work remaining, and progress can only be recorded after the block starts.",
          );
        throw Error(
          "We couldn’t confirm this update. Try again; the same request will not record progress twice.",
        );
      }
      const result = readStudyOutcomePreview(await response.json());
      if (!alive.current) return;
      if (confirm) {
        onSaved();
        setKind(null);
        setPreview(null);
      } else setPreview(result);
    } catch (error) {
      if (alive.current)
        setNotice(
          error instanceof Error
            ? error.message
            : "Couldn’t reach your plan. Please try again.",
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div className={styles.root}>
      {!kind ? (
        <div className={styles.actions}>
          {(Object.keys(labels) as Kind[]).map((value) => (
            <button
              key={value}
              type="button"
              disabled={value !== "skipped" && Date.parse(block.start_at) > now}
              aria-label={`${labels[value]}: ${block.title}`}
              onClick={() => {
                request.current = crypto.randomUUID();
                setKind(value);
                setNotice("");
              }}
            >
              {labels[value]}
            </button>
          ))}
        </div>
      ) : (
        <form
          className={styles.form}
          aria-label={`Update ${block.title}`}
          onSubmit={(e) => {
            e.preventDefault();
            void submit(!!preview);
          }}
        >
          <strong>
            {labels[kind]} · {block.title}
          </strong>
          {!preview && kind !== "skipped" && (
            <>
              <label>
                Minutes studied
                <input
                  type="number"
                  min="0"
                  max="1440"
                  required
                  value={actual}
                  disabled={busy}
                  onChange={(e) => setActual(Number(e.target.value))}
                />
              </label>
              <label>
                Minutes still needed
                <input
                  type="number"
                  min={kind === "took_longer" ? 1 : 0}
                  max="10080"
                  required
                  value={remaining}
                  disabled={busy || complete}
                  onChange={(e) => setRemaining(Number(e.target.value))}
                />
              </label>
              {kind === "done" && (
                <label className={styles.checkbox}>
                  <input
                    type="checkbox"
                    checked={complete}
                    disabled={busy}
                    onChange={(e) => setComplete(e.target.checked)}
                  />
                  The whole task is finished
                </label>
              )}
            </>
          )}
          {preview && (
            <div className={styles.preview}>
              <p>
                Record this block as {labels[kind].toLowerCase()}. Add{" "}
                {preview.block.actual_minutes} minutes studied;{" "}
                {preview.task.remaining_minutes} minutes will remain on “
                {preview.task.title}”.
              </p>
              <p>Replace these future study blocks:</p>
              {preview.replaced_blocks.length ? (
                <ul>
                  {preview.replaced_blocks.map((row) => (
                    <li key={row.id}>
                      {row.title} · {format(row.start_at)}–{format(row.end_at)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>None.</p>
              )}
              <p>With this schedule:</p>
              {preview.result.blocks.length ? (
                <ul>
                  {preview.result.blocks.map((row) => (
                    <li key={`${row.task_id}:${row.start_at}`}>
                      {row.title} · {format(row.start_at)}–{format(row.end_at)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>No replacement study blocks.</p>
              )}
              {preview.result.unfitted_work.map((row) => (
                <p key={row.task_id}>
                  {row.title}: {row.minutes} minutes could not be scheduled.
                </p>
              ))}
              {preview.result.warnings.map((message) => (
                <p key={message}>{message}</p>
              ))}
              <p>Classes, calendar events and alarms will stay as they are.</p>
            </div>
          )}
          {notice && <p role="alert">{notice}</p>}
          <div className={styles.actions}>
            <button type="submit" disabled={busy}>
              {busy
                ? "Checking…"
                : preview
                  ? "Approve changes"
                  : "Preview changes"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setKind(null);
                setPreview(null);
                setNotice("");
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
