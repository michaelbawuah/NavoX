"use client";
import { useEffect, useRef, useState } from "react";
import {
  readTaskCompletion,
  routineRequest,
  type StudyTask,
  type TaskCompletion,
} from "../lib/student-planning";
import { studentClock, studentDateInZone } from "../lib/student-schedule";
import styles from "./today-study-actions.module.css";
export function TaskCompletionReview({
  task,
  accountKey,
  timezone,
  onClose,
  onSaved,
}: {
  task: StudyTask;
  accountKey: string;
  timezone: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [preview, setPreview] = useState<TaskCompletion | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const request = useRef(crypto.randomUUID());
  const alive = useRef(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry requests the same exact task review.
  useEffect(() => {
    let current = true;
    alive.current = true;
    setPreview(null);
    setNotice("");
    routineRequest(
      accountKey,
      "/tasks/" + task.id + "/complete/preview",
      readTaskCompletion,
      "POST",
      { request_id: request.current, revision: task.revision },
    )
      .then((value) => {
        if (current) setPreview(value);
      })
      .catch((error) => {
        if (current)
          setNotice(
            error instanceof Error
              ? error.message
              : "Could not check this task.",
          );
      });
    return () => {
      current = false;
      alive.current = false;
    };
  }, [task.id, task.revision, accountKey, retry]);
  async function approve() {
    if (!preview || busy) return;
    setBusy(true);
    setNotice("");
    try {
      await routineRequest(
        accountKey,
        "/tasks/" + task.id + "/complete/confirm",
        readTaskCompletion,
        "POST",
        {
          request_id: request.current,
          revision: task.revision,
          preview_hash: preview.preview_hash,
        },
      );
      if (alive.current) onSaved();
    } catch (error) {
      if (alive.current) {
        setPreview(null);
        setNotice(
          error instanceof Error
            ? error.message
            : "Could not confirm completion.",
        );
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section className={styles.form} aria-label="Review task completion">
      <h3>Complete {task.title}?</h3>
      {preview ? (
        <>
          <p>
            This marks the task complete and removes these remaining study
            blocks:
          </p>
          {preview.removed_blocks.length ? (
            <ul>
              {preview.removed_blocks.map((row) => (
                <li key={row.id}>
                  {row.title} · {studentDateInZone(row.start_at, timezone)} ·{" "}
                  {studentClock(row.start_at, timezone)}–
                  {studentClock(row.end_at, timezone)}
                </li>
              ))}
            </ul>
          ) : (
            <p>No remaining study blocks.</p>
          )}
          <p>
            Other tasks, study blocks, alarms and calendar events stay as they
            are. This does not record study minutes or a streak.
          </p>
          <button type="button" disabled={busy} onClick={() => void approve()}>
            {busy ? "Saving…" : "Approve completion"}
          </button>
        </>
      ) : !notice ? (
        <p role="status">Checking the affected blocks…</p>
      ) : null}
      {notice && <p role="status">{notice}</p>}
      {!preview && notice && (
        <button type="button" onClick={() => setRetry((v) => v + 1)}>
          Retry review
        </button>
      )}
      <button type="button" disabled={busy} onClick={onClose}>
        Keep task
      </button>
    </section>
  );
}
