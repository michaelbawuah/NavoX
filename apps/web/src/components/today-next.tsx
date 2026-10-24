import Link from "next/link";
import { studentClock } from "../lib/student-schedule";
import type { DayRow } from "../lib/today-home";
import styles from "./today-home.module.css";

export function TodayNext({
  row,
  now,
  timezone,
  label = "Next up",
}: {
  row: DayRow;
  now: number;
  timezone: string;
  label?: string;
}) {
  const minutes = Math.max(0, Math.ceil((Date.parse(row.start) - now) / 60000));
  const circumference = 2 * Math.PI * 38;
  const location = row.courseId ? row.detail : null;
  return (
    <section className={styles.next} aria-label={label}>
      <div
        className={styles.countdown}
        role="img"
        aria-label={`${minutes} minutes until ${row.title}`}
      >
        <svg viewBox="0 0 88 88" aria-hidden="true">
          <circle cx="44" cy="44" r="38" />
          <circle
            cx="44"
            cy="44"
            r="38"
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - Math.min(1, minutes / 60))}
          />
        </svg>
        <span>
          <strong className={styles.countdownNumber}>{minutes}</strong>
          <small className={styles.countdownUnit}>min</small>
        </span>
      </div>
      <div className={styles.nextWords}>
        <p className={styles.nextLabel}>
          ● {label} · starts in {minutes} min
        </p>
        <h2>{row.title}</h2>
        <div className={styles.nextDetails}>
          <span>
            {studentClock(row.start, timezone)}
            {row.end && ` – ${studentClock(row.end, timezone)}`}
          </span>
          {location && <span>{location}</span>}
          {row.walkMinutes !== null && (
            <span className={styles.leaveBy}>
              Leave by{" "}
              {studentClock(
                new Date(
                  Date.parse(row.start) - row.walkMinutes * 60000,
                ).toISOString(),
                timezone,
              )}{" "}
              · {row.walkMinutes} min walk
            </span>
          )}
        </div>
      </div>
      <div className={styles.nextActions}>
        {location && (
          <a
            className="nx-button"
            data-variant="primary"
            href={`https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(location)}&travelmode=walking`}
            target="_blank"
            rel="noreferrer"
          >
            Directions
          </a>
        )}
        <Link className="nx-button" data-variant="secondary" href={row.href}>
          {row.courseId ? "Open course" : "Open schedule"}
        </Link>
      </div>
    </section>
  );
}
