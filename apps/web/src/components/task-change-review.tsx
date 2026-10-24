"use client";
import type { TaskChangePreview } from "../lib/student-planning";
import { studyMinutes } from "../lib/student-planning";
import {
  type StudentCourse,
  studentClock,
  studentDateInZone,
} from "../lib/student-schedule";
import styles from "./student-daily-plan.module.css";

export function TaskChangeReview({
  preview,
  courses = [],
  busy,
  onApprove,
  onCancel,
}: {
  preview: TaskChangePreview;
  courses?: StudentCourse[];
  busy: boolean;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const task = preview.proposed;
  const deadline = (value: string | null) =>
    value
      ? new Intl.DateTimeFormat("en", {
          dateStyle: "medium",
          timeStyle: "short",
          timeZone: preview.timezone,
        }).format(new Date(value))
      : "No deadline";
  return (
    <section className={styles.card} aria-label="Review task changes">
      <h3>{preview.before ? "Review task changes" : "Review your new task"}</h3>
      <p>
        <strong>{task.title}</strong>
        {task.course_id
          ? " · " +
            (preview.course_label ||
              courses.find((c) => c.id === task.course_id)?.code ||
              "Linked course")
          : ""}
      </p>
      {preview.before && (
        <p>
          Before: {preview.before.title} · {deadline(preview.before.due_at)} ·{" "}
          {studyMinutes(preview.before.remaining_minutes)} remaining ·{" "}
          {preview.before.status}.
        </p>
      )}
      <p>
        {deadline(task.due_at)} · {studyMinutes(task.estimated_minutes)}{" "}
        estimated · {studyMinutes(task.remaining_minutes)} remaining ·{" "}
        {task.status}.
      </p>
      <p>
        Priority {task.priority}/5 · Difficulty {task.difficulty}/5 · Course
        weight {task.course_weight}/10.
      </p>
      {preview.replaced_blocks.length ? (
        <>
          <p>
            Saving this task retires these future blocks. Review a new plan
            afterwards to fit the updated work:
          </p>
          <ul>
            {preview.replaced_blocks.map((row) => (
              <li key={row.id}>
                {row.title} ·{" "}
                {studentDateInZone(row.start_at, preview.timezone)} ·{" "}
                {studentClock(row.start_at, preview.timezone)}–
                {studentClock(row.end_at, preview.timezone)}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>No existing study blocks will be removed.</p>
      )}
      <p>Classes, calendar events and alarms stay as they are.</p>
      <div className={styles.actions}>
        <button type="button" disabled={busy} onClick={onApprove}>
          {busy ? "Saving…" : "Approve task changes"}
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Back to task
        </button>
      </div>
    </section>
  );
}
