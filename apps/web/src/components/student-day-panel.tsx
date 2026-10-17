"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  readStudentLookup,
  type StudentLookup,
  type StudentOccurrence,
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentError,
  studentLocation,
  studentRequest,
} from "../lib/student-schedule";
import styles from "./student-schedule.module.css";

export function StudentClassSummary({
  occurrence,
  timezone,
}: {
  occurrence: StudentOccurrence;
  timezone: string;
}) {
  return (
    <div className={styles.classSummary}>
      <strong>{occurrence.course_code || occurrence.course_name}</strong>
      {occurrence.course_code && <span>{occurrence.course_name}</span>}
      <span>
        {studentClock(occurrence.start_at, timezone)}–
        {studentClock(occurrence.end_at, timezone)} · {occurrence.kind}
      </span>
      <span>{studentLocation(occurrence)}</span>
      {occurrence.exception && <small>One-day change</small>}
    </div>
  );
}

export function StudentDaySummary({
  now,
  next,
  loading = false,
  error = "",
  onRefresh,
}: {
  now: StudentLookup | null;
  next: StudentLookup | null;
  loading?: boolean;
  error?: string;
  onRefresh: () => void;
}) {
  const current = now?.occurrences.filter((row) => !row.cancelled) ?? [];
  const upcoming =
    next?.occurrences.find((row) => !row.cancelled) ??
    next?.next_upcoming ??
    null;
  return (
    <section
      className={styles.dayPanel}
      aria-labelledby="student-day-title"
      aria-busy={loading}
    >
      <header className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Your classes</p>
          <h2 id="student-day-title">A little clarity for your day.</h2>
        </div>
        <Link className={styles.textLink} href="/classes">
          Open timetable <span aria-hidden="true">↗</span>
        </Link>
      </header>
      {error && (
        <p className={styles.error} role="alert">
          {now
            ? "The last checked timetable is shown. "
            : "Your classes could not be checked. "}
          {error}{" "}
          <button type="button" disabled={loading} onClick={onRefresh}>
            Try again
          </button>
        </p>
      )}
      {!now && loading ? (
        <p className={styles.muted} role="status">
          Checking the time and your saved timetable…
        </p>
      ) : now && next ? (
        <>
          <div className={styles.nowNext}>
            <div className={styles.nowCard}>
              <span className={styles.pill}>
                Now · {studentClock(now.server_now, now.timezone)}
              </span>
              {current.length > 0 ? (
                current.map((row) => (
                  <StudentClassSummary
                    key={row.id}
                    occurrence={row}
                    timezone={now.timezone}
                  />
                ))
              ) : (
                <div className={styles.classSummary}>
                  <strong>
                    {now.reason === "break"
                      ? "You’re on a class break."
                      : "No class at the moment."}
                  </strong>
                  <span>Based on your saved timetable.</span>
                </div>
              )}
            </div>
            <div className={styles.nextCard}>
              <span className={styles.pill}>Up next</span>
              {upcoming ? (
                <>
                  <StudentClassSummary
                    occurrence={upcoming}
                    timezone={next.timezone}
                  />
                  {!next.occurrences.length && (
                    <small>
                      {studentDayLabel(
                        studentDateInZone(upcoming.start_at, next.timezone),
                      )}
                    </small>
                  )}
                </>
              ) : (
                <div className={styles.classSummary}>
                  <strong>No next class saved.</strong>
                  <span>
                    Add your classes, even if your school isn’t connected.
                  </span>
                </div>
              )}
            </div>
          </div>
          {(now.conflicts.length > 0 || next.conflicts.length > 0) && (
            <p className={styles.warning}>
              Some saved times overlap.{" "}
              <Link href="/classes">Review your timetable</Link> before heading
              out.
            </p>
          )}
          <footer className={styles.panelFoot}>
            <span>
              {now.timezone.replaceAll("_", " ")} · checked at{" "}
              <time dateTime={now.server_now}>
                {studentClock(now.server_now, now.timezone)}
              </time>
            </span>
            <button type="button" disabled={loading} onClick={onRefresh}>
              {loading ? "Checking…" : "Refresh"}
            </button>
          </footer>
        </>
      ) : !loading && !error ? (
        <p className={styles.muted}>Your saved timetable will appear here.</p>
      ) : null}
    </section>
  );
}

function StudentDayPanelAccount() {
  const [data, setData] = useState<{
    now: StudentLookup;
    next: StudentLookup;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const load = useCallback(async () => {
    const request = ++generation.current;
    setLoading(true);
    try {
      const [now, next] = await Promise.all([
        studentRequest("/lookup?mode=now", readStudentLookup),
        studentRequest("/lookup?mode=next", readStudentLookup),
      ]);
      if (request === generation.current) {
        setData({ now, next });
        setError("");
      }
    } catch (cause) {
      if (request === generation.current) setError(studentError(cause));
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 60_000);
    const visible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      ++generation.current;
      clearInterval(interval);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [load]);
  return (
    <StudentDaySummary
      now={data?.now ?? null}
      next={data?.next ?? null}
      loading={loading}
      error={error}
      onRefresh={() => void load()}
    />
  );
}
export function StudentDayPanel({ accountId }: { accountId: string }) {
  return <StudentDayPanelAccount key={accountId} />;
}
