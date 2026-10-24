"use client";
import { courseColor } from "@navox/ui";
import Link from "next/link";
import { type CSSProperties, useEffect, useState } from "react";
import {
  addStudentDays,
  type StudentCourse,
  type StudentMeeting,
  type StudentTimetable,
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentLocation,
} from "../lib/student-schedule";
import { studentLocalTime } from "../lib/student-time";
import styles from "./classes-redesign.module.css";
import { useVoxRequest } from "./use-vox-request";

export function mondayOf(date: string) {
  const weekday = new Date(`${date}T12:00:00Z`).getUTCDay();
  return addStudentDays(date, -((weekday + 6) % 7));
}
type Event = {
  id: string;
  start: string;
  end: string;
  title: string;
  room: string;
  courseId?: string;
  shift: boolean;
  cancelled: boolean;
};
export function ClassesWeek({
  timetable,
  onEdit,
  onShift,
}: {
  timetable: StudentTimetable;
  onEdit: (course: StudentCourse, meeting?: StudentMeeting) => void;
  onShift: (id: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const vox = useVoxRequest();
  // biome-ignore lint/correctness/useExhaustiveDependencies: Restart elapsed time when the server clock snapshot changes.
  useEffect(() => {
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Date.now() - started), 30000);
    return () => clearInterval(timer);
  }, [timetable.server_now]);
  const now = Date.parse(timetable.server_now) + elapsed;
  const local = studentLocalTime(now, timetable.timezone);
  const events: Event[] = [
    ...timetable.occurrences.map((row) => ({
      id: row.id,
      start: row.start_at,
      end: row.end_at,
      title: row.course_code || row.course_name,
      room: studentLocation(row),
      courseId: row.course_id,
      shift: false,
      cancelled: row.cancelled,
    })),
    ...timetable.fixed_occurrences.map((row) => ({
      id: row.id,
      start: row.start_at,
      end: row.end_at,
      title: row.title,
      room: `${studentClock(row.start_at, timetable.timezone)} – ${studentClock(row.end_at, timetable.timezone)}`,
      shift: true,
      cancelled: false,
    })),
  ].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const selected =
    timetable.occurrences.find((row) => row.id === selectedId) ??
    timetable.occurrences.find(
      (row) => !row.cancelled && Date.parse(row.end_at) > now,
    ) ??
    timetable.occurrences.find((row) => !row.cancelled);
  const course = timetable.courses.find(
    (row) => row.id === selected?.course_id,
  );
  const meeting = course?.meetings.find(
    (row) => row.id === selected?.meeting_id,
  );
  const minute = (date: string) => {
    const value = studentLocalTime(date, timetable.timezone);
    return value.hour * 60 + value.minute;
  };
  const startHour = Math.min(
    8,
    ...events.map((row) => Math.floor(minute(row.start) / 60)),
  );
  const endHour = Math.max(
    22,
    ...events.map((row) => Math.ceil(minute(row.end) / 60)),
  );
  const height = (endHour - startHour) * 64;
  const days = Array.from({ length: 7 }, (_, i) =>
    addStudentDays(timetable.date, i),
  );
  const firstClass = selected
    ? timetable.occurrences.filter(
        (row) =>
          row.course_id === selected.course_id &&
          row.kind === "lab" &&
          !row.cancelled,
      )[0]
    : undefined;
  return (
    <div className={styles.weekLayout}>
      <div className={styles.weekMain}>
        <section
          className={styles.weekScroll}
          aria-label="Weekly timetable; scroll sideways on a small screen"
        >
          <div className={styles.weekGrid}>
            <div className={styles.weekHead}>
              <span />
              {days.map((day) => (
                <div key={day} data-today={day === local.date}>
                  <span>
                    {new Intl.DateTimeFormat("en", {
                      weekday: "short",
                      timeZone: "UTC",
                    }).format(new Date(`${day}T12:00:00Z`))}
                  </span>
                  <strong>{Number(day.slice(-2))}</strong>
                </div>
              ))}
            </div>
            <div className={styles.weekBody} style={{ height }}>
              <div className={styles.hours}>
                {Array.from(
                  { length: endHour - startHour + 1 },
                  (_, i) => startHour + i,
                ).map((hour) => (
                  <span key={hour} style={{ top: (hour - startHour) * 64 }}>
                    {hour % 12 || 12} {hour < 12 ? "AM" : "PM"}
                  </span>
                ))}
              </div>
              {days.map((day) => {
                const rows = events.filter(
                  (row) =>
                    studentDateInZone(row.start, timetable.timezone) === day,
                );
                const laneEnds: number[] = [];
                const placed = rows.map((row) => {
                  const start = minute(row.start);
                  const finish = Math.max(minute(row.end), start + 42);
                  let lane = laneEnds.findIndex((end) => end <= start);
                  if (lane < 0) lane = laneEnds.length;
                  laneEnds[lane] = finish;
                  return { row, lane };
                });
                const lanes = Math.max(1, laneEnds.length);
                return (
                  <div
                    key={day}
                    className={styles.dayColumn}
                    data-today={day === local.date}
                  >
                    {placed.map(({ row, lane }) => (
                      <button
                        type="button"
                        key={row.id}
                        className={styles.classBlock}
                        data-shift={row.shift}
                        data-cancelled={row.cancelled}
                        aria-pressed={!row.shift && selected?.id === row.id}
                        title={`${row.title} · ${studentClock(row.start, timetable.timezone)} – ${studentClock(row.end, timetable.timezone)} · ${row.room}`}
                        style={
                          {
                            top:
                              ((minute(row.start) - startHour * 60) / 60) * 64,
                            height: Math.max(
                              44,
                              ((minute(row.end) - minute(row.start)) / 60) *
                                64 -
                                4,
                            ),
                            left: `calc(${(lane / lanes) * 100}% + 4px)`,
                            width: `calc(${100 / lanes}% - 8px)`,
                            "--class-color": row.courseId
                              ? courseColor(row.courseId)
                              : "var(--nx-warning)",
                          } as CSSProperties
                        }
                        onClick={() =>
                          row.shift
                            ? onShift(
                                timetable.fixed_occurrences.find(
                                  (f) => f.id === row.id,
                                )?.fixed_block_id ?? "",
                              )
                            : setSelectedId(row.id)
                        }
                      >
                        <strong>{row.title}</strong>
                        <small>{row.room}</small>
                        {row.cancelled && <small>Cancelled</small>}
                      </button>
                    ))}
                    {day === local.date &&
                      local.hour >= startHour &&
                      local.hour < endHour && (
                        <div
                          className={styles.nowLine}
                          style={{
                            top:
                              ((local.hour * 60 +
                                local.minute -
                                startHour * 60) /
                                60) *
                              64,
                          }}
                          role="img"
                          aria-label={`Now: ${local.clock}`}
                        />
                      )}
                  </div>
                );
              })}
            </div>
          </div>
        </section>
        <div className={styles.legend}>
          {timetable.courses
            .filter((row) => !row.archived)
            .map((row) => (
              <span key={row.id}>
                <i style={{ background: courseColor(row.id) }} />
                {row.code || row.name}
              </span>
            ))}
          <span>
            <i className={styles.shiftKey} />
            Shifts
          </span>
        </div>
      </div>
      {selected && course ? (
        <aside className={styles.detail} aria-label="Selected class">
          <p className={styles.relative}>
            {selected.cancelled
              ? "Cancelled"
              : Date.parse(selected.start_at) > now
                ? `${studentDateInZone(selected.start_at, timetable.timezone) === local.date ? "Today" : "Upcoming"} · in ${Math.ceil((Date.parse(selected.start_at) - now) / 60000)} min`
                : Date.parse(selected.end_at) > now
                  ? "Happening now"
                  : studentDayLabel(
                      studentDateInZone(selected.start_at, timetable.timezone),
                    )}
          </p>
          <h2>
            {course.code && `${course.code} · `}
            {course.name}
          </h2>
          <p className={styles.kind}>{selected.kind}</p>
          <dl>
            <div>
              <dt>When</dt>
              <dd>
                {studentDayLabel(
                  studentDateInZone(selected.start_at, timetable.timezone),
                )}{" "}
                · {studentClock(selected.start_at, timetable.timezone)} –{" "}
                {studentClock(selected.end_at, timetable.timezone)}
              </dd>
            </div>
            <div>
              <dt>Where</dt>
              <dd>
                {studentLocation(selected)}
                {selected.walk_minutes != null &&
                  ` · ${selected.walk_minutes} min walk`}
              </dd>
            </div>
            <div>
              <dt>Professor</dt>
              <dd>{selected.instructor || course.professor || "Not added"}</dd>
            </div>
            <div>
              <dt>Office hours</dt>
              <dd>Not added</dd>
            </div>
            <div>
              <dt>Lab</dt>
              <dd>
                {firstClass
                  ? `${studentDayLabel(studentDateInZone(firstClass.start_at, timetable.timezone))} · ${studentClock(firstClass.start_at, timetable.timezone)} – ${studentClock(firstClass.end_at, timetable.timezone)} · ${studentLocation(firstClass)}`
                  : "No lab saved this week"}
              </dd>
            </div>
          </dl>
          <div className={styles.detailActions}>
            <Link
              className="nx-button"
              data-variant="primary"
              href={`/classes/${course.id}`}
            >
              Open course
            </Link>
            <button type="button" onClick={() => onEdit(course, meeting)}>
              Edit
            </button>
          </div>
          <button
            type="button"
            className={styles.ask}
            disabled={!vox.available || vox.busy}
            onClick={() =>
              void vox.ask(
                `Tell me about my saved class ${JSON.stringify(`${course.code} ${course.name}`)}. Use my real timetable and course information.`,
              )
            }
          >
            Ask Vox about this class
          </button>
          {vox.notice && <p role="status">{vox.notice}</p>}
        </aside>
      ) : (
        <aside className={styles.detail}>
          <h2>Your week</h2>
          <p>
            No classes in this week. Use the arrows to see another week, or add
            a class.
          </p>
        </aside>
      )}
    </div>
  );
}
