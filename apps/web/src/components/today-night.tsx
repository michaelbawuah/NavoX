"use client";
import Link from "next/link";
import { type AlarmOccurrence, studyMinutes } from "../lib/student-planning";
import { studentClock } from "../lib/student-schedule";
import styles from "./today-home.module.css";
import { useVoxRequest } from "./use-vox-request";

export function TodayNightActions({
  alarm,
  now,
}: {
  alarm: AlarmOccurrence | undefined;
  now: number;
}) {
  const vox = useVoxRequest();
  const until =
    alarm?.enabled && alarm.scheduled_at
      ? Math.max(0, Math.floor((Date.parse(alarm.scheduled_at) - now) / 60000))
      : null;
  return (
    <section className={styles.nightActions} aria-label="Wind down">
      {until !== null && until > 0 && (
        <p>
          If you sleep now, there’s {studyMinutes(until)} until your saved
          wake-up time.
        </p>
      )}
      <div>
        <Link className="nx-button" href="/settings#routine-alarm">
          Set alarm
        </Link>
        <button
          type="button"
          disabled={!vox.available || vox.busy}
          onClick={() =>
            void vox.ask(
              "Help me wind down for sleep. Use my real next class and saved routine, and ask before changing any study blocks or alarms.",
            )
          }
        >
          Wind down with Vox
        </button>
      </div>
      {vox.notice && <p role="status">{vox.notice}</p>}
    </section>
  );
}
export function TodayEveningAlarm({
  alarm,
  timezone,
}: {
  alarm: AlarmOccurrence | undefined;
  timezone: string;
}) {
  if (!alarm) return null;
  return (
    <section className={styles.eveningCard} aria-label="Tomorrow morning">
      <div>
        <h2>Tomorrow morning</h2>
        <p>
          {alarm.enabled && alarm.scheduled_at
            ? `Wake-up plan · ${studentClock(alarm.scheduled_at, timezone)}`
            : "Choose your wake-up time"}
          {alarm.first_class_at &&
            ` · ${alarm.first_class_title} at ${studentClock(alarm.first_class_at, timezone)}`}
        </p>
      </div>
      <Link className="nx-button" href="/settings#routine-alarm">
        Set alarm
      </Link>
    </section>
  );
}
