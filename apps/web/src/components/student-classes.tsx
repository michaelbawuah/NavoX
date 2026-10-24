"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  notifyAccountSessionChanged,
  notifyAccountSessionChanging,
} from "../lib/account-session";
import { productFeatures } from "../lib/product-features";
import {
  type StudentScreenshotCandidate,
  studentClassPrefill,
} from "../lib/student-imports";
import {
  addStudentDays,
  readStudentBreak,
  readStudentCourse,
  readStudentFixedBlock,
  readStudentList,
  readStudentMeeting,
  readStudentProfile,
  readStudentTimetable,
  type StudentCourse,
  type StudentCourseInput,
  type StudentFixedBlock,
  type StudentFixedBlockInput,
  type StudentMeeting,
  type StudentMeetingInput,
  type StudentProfile,
  type StudentTimetable,
  studentClock,
  studentCourseDraft,
  studentDateInZone,
  studentDayLabel,
  studentDayNames,
  studentError,
  studentFixedDraft,
  studentLmsOptions,
  studentLocation,
  studentMeetingDraft,
  studentMeetingKinds,
  studentRequest,
  studentWeekdays,
  studentWriteUncertain,
} from "../lib/student-schedule";
import { consumeStudentClassDraft } from "../lib/student-voice";
import { ClassesWeek, mondayOf } from "./classes-week";
import { ClassesWelcome } from "./classes-welcome";
import { StudentNotifications } from "./student-notifications";
import styles from "./student-schedule.module.css";
import { StudentTimetableImport } from "./student-timetable-import";

const noContent = (_value: unknown) => null;
const clean = (value: string) => value.trim() || null;
type Save = <T>(
  path: string,
  parse: (value: unknown) => T,
  method: string,
  body?: unknown,
) => Promise<T | null>;
type Editor =
  | { kind: "course"; course?: StudentCourse }
  | {
      kind: "meeting";
      course: StudentCourse;
      meeting?: StudentMeeting;
      suggested?: Partial<StudentMeetingInput>;
    }
  | { kind: "shift"; shift?: StudentFixedBlock }
  | null;

export function StudentDayAgenda({
  timetable,
}: {
  timetable: StudentTimetable;
}) {
  const conflictIds = new Set(
    timetable.conflicts.flatMap((row) => [row.left_id, row.right_id]),
  );
  return (
    <div className={styles.agenda}>
      {Array.from({ length: timetable.days }, (_, index) =>
        addStudentDays(timetable.date, index),
      ).map((date) => {
        const classes = timetable.occurrences.filter(
          (row) => studentDateInZone(row.start_at, timetable.timezone) === date,
        );
        const shifts = timetable.fixed_occurrences.filter(
          (row) => studentDateInZone(row.start_at, timetable.timezone) === date,
        );
        const events = [
          ...classes.map((row) => ({
            id: row.id,
            start: row.start_at,
            end: row.end_at,
            title: row.course_code
              ? `${row.course_code} · ${row.course_name}`
              : row.course_name,
            subtitle: `${row.kind} · ${studentLocation(row)}`,
            fixed: false,
            cancelled: row.cancelled,
            changed: row.exception,
          })),
          ...shifts.map((row) => ({
            id: row.id,
            start: row.start_at,
            end: row.end_at,
            title: row.title,
            subtitle: "Work shift / fixed commitment",
            fixed: true,
            cancelled: false,
            changed: false,
          })),
        ].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
        const breaks = timetable.breaks.filter(
          (row) => row.start_date <= date && row.end_date >= date,
        );
        return (
          <section
            className={styles.day}
            key={date}
            aria-label={studentDayLabel(date)}
          >
            <h3>{studentDayLabel(date)}</h3>
            <div className={styles.dayEvents}>
              {breaks.map((row) => (
                <p className={styles.notice} key={row.id}>
                  {row.name} · classes excluded
                </p>
              ))}
              {events.length === 0 ? (
                <p className={styles.muted}>
                  No classes or shifts saved for this day.
                </p>
              ) : (
                events.map((row) => (
                  <article
                    key={row.id}
                    className={[
                      styles.event,
                      row.fixed ? styles.fixed : "",
                      row.cancelled ? styles.cancelled : "",
                      conflictIds.has(row.id) ? styles.conflict : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <time dateTime={row.start}>
                      {studentClock(row.start, timetable.timezone)}
                      <span>{studentClock(row.end, timetable.timezone)}</span>
                    </time>
                    <div>
                      <h4>
                        {row.title}
                        {row.cancelled && (
                          <span className={styles.badge}>Cancelled</span>
                        )}
                        {row.changed && !row.cancelled && (
                          <span className={styles.badge}>Changed</span>
                        )}
                      </h4>
                      <p>{row.subtitle}</p>
                      {conflictIds.has(row.id) && (
                        <p>Overlaps another saved commitment</p>
                      )}
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

export function StudentSetupCard({
  profile,
  busy,
  onSave,
  onChoose,
  onClose,
}: {
  profile: StudentProfile;
  busy: boolean;
  onSave: (value: StudentProfile) => Promise<boolean>;
  onChoose: (mode: "manual" | "ics" | "screenshot") => void;
  onClose: () => void;
}) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState(profile);
  const [suggestedZone, setSuggestedZone] = useState(false);
  const zonePrefilled = useRef(false);
  useEffect(() => {
    if (
      zonePrefilled.current ||
      profile.revision !== 0 ||
      profile.timezone !== "UTC"
    )
      return;
    zonePrefilled.current = true;
    try {
      const deviceZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (deviceZone && deviceZone !== "UTC") {
        new Intl.DateTimeFormat("en", { timeZone: deviceZone }).format();
        setDraft((current) =>
          current.timezone === "UTC"
            ? { ...current, timezone: deviceZone }
            : current,
        );
        setSuggestedZone(true);
      }
    } catch {}
  }, [profile.revision, profile.timezone]);
  const id = useId();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      await onSave({
        ...draft,
        school_name: clean(draft.school_name ?? ""),
        display_name: clean(draft.display_name ?? ""),
      })
    )
      setStep(1);
  }
  return (
    <section
      className={`${styles.panel} ${styles.setup}`}
      aria-labelledby={`${id}-title`}
    >
      <ol className={styles.setupSteps} aria-label="Student setup progress">
        {["Make it yours", "Add your timetable", "Ready for your day"].map(
          (label, index) => (
            <li key={label} aria-current={step === index ? "step" : undefined}>
              <span>{index + 1}</span>
              {label}
            </li>
          ),
        )}
      </ol>
      {step === 0 ? (
        <>
          <div className={styles.heading}>
            <div>
              <p className={styles.eyebrow}>
                About 3 minutes · every step is optional
              </p>
              <h2 id={`${id}-title`}>Hi, I’m NavoX. What should I call you?</h2>
              <p>
                Your classes and shifts work at any school. Start with the
                basics; add the rest when you’re ready.
              </p>
            </div>
          </div>
          <form className={styles.form} onSubmit={submit}>
            <fieldset className={styles.fields} disabled={busy}>
              <label htmlFor={`${id}-name`}>
                What should we call you?
                <input
                  id={`${id}-name`}
                  value={draft.display_name ?? ""}
                  maxLength={120}
                  autoComplete="given-name"
                  onChange={(event) =>
                    setDraft({ ...draft, display_name: event.target.value })
                  }
                />
              </label>
              <label htmlFor={`${id}-school`}>
                School (optional)
                <input
                  id={`${id}-school`}
                  value={draft.school_name ?? ""}
                  maxLength={120}
                  autoComplete="organization"
                  placeholder="Your college or university"
                  onChange={(event) =>
                    setDraft({ ...draft, school_name: event.target.value })
                  }
                />
                <span className={styles.hint}>
                  Any school works. Your timetable is the source of truth.
                </span>
              </label>
              <label htmlFor={`${id}-zone`}>
                Your time zone
                <input
                  id={`${id}-zone`}
                  list={`${id}-zone-choices`}
                  required
                  value={draft.timezone}
                  maxLength={64}
                  onChange={(event) =>
                    setDraft({ ...draft, timezone: event.target.value })
                  }
                  placeholder="Area / City"
                />
                <StudentTimezoneChoices id={`${id}-zone-choices`} />
                <span className={styles.hint}>
                  Choose your city’s zone.{" "}
                  {suggestedZone
                    ? "Suggested from this device; review before saving."
                    : "All class answers use your saved zone."}
                </span>
              </label>
              <label htmlFor={`${id}-lms`}>
                Learning system (optional)
                <select
                  id={`${id}-lms`}
                  value={draft.lms}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      lms: event.target.value as StudentProfile["lms"],
                    })
                  }
                >
                  {studentLmsOptions.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor={`${id}-start`}>
                Term starts (optional)
                <input
                  id={`${id}-start`}
                  type="date"
                  value={draft.term_start ?? ""}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      term_start: event.target.value || null,
                    })
                  }
                />
              </label>
              <label htmlFor={`${id}-end`}>
                Term ends (optional)
                <input
                  id={`${id}-end`}
                  type="date"
                  value={draft.term_end ?? ""}
                  min={draft.term_start ?? undefined}
                  onChange={(event) =>
                    setDraft({ ...draft, term_end: event.target.value || null })
                  }
                />
              </label>
            </fieldset>
            <div className={styles.actions}>
              <button className={styles.primary} disabled={busy} type="submit">
                Save and continue
              </button>
              <button
                className={styles.quiet}
                disabled={busy}
                type="button"
                onClick={() => setStep(1)}
              >
                Skip for now
              </button>
            </div>
          </form>
        </>
      ) : step === 1 ? (
        <>
          <p className={styles.eyebrow}>A schedule that belongs to you</p>
          <h2 id={`${id}-title`}>How would you like to add your classes?</h2>
          <p className={styles.muted}>
            No school login is needed. Review each class before saving.
          </p>
          <div className={styles.choiceGrid}>
            {[
              ["manual", "Add by hand", "Course, days, times, and room."],
              [
                "ics",
                "Import a calendar",
                "Upload an .ics file from your student portal.",
              ],
              [
                "screenshot",
                "Use a screenshot",
                "Read your schedule, then check every detail.",
              ],
            ].map(([mode, title, detail]) => (
              <button
                key={mode}
                type="button"
                disabled={busy}
                onClick={() => {
                  onChoose(mode as "manual" | "ics" | "screenshot");
                  setStep(2);
                }}
              >
                <strong>{title}</strong>
                <span>{detail}</span>
              </button>
            ))}
          </div>
          <div className={styles.actions}>
            <button
              className={styles.quiet}
              type="button"
              disabled={busy}
              onClick={() => setStep(2)}
            >
              I’ll add these later
            </button>
            <button
              className={styles.quiet}
              type="button"
              disabled={busy}
              onClick={() => setStep(0)}
            >
              Back
            </button>
          </div>
        </>
      ) : (
        <>
          <p className={styles.eyebrow}>One place to find your day</p>
          <h2 id={`${id}-title`}>Start small. Your next class is enough.</h2>
          <p className={styles.muted}>
            Add classes below, and NavoX can tell you what’s now and what’s
            next. Your school calendar, email, and location are separate
            choices.
          </p>
          <div className={styles.actions}>
            <button
              className={styles.primary}
              disabled={busy}
              type="button"
              onClick={onClose}
            >
              Open my timetable
            </button>
            <Link href="/connections" className={styles.textLink}>
              Explore optional connections
            </Link>
          </div>
        </>
      )}
    </section>
  );
}

function MeetingFields({
  value,
  onChange,
  id,
}: {
  value: StudentMeetingInput;
  onChange: (value: StudentMeetingInput) => void;
  id: string;
}) {
  return (
    <>
      <fieldset className={styles.days}>
        <legend>Days</legend>
        {studentWeekdays.map((label, day) => (
          <label key={label}>
            <input
              type="checkbox"
              checked={value.weekdays.includes(day)}
              onChange={(event) =>
                onChange({
                  ...value,
                  weekdays: event.target.checked
                    ? [...value.weekdays, day].sort()
                    : value.weekdays.filter((item) => item !== day),
                })
              }
            />
            {label}
          </label>
        ))}
      </fieldset>
      <label htmlFor={`${id}-kind`}>
        Section type
        <select
          id={`${id}-kind`}
          value={value.kind}
          onChange={(event) =>
            onChange({
              ...value,
              kind: event.target.value as StudentMeetingInput["kind"],
            })
          }
        >
          {studentMeetingKinds.map((kind) => (
            <option key={kind}>{kind}</option>
          ))}
        </select>
      </label>
      <label htmlFor={`${id}-timezone`}>
        Time zone
        <input
          id={`${id}-timezone`}
          list={`${id}-zone-choices`}
          required
          value={value.timezone}
          onChange={(event) =>
            onChange({ ...value, timezone: event.target.value })
          }
          maxLength={64}
        />
        <StudentTimezoneChoices id={`${id}-zone-choices`} />
      </label>
      <label htmlFor={`${id}-start-time`}>
        Starts
        <input
          id={`${id}-start-time`}
          required
          type="time"
          value={value.start_time}
          onChange={(event) =>
            onChange({ ...value, start_time: event.target.value })
          }
        />
      </label>
      <label htmlFor={`${id}-end-time`}>
        Ends
        <input
          id={`${id}-end-time`}
          required
          type="time"
          value={value.end_time}
          min={value.start_time || undefined}
          onChange={(event) =>
            onChange({ ...value, end_time: event.target.value })
          }
        />
      </label>
      <label htmlFor={`${id}-building`}>
        Building / location
        <input
          id={`${id}-building`}
          value={value.building ?? ""}
          onChange={(event) =>
            onChange({ ...value, building: event.target.value })
          }
          maxLength={200}
        />
      </label>
      <label htmlFor={`${id}-room`}>
        Room
        <input
          id={`${id}-room`}
          value={value.room ?? ""}
          onChange={(event) => onChange({ ...value, room: event.target.value })}
          maxLength={80}
        />
      </label>
      <label className={styles.wide} htmlFor={`${id}-instructor`}>
        Professor or TA (optional)
        <input
          id={`${id}-instructor`}
          value={value.instructor ?? ""}
          onChange={(event) =>
            onChange({ ...value, instructor: event.target.value })
          }
          maxLength={120}
        />
      </label>
      <label className={styles.wide} htmlFor={`${id}-walk`}>
        Walking time in minutes (optional)
        <input
          id={`${id}-walk`}
          type="number"
          min={0}
          max={120}
          step={1}
          value={value.walk_minutes ?? ""}
          onChange={(event) =>
            onChange({
              ...value,
              walk_minutes:
                event.target.value === "" ? null : Number(event.target.value),
            })
          }
        />
        <span className={styles.hint}>
          Your estimate. Used for a leave-now alert when you enable it; no
          location tracking.
        </span>
      </label>
    </>
  );
}

export function StudentCourseEditor({
  course,
  profile,
  busy,
  onSave,
  onClose,
  suggested,
}: {
  course?: StudentCourse;
  profile: StudentProfile;
  busy: boolean;
  onSave: (value: StudentCourseInput) => Promise<boolean>;
  onClose: () => void;
  suggested?: Partial<StudentCourseInput>;
}) {
  const id = useId();
  const [value, setValue] = useState<StudentCourseInput>(
    course
      ? studentCourseDraft(course)
      : {
          code: suggested?.code ?? "",
          name: suggested?.name ?? "",
          professor: suggested?.professor ?? null,
          office_hours: suggested?.office_hours ?? null,
          term_start: profile.term_start ?? "",
          term_end: profile.term_end ?? "",
        },
  );
  const [validation, setValidation] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (value.term_end < value.term_start) {
      setValidation("The term end must be on or after its start.");
      return;
    }
    if (
      await onSave({
        ...value,
        code: value.code.trim(),
        name: value.name.trim(),
        professor: clean(value.professor ?? ""),
        office_hours: clean(value.office_hours ?? ""),
      })
    )
      onClose();
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.formTitle}>
        <h3>{course ? "Edit course" : "Add a course"}</h3>
        <button
          className={styles.quiet}
          type="button"
          disabled={busy}
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
      <p className={styles.muted}>
        Save the course, then add its lecture, lab, or discussion times.
      </p>
      <fieldset className={styles.fields} disabled={busy}>
        <label htmlFor={`${id}-code`}>
          Course code (optional)
          <input
            id={`${id}-code`}
            value={value.code}
            maxLength={40}
            onChange={(event) =>
              setValue({ ...value, code: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-name`}>
          Course name
          <input
            id={`${id}-name`}
            required
            value={value.name}
            maxLength={120}
            onChange={(event) =>
              setValue({ ...value, name: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-start`}>
          Term starts
          <input
            id={`${id}-start`}
            required
            type="date"
            value={value.term_start}
            onChange={(event) =>
              setValue({ ...value, term_start: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-end`}>
          Term ends
          <input
            id={`${id}-end`}
            required
            type="date"
            value={value.term_end}
            min={value.term_start || undefined}
            onChange={(event) =>
              setValue({ ...value, term_end: event.target.value })
            }
          />
        </label>
        <label className={styles.wide} htmlFor={`${id}-professor`}>
          Professor (optional)
          <input
            id={`${id}-professor`}
            value={value.professor ?? ""}
            maxLength={120}
            onChange={(event) =>
              setValue({ ...value, professor: event.target.value })
            }
          />
        </label>
        <label className={styles.wide} htmlFor={`${id}-office-hours`}>
          Office hours (optional)
          <input
            id={`${id}-office-hours`}
            maxLength={500}
            value={value.office_hours ?? ""}
            placeholder="e.g. Tue 2–4 PM, Phillips 101"
            onChange={(e) =>
              setValue({ ...value, office_hours: e.target.value })
            }
          />
        </label>
      </fieldset>
      {validation && (
        <p className={styles.error} role="alert">
          {validation}
        </p>
      )}
      <div className={styles.actions}>
        <button disabled={busy} type="submit" className={styles.primary}>
          {busy
            ? "Saving…"
            : course
              ? "Save course"
              : "Save course and add times"}
        </button>
      </div>
    </form>
  );
}

function StudentMeetingEditor({
  course,
  meeting,
  suggested,
  timezone,
  busy,
  onSave,
  onClose,
}: {
  course: StudentCourse;
  meeting?: StudentMeeting;
  suggested?: Partial<StudentMeetingInput>;
  timezone: string;
  busy: boolean;
  onSave: (value: StudentMeetingInput) => Promise<boolean>;
  onClose: () => void;
}) {
  const id = useId();
  const [value, setValue] = useState<StudentMeetingInput>(
    meeting
      ? studentMeetingDraft(meeting)
      : {
          kind: "lecture",
          weekdays: [],
          start_time: "",
          end_time: "",
          timezone,
          building: null,
          room: null,
          instructor: null,
          walk_minutes: null,
          ...suggested,
        },
  );
  const [validation, setValidation] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (value.weekdays.length === 0) {
      setValidation("Choose at least one day for this section.");
      return;
    }
    if (value.end_time <= value.start_time) {
      setValidation("End time must be later than start time on the same day.");
      return;
    }
    if (
      await onSave({
        ...value,
        building: clean(value.building ?? ""),
        room: clean(value.room ?? ""),
        instructor: clean(value.instructor ?? ""),
      })
    )
      onClose();
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.formTitle}>
        <h3>
          {meeting ? "Edit" : "Add"} section · {course.code || course.name}
        </h3>
        <button
          className={styles.quiet}
          disabled={busy}
          type="button"
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
      {meeting?.dates && (
        <p className={styles.warning}>
          This imported section has {meeting.dates.length} exact meeting dates.
          Its saved dates stay in place when you edit the other details.
        </p>
      )}
      <fieldset className={styles.fields} disabled={busy}>
        <MeetingFields value={value} onChange={setValue} id={id} />
      </fieldset>
      {validation && (
        <p className={styles.error} role="alert">
          {validation}
        </p>
      )}
      <div className={styles.actions}>
        <button disabled={busy} type="submit" className={styles.primary}>
          {busy ? "Saving…" : "Save section"}
        </button>
        <small>
          {course.term_start} to {course.term_end}
        </small>
      </div>
    </form>
  );
}

function StudentShiftEditor({
  shift,
  profile,
  busy,
  onSave,
  onClose,
}: {
  shift?: StudentFixedBlock;
  profile: StudentProfile;
  busy: boolean;
  onSave: (value: StudentFixedBlockInput) => Promise<boolean>;
  onClose: () => void;
}) {
  const id = useId();
  const [value, setValue] = useState<StudentFixedBlockInput>(
    shift
      ? studentFixedDraft(shift)
      : {
          title: "",
          weekdays: [],
          start_time: "",
          end_time: "",
          timezone: profile.timezone,
          term_start: profile.term_start ?? "",
          term_end: profile.term_end ?? "",
        },
  );
  const [validation, setValidation] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !value.weekdays.length ||
      value.end_time <= value.start_time ||
      value.term_end < value.term_start
    ) {
      setValidation(
        "Choose days, an end time after the start, and valid term dates.",
      );
      return;
    }
    if (await onSave({ ...value, title: value.title.trim() })) onClose();
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.formTitle}>
        <h3>{shift ? "Edit" : "Add"} work shift or fixed commitment</h3>
        <button
          className={styles.quiet}
          disabled={busy}
          type="button"
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
      <fieldset className={styles.fields} disabled={busy}>
        <label className={styles.wide} htmlFor={`${id}-title`}>
          Name
          <input
            required
            id={`${id}-title`}
            maxLength={200}
            value={value.title}
            onChange={(event) =>
              setValue({ ...value, title: event.target.value })
            }
          />
        </label>
        <fieldset className={styles.days}>
          <legend>Days</legend>
          {studentWeekdays.map((day, index) => (
            <label key={day}>
              <input
                type="checkbox"
                checked={value.weekdays.includes(index)}
                onChange={(event) =>
                  setValue({
                    ...value,
                    weekdays: event.target.checked
                      ? [...value.weekdays, index].sort()
                      : value.weekdays.filter((day) => day !== index),
                  })
                }
              />
              {day}
            </label>
          ))}
        </fieldset>
        <label htmlFor={`${id}-start`}>
          Starts
          <input
            id={`${id}-start`}
            type="time"
            required
            value={value.start_time}
            onChange={(event) =>
              setValue({ ...value, start_time: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-end`}>
          Ends
          <input
            id={`${id}-end`}
            type="time"
            required
            min={value.start_time || undefined}
            value={value.end_time}
            onChange={(event) =>
              setValue({ ...value, end_time: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-term-start`}>
          From
          <input
            id={`${id}-term-start`}
            type="date"
            required
            value={value.term_start}
            onChange={(event) =>
              setValue({ ...value, term_start: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-term-end`}>
          Through
          <input
            id={`${id}-term-end`}
            type="date"
            required
            min={value.term_start || undefined}
            value={value.term_end}
            onChange={(event) =>
              setValue({ ...value, term_end: event.target.value })
            }
          />
        </label>
        <label className={styles.wide} htmlFor={`${id}-timezone`}>
          Time zone
          <input
            id={`${id}-timezone`}
            list={`${id}-zone-choices`}
            required
            maxLength={64}
            value={value.timezone}
            onChange={(event) =>
              setValue({ ...value, timezone: event.target.value })
            }
          />
          <StudentTimezoneChoices id={`${id}-zone-choices`} />
        </label>
      </fieldset>
      {validation && (
        <p className={styles.error} role="alert">
          {validation}
        </p>
      )}
      <div className={styles.actions}>
        <button className={styles.primary} type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save commitment"}
        </button>
      </div>
    </form>
  );
}

function StudentExceptionEditor({
  course,
  meeting,
  busy,
  onSave,
  onClose,
}: {
  course: StudentCourse;
  meeting: StudentMeeting;
  busy: boolean;
  onSave: (date: string, value: unknown) => Promise<boolean>;
  onClose: () => void;
}) {
  const id = useId();
  const [date, setDate] = useState("");
  const [cancelled, setCancelled] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [building, setBuilding] = useState("");
  const [room, setRoom] = useState("");
  const [validation, setValidation] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!!start !== !!end || (start && end <= start)) {
      setValidation(
        "Enter both times, with the end after the start, or leave both blank.",
      );
      return;
    }
    if (
      await onSave(date, {
        revision: meeting.revision,
        cancelled,
        start_time: start || null,
        end_time: end || null,
        building: clean(building),
        room: clean(room),
      })
    )
      onClose();
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.formTitle}>
        <h3>
          One-day change · {course.code || course.name} {meeting.kind}
        </h3>
        <button
          className={styles.quiet}
          type="button"
          disabled={busy}
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
      <p className={styles.muted}>
        This changes one meeting only. Blank times and locations keep the usual
        details.
      </p>
      <fieldset className={styles.fields} disabled={busy}>
        <label htmlFor={`${id}-date`}>
          Meeting date
          <input
            id={`${id}-date`}
            type="date"
            required
            min={course.term_start}
            max={course.term_end}
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        </label>
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={cancelled}
            onChange={(event) => setCancelled(event.target.checked)}
          />
          Class cancelled this day
        </label>
        {!cancelled && (
          <>
            <label htmlFor={`${id}-start`}>
              New start (optional)
              <input
                id={`${id}-start`}
                type="time"
                value={start}
                onChange={(event) => setStart(event.target.value)}
              />
            </label>
            <label htmlFor={`${id}-end`}>
              New end (optional)
              <input
                id={`${id}-end`}
                type="time"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
              />
            </label>
            <label htmlFor={`${id}-building`}>
              New location (optional)
              <input
                id={`${id}-building`}
                value={building}
                maxLength={200}
                onChange={(event) => setBuilding(event.target.value)}
              />
            </label>
            <label htmlFor={`${id}-room`}>
              New room (optional)
              <input
                id={`${id}-room`}
                value={room}
                maxLength={80}
                onChange={(event) => setRoom(event.target.value)}
              />
            </label>
          </>
        )}
      </fieldset>
      {validation && (
        <p className={styles.error} role="alert">
          {validation}
        </p>
      )}
      <div className={styles.actions}>
        <button className={styles.primary} disabled={busy} type="submit">
          Save one-day change
        </button>
      </div>
    </form>
  );
}

function StudentBreakEditor({
  busy,
  onSave,
}: {
  busy: boolean;
  onSave: (value: unknown) => Promise<boolean>;
}) {
  const id = useId();
  const [name, setName] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (await onSave({ name: name.trim(), start_date: start, end_date: end })) {
      setName("");
      setStart("");
      setEnd("");
    }
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <h3>Add a class break</h3>
      <p className={styles.muted}>
        Classes won’t appear during these dates. Work shifts stay on your
        timetable.
      </p>
      <fieldset className={styles.fields} disabled={busy}>
        <label className={styles.wide} htmlFor={`${id}-name`}>
          Break name
          <input
            required
            id={`${id}-name`}
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label htmlFor={`${id}-start`}>
          First day
          <input
            required
            id={`${id}-start`}
            type="date"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </label>
        <label htmlFor={`${id}-end`}>
          Last day (included)
          <input
            required
            id={`${id}-end`}
            type="date"
            min={start || undefined}
            value={end}
            onChange={(event) => setEnd(event.target.value)}
          />
        </label>
      </fieldset>
      <div className={styles.actions}>
        <button className={styles.primary} disabled={busy} type="submit">
          Save break
        </button>
      </div>
    </form>
  );
}

function StudentClassesAccount({
  accountKey,
  onTimezoneChange,
  settingsOnly = false,
}: {
  accountKey: string;
  settingsOnly?: boolean;
  onTimezoneChange?: (zone: string) => void;
}) {
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [courses, setCourses] = useState<StudentCourse[]>([]);
  const [timetable, setTimetable] = useState<StudentTimetable | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [date, setDate] = useState("");
  const [tab, setTab] = useState<"week" | "courses" | "shifts" | "term">(
    settingsOnly ? "term" : "week",
  );
  const [editor, setEditor] = useState<Editor>(null);
  const [exception, setException] = useState<{
    course: StudentCourse;
    meeting: StudentMeeting;
  } | null>(null);
  const [importMode, setImportMode] = useState<"ics" | "screenshot" | null>(
    null,
  );
  const [showArchive, setShowArchive] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const [screenshotSuggestion, setScreenshotSuggestion] =
    useState<StudentScreenshotCandidate | null>(null);
  const generation = useRef(0);
  const dateRef = useRef("");
  const operation = useRef(false);
  const alive = useRef(true);
  const [spokenDraft, setSpokenDraft] = useState("");
  const load = useCallback(
    async (reset = false, quiet = false): Promise<boolean> => {
      const request = ++generation.current;
      if (!quiet) setLoading(true);
      try {
        const [nextProfile, active, archived, nextTable] = await Promise.all([
          studentRequest("/profile", readStudentProfile),
          studentRequest("/courses?archived=false", (value) =>
            readStudentList(value, "courses", readStudentCourse),
          ),
          studentRequest("/courses?archived=true", (value) =>
            readStudentList(value, "courses", readStudentCourse),
          ),
          studentRequest(
            `/timetable?days=7${dateRef.current ? `&date=${dateRef.current}` : ""}`,
            readStudentTimetable,
          ),
        ]);
        if (request !== generation.current) return false;
        setProfile(nextProfile);
        setCourses([
          ...new Map(
            [...active, ...archived].map((row) => [row.id, row]),
          ).values(),
        ]);
        const aligned = mondayOf(nextTable.date);
        const week =
          nextTable.date === aligned
            ? nextTable
            : await studentRequest(
                `/timetable?days=7&date=${aligned}`,
                readStudentTimetable,
              );
        if (request !== generation.current) return false;
        setTimetable(week);
        setDate(week.date);
        dateRef.current = week.date;
        setError("");
        if (reset) {
          setUncertain(false);
          setEditor(null);
          setException(null);
          setImportMode(null);
          setScreenshotSuggestion(null);
          setEpoch((value) => value + 1);
          setNotice(
            "Saved timetable reloaded. Check the saved details before making another change.",
          );
        }
        return true;
      } catch (cause) {
        if (request === generation.current) setError(studentError(cause));
        return false;
      } finally {
        if (request === generation.current && !quiet) setLoading(false);
      }
    },
    [],
  );
  useEffect(() => {
    alive.current = true;
    let draft: string | null = null;
    try {
      draft = consumeStudentClassDraft(window.sessionStorage, accountKey);
    } catch {
      /* Timetable loading also works when browser storage is blocked. */
    }
    if (draft) {
      setSpokenDraft(draft);
      setScreenshotSuggestion(studentClassPrefill(draft));
      setTab("courses");
      setEditor({ kind: "course" });
    }
    if (new URLSearchParams(window.location.search).has("add")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
    void load();
    return () => {
      alive.current = false;
      ++generation.current;
    };
  }, [load, accountKey]);
  useEffect(() => {
    if (
      settingsOnly ||
      busy ||
      loading ||
      uncertain ||
      editor ||
      exception ||
      importMode
    )
      return;
    const refresh = () => {
      if (document.visibilityState === "visible" && !operation.current)
        void load(false, true);
    };
    const interval = window.setInterval(refresh, 60000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [
    settingsOnly,
    busy,
    loading,
    uncertain,
    editor,
    exception,
    importMode,
    load,
  ]);
  const mutate: Save = async (path, parse, method, body) => {
    if (operation.current || uncertain) return null;
    operation.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await studentRequest(path, parse, method, body);
      if (!alive.current) return null;
      const refreshed = await load();
      if (!alive.current) return null;
      if (!refreshed) {
        setUncertain(true);
        setNotice(
          "The change was saved, but the latest timetable could not be reloaded. Reload before editing again.",
        );
      } else setNotice("Saved to your timetable.");
      return result;
    } catch (cause) {
      setError(studentError(cause));
      if (studentWriteUncertain(cause)) setUncertain(true);
      return null;
    } finally {
      operation.current = false;
      setBusy(false);
    }
  };
  const locked = busy || loading || uncertain;
  function choose(mode: "manual" | "ics" | "screenshot") {
    setScreenshotSuggestion(null);
    setTab("courses");
    if (mode === "manual") {
      setImportMode(null);
      setEditor({ kind: "course" });
    } else {
      setEditor(null);
      setImportMode(mode);
    }
  }
  async function deleteEntry(path: string) {
    if (operation.current || uncertain) return;
    operation.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await studentRequest(path, noContent, "DELETE");
      if (await load()) setNotice("Removed from your timetable.");
      else setUncertain(true);
    } catch (cause) {
      setError(studentError(cause));
      if (studentWriteUncertain(cause)) setUncertain(true);
    } finally {
      operation.current = false;
      setBusy(false);
    }
  }
  function changeDate(next: string) {
    if (!next || locked) return;
    dateRef.current = next;
    setDate(next);
    void load();
  }
  return (
    <div className={styles.workspace}>
      {!settingsOnly && (
        <div className={styles.classesToolbar}>
          <p>Classes, labs and shifts</p>
          <details className={styles.addMenu}>
            <summary>
              Add <span aria-hidden="true">⌄</span>
            </summary>
            <div>
              <button
                type="button"
                disabled={!profile || locked}
                onClick={(e) => {
                  choose("manual");
                  e.currentTarget.closest("details")?.removeAttribute("open");
                }}
              >
                Add a class
              </button>
              <button
                type="button"
                disabled={!profile || locked}
                onClick={(e) => {
                  choose("ics");
                  e.currentTarget.closest("details")?.removeAttribute("open");
                }}
              >
                Import a file
              </button>
              <button
                type="button"
                disabled={!profile || locked}
                onClick={(e) => {
                  choose("screenshot");
                  e.currentTarget.closest("details")?.removeAttribute("open");
                }}
              >
                Snap a photo
              </button>
            </div>
          </details>
        </div>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
          {!uncertain && (
            <button
              type="button"
              disabled={busy || loading}
              onClick={() => void load()}
            >
              Reload
            </button>
          )}
        </p>
      )}
      {uncertain && (
        <div className={styles.warning} role="alert">
          <strong>Check what saved before trying again.</strong>
          <p>
            The result of a change could not be fully checked. Editing is paused
            to avoid saving it twice.
          </p>
          <button
            type="button"
            disabled={busy || loading}
            onClick={() => void load(true)}
          >
            {loading ? "Reloading…" : "Reload saved timetable"}
          </button>
        </div>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      {!profile ? (
        <section
          className={`${styles.panel} ${tab === "week" ? styles.weekPanel : ""}`}
          aria-busy={loading}
        >
          <p className={styles.muted}>
            {loading
              ? "Opening your saved timetable…"
              : "Your timetable could not be opened. Reload to try again."}
          </p>
        </section>
      ) : (
        <>
          {!settingsOnly &&
            tab === "week" &&
            !courses.some((row) => !row.archived) &&
            !timetable?.fixed_blocks.length && (
              <ClassesWelcome
                disabled={locked}
                onChoose={choose}
                onShifts={() => {
                  setTab("shifts");
                  setEditor({ kind: "shift" });
                }}
              />
            )}
          <section
            className={`${styles.panel} ${tab === "week" ? styles.weekPanel : ""}`}
            aria-busy={loading}
          >
            {!settingsOnly && (
              <nav className={styles.tabs} aria-label="Timetable views">
                {[
                  ["week", "Week"],
                  ["courses", "Courses"],
                  ["shifts", "Shifts"],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    aria-pressed={tab === value}
                    type="button"
                    onClick={() => setTab(value as typeof tab)}
                  >
                    {label}
                    {value === "courses" &&
                      ` · ${courses.filter((row) => !row.archived).length}`}
                  </button>
                ))}
              </nav>
            )}
            {tab === "week" && (
              <>
                <div className={styles.dateNav}>
                  <div>
                    <h2>
                      {timetable
                        ? studentDayLabel(timetable.date)
                        : "Your timetable"}
                    </h2>
                    <span className={styles.badge}>7 days</span>
                  </div>
                  <div>
                    <button
                      type="button"
                      aria-label="Previous week"
                      disabled={locked || !date}
                      onClick={() => changeDate(addStudentDays(date, -7))}
                    >
                      ←
                    </button>
                    <label>
                      Starting
                      <input
                        type="date"
                        value={date}
                        disabled={locked}
                        onChange={(event) => changeDate(event.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      aria-label="Next week"
                      disabled={locked || !date}
                      onClick={() => changeDate(addStudentDays(date, 7))}
                    >
                      →
                    </button>
                    <button
                      type="button"
                      disabled={locked || !timetable}
                      onClick={() => {
                        dateRef.current = "";
                        void load();
                      }}
                    >
                      Today
                    </button>
                  </div>
                </div>
                {timetable && (
                  <>
                    {timetable.conflicts.length > 0 && (
                      <p className={styles.warning}>
                        {timetable.conflicts.length} saved time{" "}
                        {timetable.conflicts.length === 1
                          ? "overlap"
                          : "overlaps"}
                        . Both commitments stay visible so you can decide what
                        to change.
                      </p>
                    )}
                    {timetable.warnings.map((warning) => (
                      <p key={warning} className={styles.warning}>
                        {warning}
                      </p>
                    ))}
                    {(courses.some((row) => !row.archived) ||
                      timetable.fixed_blocks.length > 0) && (
                      <ClassesWeek
                        timetable={timetable}
                        onEdit={(course, meeting) => {
                          setTab("courses");
                          setEditor(
                            meeting
                              ? { kind: "meeting", course, meeting }
                              : { kind: "course", course },
                          );
                        }}
                        onShift={(id) => {
                          setTab("shifts");
                          const shift = timetable.fixed_blocks.find(
                            (row) => row.id === id,
                          );
                          if (shift) setEditor({ kind: "shift", shift });
                        }}
                      />
                    )}
                  </>
                )}
              </>
            )}
            {tab === "courses" && (
              <>
                <div className={styles.heading} style={{ marginTop: "1.2rem" }}>
                  <div>
                    <h2>Your courses</h2>
                    <p>
                      One course can have a lecture, a lab, and a discussion.
                      Add each section’s real times.
                    </p>
                  </div>
                  <div className={styles.actions} style={{ marginTop: 0 }}>
                    <button
                      disabled={locked}
                      type="button"
                      onClick={() => choose("ics")}
                    >
                      Import .ics
                    </button>
                    <button
                      disabled={locked}
                      type="button"
                      onClick={() => choose("screenshot")}
                    >
                      Screenshot
                    </button>
                  </div>
                </div>
                {importMode && (
                  <StudentTimetableImport
                    key={`${importMode}-${epoch}`}
                    mode={importMode}
                    profile={profile}
                    disabled={locked}
                    onClose={() => setImportMode(null)}
                    onSaved={async () => {
                      setImportMode(null);
                      if (!(await load())) {
                        setUncertain(true);
                        setNotice(
                          "Reviewed classes were saved. Reload the saved timetable before editing again.",
                        );
                      } else
                        setNotice("Reviewed classes saved to your timetable.");
                    }}
                    onUncertain={(message) => {
                      setUncertain(true);
                      setError(message);
                    }}
                    onReviewCourse={(suggestion) => {
                      const existing = courses.find(
                        (course) =>
                          !course.archived &&
                          ((suggestion.course_code &&
                            course.code.toLocaleLowerCase() ===
                              suggestion.course_code.toLocaleLowerCase()) ||
                            course.name.toLocaleLowerCase() ===
                              suggestion.course_name.toLocaleLowerCase()),
                      );
                      setEditor(
                        existing
                          ? {
                              kind: "meeting",
                              course: existing,
                              suggested: {
                                kind: suggestion.kind,
                                weekdays: suggestion.weekdays,
                                start_time: suggestion.start_time ?? "",
                                end_time: suggestion.end_time ?? "",
                                timezone: profile.timezone,
                                building: suggestion.building,
                                room: suggestion.room,
                                instructor: suggestion.instructor,
                                walk_minutes: null,
                              },
                            }
                          : { kind: "course" },
                      );
                      setScreenshotSuggestion(suggestion);
                    }}
                  />
                )}
                {spokenDraft && (
                  <p className={styles.notice}>
                    You said: “{spokenDraft}”<br />
                    Review and fill the class details below. Nothing has been
                    added yet.
                  </p>
                )}
                {screenshotSuggestion && (
                  <p className={styles.warning}>
                    {screenshotSuggestion.uncertainties.join(" ")}
                  </p>
                )}
                {editor?.kind === "course" && (
                  <StudentCourseEditor
                    key={`course-${editor.course?.id ?? (screenshotSuggestion ? JSON.stringify(screenshotSuggestion) : "new")}-${epoch}`}
                    course={editor.course}
                    profile={profile}
                    busy={locked}
                    suggested={
                      screenshotSuggestion
                        ? {
                            name: screenshotSuggestion.course_name,
                            code: screenshotSuggestion.course_code ?? "",
                          }
                        : undefined
                    }
                    onSave={async (value) => {
                      const saved = await mutate(
                        editor.course
                          ? `/courses/${editor.course.id}`
                          : "/courses",
                        readStudentCourse,
                        editor.course ? "PATCH" : "POST",
                        editor.course
                          ? {
                              ...value,
                              archived: editor.course.archived,
                              revision: editor.course.revision,
                            }
                          : value,
                      );
                      if (saved && !editor.course) {
                        const suggestion = screenshotSuggestion;
                        setEditor({
                          kind: "meeting",
                          course: saved,
                          ...(suggestion
                            ? {
                                suggested: {
                                  kind: suggestion.kind,
                                  weekdays: suggestion.weekdays,
                                  start_time: suggestion.start_time ?? "",
                                  end_time: suggestion.end_time ?? "",
                                  timezone: profile.timezone,
                                  building: suggestion.building,
                                  room: suggestion.room,
                                  instructor: suggestion.instructor,
                                },
                              }
                            : {}),
                        });
                      }
                      if (saved) {
                        setScreenshotSuggestion(null);
                        setSpokenDraft("");
                      }
                      return !!saved;
                    }}
                    onClose={() => {
                      setEditor((current) =>
                        current?.kind === "course" ? null : current,
                      );
                      setScreenshotSuggestion(null);
                    }}
                  />
                )}
                {editor?.kind === "meeting" && (
                  <StudentMeetingEditor
                    key={`meeting-${editor.meeting?.id ?? editor.course.id}-${epoch}`}
                    course={editor.course}
                    meeting={editor.meeting}
                    suggested={editor.suggested}
                    timezone={profile.timezone}
                    busy={locked}
                    onClose={() => setEditor(null)}
                    onSave={async (value) =>
                      !!(await mutate(
                        editor.meeting
                          ? `/meetings/${editor.meeting.id}`
                          : `/courses/${editor.course.id}/meetings`,
                        readStudentMeeting,
                        editor.meeting ? "PATCH" : "POST",
                        editor.meeting
                          ? { ...value, revision: editor.meeting.revision }
                          : value,
                      ))
                    }
                  />
                )}
                {exception && (
                  <StudentExceptionEditor
                    key={`exception-${exception.meeting.id}-${epoch}`}
                    {...exception}
                    busy={locked}
                    onClose={() => setException(null)}
                    onSave={async (date, value) =>
                      !!(await mutate(
                        `/meetings/${exception.meeting.id}/exceptions/${date}`,
                        readStudentMeeting,
                        "PUT",
                        value,
                      ))
                    }
                  />
                )}
                <div className={styles.cards}>
                  {courses
                    .filter((row) => row.archived === showArchive)
                    .map((course) => (
                      <article className={styles.course} key={course.id}>
                        <div className={styles.courseHeading}>
                          <div>
                            {course.code && (
                              <p className={styles.courseCode}>{course.code}</p>
                            )}
                            <h3>{course.name}</h3>
                            {productFeatures.studentCoursework && (
                              <Link href={`/classes/${course.id}`}>
                                Open course workspace
                              </Link>
                            )}
                            <p className={styles.courseMeta}>
                              {course.term_start} — {course.term_end}
                              {course.professor && ` · ${course.professor}`}
                              {course.archived && " · Archived"}
                            </p>
                          </div>
                          <div
                            className={styles.actions}
                            style={{ marginTop: 0 }}
                          >
                            <button
                              disabled={locked}
                              type="button"
                              onClick={() => {
                                setImportMode(null);
                                setEditor({ kind: "course", course });
                              }}
                            >
                              Edit course
                            </button>
                            <button
                              disabled={locked}
                              type="button"
                              onClick={() =>
                                void mutate(
                                  `/courses/${course.id}`,
                                  readStudentCourse,
                                  "PATCH",
                                  {
                                    code: course.code,
                                    name: course.name,
                                    term_start: course.term_start,
                                    term_end: course.term_end,
                                    professor: course.professor,
                                    archived: !course.archived,
                                    revision: course.revision,
                                  },
                                )
                              }
                            >
                              {course.archived ? "Restore" : "Archive"}
                            </button>
                          </div>
                        </div>
                        {!course.archived && (
                          <>
                            {course.meetings.length === 0 && (
                              <p className={styles.muted}>
                                No meeting times saved yet. Add a section so
                                this course appears in your day.
                              </p>
                            )}
                            {course.meetings.map((meeting) => (
                              <div className={styles.section} key={meeting.id}>
                                <div>
                                  <strong>{meeting.kind}</strong>
                                  <p>
                                    {meeting.dates
                                      ? `${meeting.dates.length} exact dates`
                                      : studentDayNames(meeting.weekdays)}{" "}
                                    · {meeting.start_time}–{meeting.end_time} ·{" "}
                                    {meeting.timezone.replaceAll("_", " ")}
                                  </p>
                                  <p>
                                    {studentLocation(meeting)}
                                    {meeting.instructor &&
                                      ` · ${meeting.instructor}`}
                                  </p>
                                  {timetable?.exceptions
                                    .filter(
                                      (row) => row.meeting_id === meeting.id,
                                    )
                                    .map((change) => (
                                      <p key={change.id}>
                                        {change.date} ·{" "}
                                        {change.cancelled
                                          ? "Cancelled"
                                          : "One-day change"}{" "}
                                        <button
                                          className={styles.quiet}
                                          disabled={locked}
                                          type="button"
                                          onClick={() =>
                                            void deleteEntry(
                                              `/meetings/${meeting.id}/exceptions/${change.date}?revision=${meeting.revision}`,
                                            )
                                          }
                                        >
                                          Undo change
                                        </button>
                                      </p>
                                    ))}
                                </div>
                                <div className={styles.actions}>
                                  <button
                                    disabled={locked}
                                    type="button"
                                    onClick={() =>
                                      setEditor({
                                        kind: "meeting",
                                        course,
                                        meeting,
                                      })
                                    }
                                  >
                                    Edit times
                                  </button>
                                  <button
                                    disabled={locked}
                                    type="button"
                                    onClick={() =>
                                      setException({ course, meeting })
                                    }
                                  >
                                    One-day change
                                  </button>
                                  <button
                                    disabled={locked}
                                    className={styles.danger}
                                    type="button"
                                    onClick={() =>
                                      void deleteEntry(
                                        `/meetings/${meeting.id}?revision=${meeting.revision}`,
                                      )
                                    }
                                  >
                                    Remove section
                                  </button>
                                </div>
                              </div>
                            ))}
                            <div className={styles.actions}>
                              <button
                                disabled={locked}
                                type="button"
                                onClick={() =>
                                  setEditor({ kind: "meeting", course })
                                }
                              >
                                ＋ Add section
                              </button>
                            </div>
                          </>
                        )}
                      </article>
                    ))}
                </div>
                {courses.filter((row) => row.archived === showArchive)
                  .length === 0 && (
                  <p className={styles.empty}>
                    {showArchive
                      ? "No archived courses."
                      : "No courses saved yet. Add your first course to get started."}
                  </p>
                )}
                <div className={styles.actions}>
                  <button
                    className={styles.quiet}
                    type="button"
                    onClick={() => setShowArchive(!showArchive)}
                  >
                    {showArchive
                      ? "Show active courses"
                      : `Archived courses (${courses.filter((row) => row.archived).length})`}
                  </button>
                </div>
              </>
            )}
            {tab === "shifts" && (
              <>
                <div className={styles.heading} style={{ marginTop: "1.2rem" }}>
                  <div>
                    <h2>Keep space for the rest of your life.</h2>
                    <p>
                      Add work shifts, club meetings, or other fixed
                      commitments. You’ll see class overlaps.
                    </p>
                  </div>
                  <button
                    disabled={locked}
                    type="button"
                    onClick={() => setEditor({ kind: "shift" })}
                  >
                    ＋ Add commitment
                  </button>
                </div>
                {editor?.kind === "shift" && (
                  <StudentShiftEditor
                    key={`shift-${editor.shift?.id ?? "new"}-${epoch}`}
                    shift={editor.shift}
                    profile={profile}
                    busy={locked}
                    onClose={() => setEditor(null)}
                    onSave={async (value) =>
                      !!(await mutate(
                        editor.shift
                          ? `/fixed-blocks/${editor.shift.id}`
                          : "/fixed-blocks",
                        readStudentFixedBlock,
                        editor.shift ? "PATCH" : "POST",
                        editor.shift
                          ? { ...value, revision: editor.shift.revision }
                          : value,
                      ))
                    }
                  />
                )}
                <div className={styles.cards}>
                  {timetable?.fixed_blocks.map((shift) => (
                    <article key={shift.id} className={styles.course}>
                      <div className={styles.courseHeading}>
                        <div>
                          <h3>{shift.title}</h3>
                          <p className={styles.courseMeta}>
                            {studentDayNames(shift.weekdays)} ·{" "}
                            {shift.start_time}–{shift.end_time} ·{" "}
                            {shift.timezone.replaceAll("_", " ")}
                          </p>
                          <p className={styles.courseMeta}>
                            {shift.term_start} — {shift.term_end}
                          </p>
                        </div>
                        <div
                          className={styles.actions}
                          style={{ marginTop: 0 }}
                        >
                          <button
                            disabled={locked}
                            type="button"
                            onClick={() => setEditor({ kind: "shift", shift })}
                          >
                            Edit
                          </button>
                          <button
                            className={styles.danger}
                            disabled={locked}
                            type="button"
                            onClick={() =>
                              void deleteEntry(
                                `/fixed-blocks/${shift.id}?revision=${shift.revision}`,
                              )
                            }
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
                {timetable?.fixed_blocks.length === 0 && (
                  <p className={styles.empty}>
                    No fixed commitments saved. Add the hours you want to keep
                    free for work or clubs.
                  </p>
                )}
              </>
            )}
            {tab === "term" && (
              <>
                <div className={styles.heading} style={{ marginTop: "1.2rem" }}>
                  <div>
                    <h2>Your school, your calendar.</h2>
                    <p>
                      Use your own term dates and breaks. NavoX won’t guess your
                      school’s calendar.
                    </p>
                  </div>
                </div>
                <StudentProfileSettings
                  key={`profile-${profile.revision}-${epoch}`}
                  profile={profile}
                  busy={locked}
                  onSave={async (value) => {
                    const saved = await mutate(
                      "/profile",
                      readStudentProfile,
                      "PUT",
                      value,
                    );
                    if (saved) onTimezoneChange?.(saved.timezone);
                    return !!saved;
                  }}
                />
                <details className={styles.disclosure}>
                  <summary>
                    Class breaks & holidays ({timetable?.breaks.length ?? 0})
                  </summary>
                  {timetable?.breaks.map((row) => (
                    <div className={styles.breakRow} key={row.id}>
                      <p>
                        {row.name}
                        <br />
                        <small>
                          {row.start_date} — {row.end_date}
                        </small>
                      </p>
                      <button
                        type="button"
                        disabled={locked}
                        onClick={() =>
                          void deleteEntry(
                            `/breaks/${row.id}?revision=${row.revision}`,
                          )
                        }
                      >
                        Remove break
                      </button>
                    </div>
                  ))}
                  <StudentBreakEditor
                    key={`break-${epoch}`}
                    busy={locked}
                    onSave={async (value) =>
                      !!(await mutate(
                        "/breaks",
                        readStudentBreak,
                        "POST",
                        value,
                      ))
                    }
                  />
                </details>
                <details className={styles.disclosure}>
                  <summary>Optional class reminders</summary>
                  <p className={styles.muted}>
                    Choose what to receive and when. Browser permission is
                    requested only when you choose to enable this device.
                  </p>
                  <StudentNotifications accountId={accountKey} />
                </details>
                <StudentDataReset
                  busy={locked}
                  onReset={async (requestId) => {
                    const change = notifyAccountSessionChanging();
                    try {
                      const result = await mutate(
                        "/data/reset",
                        (value) => {
                          if (
                            !value ||
                            typeof value !== "object" ||
                            !("cleared" in value) ||
                            value.cleared !== true
                          )
                            throw new Error(
                              "The reset result could not be checked.",
                            );
                          return value;
                        },
                        "POST",
                        {
                          confirmed: true,
                          revision: profile.revision,
                          request_id: requestId,
                        },
                      );
                      if (!result) return false;
                      setEditor(null);
                      setException(null);
                      setImportMode(null);
                      setScreenshotSuggestion(null);
                      setSpokenDraft("");
                      setEpoch((value) => value + 1);
                      setTab(settingsOnly ? "term" : "week");
                      setNotice(
                        "Your student timetable and profile have been forgotten.",
                      );
                      return true;
                    } finally {
                      notifyAccountSessionChanged(change);
                    }
                  }}
                />
                <p className={styles.hint}>
                  Connections are always optional.{" "}
                  <Link href="/connections" className={styles.textLink}>
                    Manage learning systems and email
                  </Link>
                </p>
              </>
            )}
          </section>
        </>
      )}
    </div>
  );
}

export function StudentProfileSettings({
  profile,
  busy,
  onSave,
}: {
  profile: StudentProfile;
  busy: boolean;
  onSave: (value: StudentProfile) => Promise<boolean>;
}) {
  const id = useId();
  const [value, setValue] = useState(profile);
  const [saved, setSaved] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      await onSave({
        ...value,
        school_name: clean(value.school_name ?? ""),
        display_name: clean(value.display_name ?? ""),
      })
    )
      setSaved(true);
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <fieldset className={styles.fields} disabled={busy}>
        <label htmlFor={`${id}-name`}>
          What should we call you?
          <input
            id={`${id}-name`}
            value={value.display_name ?? ""}
            maxLength={120}
            onChange={(event) => {
              setValue({ ...value, display_name: event.target.value });
              setSaved(false);
            }}
          />
        </label>
        <label htmlFor={`${id}-school`}>
          School
          <input
            id={`${id}-school`}
            value={value.school_name ?? ""}
            maxLength={120}
            onChange={(event) =>
              setValue({ ...value, school_name: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-zone`}>
          Time zone
          <input
            id={`${id}-zone`}
            list={`${id}-zone-choices`}
            value={value.timezone}
            maxLength={64}
            required
            onChange={(event) =>
              setValue({ ...value, timezone: event.target.value })
            }
          />
          <StudentTimezoneChoices id={`${id}-zone-choices`} />
          <span className={styles.hint}>
            Changing your profile zone updates display times. Saved section
            zones stay explicit.
          </span>
        </label>
        <label htmlFor={`${id}-lms`}>
          Learning system
          <select
            id={`${id}-lms`}
            value={value.lms}
            onChange={(event) =>
              setValue({
                ...value,
                lms: event.target.value as StudentProfile["lms"],
              })
            }
          >
            {studentLmsOptions.map(([key, label]) => (
              <option value={key} key={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-start`}>
          Default term start
          <input
            id={`${id}-start`}
            type="date"
            value={value.term_start ?? ""}
            onChange={(event) =>
              setValue({ ...value, term_start: event.target.value || null })
            }
          />
        </label>
        <label htmlFor={`${id}-end`}>
          Default term end
          <input
            id={`${id}-end`}
            type="date"
            min={value.term_start ?? undefined}
            value={value.term_end ?? ""}
            onChange={(event) =>
              setValue({ ...value, term_end: event.target.value || null })
            }
          />
        </label>
      </fieldset>
      <p className={styles.hint}>
        Default term dates prefill new courses and shifts. Existing term dates
        stay as you saved them.
      </p>
      <div className={styles.actions}>
        <button disabled={busy} type="submit" className={styles.primary}>
          Save profile
        </button>
        {saved && <span role="status">Profile saved.</span>}
      </div>
    </form>
  );
}

export function StudentClassesWorkspace({
  accountId,
  onTimezoneChange,
  settingsOnly,
}: {
  settingsOnly?: boolean;
  accountId: string;
  displayName?: string | null;
  timezone?: string;
  onTimezoneChange?: (zone: string) => void;
}) {
  return (
    <StudentClassesAccount
      key={accountId}
      accountKey={accountId}
      settingsOnly={settingsOnly}
      onTimezoneChange={onTimezoneChange}
    />
  );
}

function StudentDataReset({
  busy,
  onReset,
}: {
  busy: boolean;
  onReset: (requestId: string) => Promise<boolean>;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [requestId] = useState(() => crypto.randomUUID());
  return (
    <details className={styles.disclosure}>
      <summary>Forget my student data</summary>
      <p className={styles.warning}>
        This permanently forgets all your saved courses, class sections, one-day
        changes, shifts, breaks, student profile, notification devices and
        settings, calendar feeds and their private links, study tasks, plan
        history, course notes, files, saved Boards, and import previews. To drop
        a single course and keep its history, archive it instead.
      </p>
      <label className={styles.check}>
        <input
          type="checkbox"
          checked={confirmed}
          disabled={busy}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        I want to permanently forget this student data.
      </label>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.danger}
          disabled={busy || !confirmed}
          onClick={async () => {
            if (await onReset(requestId)) setConfirmed(false);
          }}
        >
          Forget student data
        </button>
      </div>
    </details>
  );
}

function StudentTimezoneChoices({ id }: { id: string }) {
  return (
    <datalist id={id}>
      {[
        "Africa/Accra",
        "Africa/Johannesburg",
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "America/Toronto",
        "America/Sao_Paulo",
        "Asia/Kolkata",
        "Asia/Singapore",
        "Asia/Tokyo",
        "Australia/Sydney",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "UTC",
      ].map((zone) => (
        <option key={zone} value={zone}>
          {zone.replaceAll("_", " ")}
        </option>
      ))}
    </datalist>
  );
}
