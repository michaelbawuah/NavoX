"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import {
  readStudentLookup,
  type StudentLookup,
  studentClock,
  studentDayLabel,
  studentError,
  studentRequest,
} from "../lib/student-schedule";
import { StudentClassSummary } from "./student-day-panel";
import styles from "./student-schedule.module.css";

export function StudentTimeLookup({
  date,
  timezone,
}: {
  date: string;
  timezone: string;
}) {
  const id = useId();
  const [day, setDay] = useState(date);
  const [time, setTime] = useState("");
  const [mode, setMode] = useState<"at" | "day" | "now" | "next">("at");
  const [result, setResult] = useState<StudentLookup | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(
    () => () => {
      ++generation.current;
    },
    [],
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = ++generation.current;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const query = new URLSearchParams({
        mode,
        ...(mode === "at" || mode === "day" ? { date: day } : {}),
        ...(mode === "at" ? { time } : {}),
      });
      const payload = await studentRequest(
        `/lookup?${query}`,
        readStudentLookup,
      );
      if (request === generation.current) setResult(payload);
    } catch (cause) {
      if (request === generation.current) setError(studentError(cause));
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  return (
    <details className={styles.disclosure}>
      <summary>Check a class at any time</summary>
      <form className={styles.form} onSubmit={submit}>
        <fieldset className={styles.fields} disabled={busy}>
          <label htmlFor={`${id}-mode`}>
            What would you like to check?
            <select
              id={`${id}-mode`}
              value={mode}
              onChange={(event) => {
                setMode(event.target.value as typeof mode);
                setResult(null);
              }}
            >
              <option value="at">At a specific time</option>
              <option value="day">A whole day</option>
              <option value="now">What’s on now?</option>
              <option value="next">Next class today</option>
            </select>
          </label>
          {(mode === "at" || mode === "day") && (
            <label htmlFor={`${id}-date`}>
              Date
              <input
                id={`${id}-date`}
                type="date"
                required
                value={day}
                onChange={(event) => {
                  setDay(event.target.value);
                  setResult(null);
                }}
              />
            </label>
          )}
          {mode === "at" && (
            <label htmlFor={`${id}-time`}>
              Time · {timezone.replaceAll("_", " ")}
              <input
                id={`${id}-time`}
                type="time"
                required
                value={time}
                onChange={(event) => {
                  setTime(event.target.value);
                  setResult(null);
                }}
              />
            </label>
          )}
        </fieldset>
        <div className={styles.actions}>
          <button type="submit" disabled={busy}>
            {busy ? "Checking…" : "Check saved classes"}
          </button>
          <small>Reads your timetable using the server clock.</small>
        </div>
      </form>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className={styles.previewCard} role="status">
          <p className={styles.eyebrow}>
            {studentDayLabel(result.date)} ·{" "}
            {result.timezone.replaceAll("_", " ")}
          </p>
          {result.occurrences.length > 0 ? (
            result.occurrences.map((row) => (
              <StudentClassSummary
                occurrence={row}
                timezone={result.timezone}
                key={row.id}
              />
            ))
          ) : (
            <p>
              {result.reason === "break"
                ? "No classes — a saved class break covers this date."
                : result.reason === "after_last_class"
                  ? "No more classes saved for today."
                  : "No classes saved for this time."}
            </p>
          )}
          {result.conflicts.length > 0 && (
            <p className={styles.warning}>
              Saved class times overlap. Review both commitments before deciding
              where to go.
            </p>
          )}
          <p className={styles.hint}>
            Checked at {studentClock(result.server_now, result.timezone)} using
            your saved timetable.
          </p>
        </div>
      )}
    </details>
  );
}
