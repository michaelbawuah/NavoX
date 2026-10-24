"use client";
import {
  defaultAlarm,
  type RoutinePreview,
  type StudyPreferences,
} from "../lib/student-planning";
import {
  studentClock,
  studentDateInZone,
  studentWeekdays,
} from "../lib/student-schedule";
import styles from "./routine-alarm-settings.module.css";
export function RoutineChangeReview({
  preview,
  draft,
  busy,
  onApprove,
  onCancel,
}: {
  preview: RoutinePreview;
  draft: StudyPreferences;
  busy: boolean;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const alarm = draft.alarm ?? defaultAlarm,
    wakes = draft.wake_times ?? Array(7).fill(draft.wake_time);
  return (
    <section
      className={styles.review}
      aria-label="Review routine and alarm changes"
    >
      <h3>Review every change</h3>
      <p>
        Study blocks: up to {draft.max_study_minutes} minutes, with{" "}
        {draft.break_minutes}-minute breaks.
      </p>
      <p>
        Focus hours:{" "}
        {draft.focus_windows.length
          ? draft.focus_windows
              .map(
                (w) =>
                  w.weekdays.map((d) => studentWeekdays[d]).join(", ") +
                  " " +
                  w.start_time +
                  "–" +
                  w.end_time,
              )
              .join("; ")
          : "No preferred hours"}
        .
      </p>
      <p>
        Protected time:{" "}
        {draft.never_schedule.length
          ? draft.never_schedule
              .map(
                (w) =>
                  w.weekdays.map((d) => studentWeekdays[d]).join(", ") +
                  " " +
                  w.start_time +
                  "–" +
                  w.end_time,
              )
              .join("; ")
          : "None added"}
        .
      </p>
      {preview.warnings.length > 0 && (
        <p role="alert">
          {preview.warnings.join(" ")} Resolve these timetable warnings before
          approving.
        </p>
      )}
      <p>
        Sleep at {draft.sleep_time}. Routine wake times:{" "}
        {studentWeekdays.map((day, i) => `${day} ${wakes[i]}`).join(" · ")}.
      </p>
      <p>
        Sound: {alarm.sound}. Snooze: {alarm.snooze_minutes} minutes.
        Preparation: {alarm.prep_minutes} minutes. Smart alarm:{" "}
        {alarm.smart ? "on" : "off"}.
      </p>
      <ul>
        {preview.alarm_occurrences.map((row, i) => {
          const old = preview.previous_alarm_occurrences[i];
          const label = (r: typeof row | undefined) =>
            !r?.enabled
              ? "Off"
              : r.scheduled_at
                ? studentClock(r.scheduled_at, preview.timezone)
                : "Needs a time";
          return (
            <li key={row.day}>
              <strong>{row.day}</strong>: {label(old)} → {label(row)}
              {row.enabled && !row.scheduled_at && <span> · {row.reason}</span>}
            </li>
          );
        })}
      </ul>
      {preview.replaced_blocks.length > 0 ? (
        <>
          <p>
            Changing the routine will retire these future study blocks. They
            will need a new plan that you review and approve:
          </p>
          <ul>
            {preview.replaced_blocks.map((block) => (
              <li key={block.id}>
                {block.title} ·{" "}
                {studentDateInZone(block.start_at, preview.timezone)} ·{" "}
                {studentClock(block.start_at, preview.timezone)}–
                {studentClock(block.end_at, preview.timezone)}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>No study blocks change.</p>
      )}
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.primary}
          disabled={
            busy ||
            preview.warnings.length > 0 ||
            preview.alarm_occurrences.some(
              (row) => row.enabled && !row.scheduled_at,
            )
          }
          onClick={onApprove}
        >
          Approve these changes
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Keep current settings
        </button>
      </div>
    </section>
  );
}
