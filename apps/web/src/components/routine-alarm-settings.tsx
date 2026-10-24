"use client";
import { useEffect, useRef, useState } from "react";
import {
  defaultAlarm,
  type RoutinePreview,
  readRoutinePreview,
  readStudyPreferences,
  routineRequest,
  type StudyPreferences,
  studyPreferencesDraft,
} from "../lib/student-planning";
import {
  addStudentDays,
  readStudentTimetable,
  type StudentTimetable,
  studentDateInZone,
  studentRequest,
  studentWeekdays,
} from "../lib/student-schedule";
import styles from "./routine-alarm-settings.module.css";
import { RoutineChangeReview } from "./routine-change-review";

export function RoutineAlarmSettings({ accountKey }: { accountKey: string }) {
  const [timetable, setTimetable] = useState<StudentTimetable | null>(null);
  const [reload, setReload] = useState(0);
  const [saved, setSaved] = useState<StudyPreferences | null>(null);
  const [draft, setDraft] = useState<StudyPreferences | null>(null);
  const [preview, setPreview] = useState<RoutinePreview | null>(null);
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const request = useRef(crypto.randomUUID());
  const alive = useRef(true);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry reloads the saved routine.
  useEffect(() => {
    alive.current = true;
    let current = true;
    setSaved(null);
    setDraft(null);
    setPreview(null);
    setNotice("");
    setTimetable(null);
    void studentRequest(
      "/timetable?days=14",
      readStudentTimetable,
      "GET",
      undefined,
      accountKey,
    )
      .then((value) => {
        if (current) setTimetable(value);
      })
      .catch(() => {});
    void routineRequest(accountKey, "/preferences", readStudyPreferences)
      .then((value) => {
        if (current) {
          setSaved(value);
          setDraft(value);
        }
      })
      .catch(() => {
        if (current)
          setNotice(
            "Your routine couldn’t load. Reopen Settings to try again.",
          );
      });
    return () => {
      current = false;
      alive.current = false;
    };
  }, [accountKey, reload]);
  if (!saved || !draft)
    return (
      <section id="routine-alarm" className={styles.card}>
        <h2>Routine & alarm</h2>
        <p role="status">{notice || "Loading your routine…"}</p>
        {notice && (
          <button type="button" onClick={() => setReload((n) => n + 1)}>
            Retry
          </button>
        )}
      </section>
    );
  const alarm = draft.alarm ?? defaultAlarm,
    wakes = draft.wake_times ?? Array(7).fill(draft.wake_time);
  const tomorrow = addStudentDays(
    studentDateInZone(
      timetable?.server_now ?? new Date().toISOString(),
      saved.timezone,
    ),
    1,
  );
  const classDays = new Set(
    (timetable?.occurrences ?? [])
      .filter((row) => !row.cancelled)
      .map((row) => {
        const day = studentDateInZone(row.start_at, saved.timezone);
        return (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
      }),
  );
  const firstClassDay = [...classDays].sort()[0] ?? 0;
  const firstOtherDay =
    [0, 1, 2, 3, 4, 5, 6].find((day) => !classDays.has(day)) ?? 5;
  const changed = (change: Partial<StudyPreferences>) => {
    setDraft({ ...draft, ...change });
    setPreview(null);
    setNotice("");
    request.current = crypto.randomUUID();
  };
  async function submit(confirm: boolean) {
    if (busy || !draft) return;
    setBusy(true);
    setNotice("");
    const body = {
      ...studyPreferencesDraft(draft),
      request_id: request.current,
    };
    try {
      if (confirm && preview) {
        const value = await routineRequest(
          accountKey,
          "/preferences/confirm",
          readStudyPreferences,
          "POST",
          { ...body, preview_hash: preview.preview_hash },
        );
        if (!alive.current) return;
        setSaved(value);
        setDraft(value);
        setPreview(null);
        request.current = crypto.randomUUID();
        setNotice(
          "Wake-up plan saved. Keep using your phone’s alarm until native ringing is available.",
        );
      } else {
        const value = await routineRequest(
          accountKey,
          "/preferences/preview",
          readRoutinePreview,
          "POST",
          body,
        );
        if (alive.current) setPreview(value);
      }
    } catch {
      if (alive.current) {
        setPreview(null);
        setNotice(
          "The routine may have changed. Reopen Settings to load it, then review again.",
        );
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section
      id="routine-alarm"
      className={styles.card}
      aria-labelledby="routine-alarm-title"
    >
      <h2 id="routine-alarm-title">Routine & alarm</h2>
      <p>Choose your wake-up plan and the hours Planner should protect.</p>
      <p className={styles.webNote}>
        Web preview: these settings save your wake-up plan. Keep using your
        phone’s alarm; NavoX cannot ring while your browser is closed.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit(false);
        }}
      >
        <fieldset disabled={busy || !!preview}>
          <div className={styles.pair}>
            <label>
              Usual wake time
              <input
                type="time"
                value={draft.wake_time}
                required
                onChange={(e) =>
                  changed({ wake_time: e.target.value, wake_times: null })
                }
              />
            </label>
            <label>
              Usually asleep by
              <input
                type="time"
                value={draft.sleep_time}
                required
                onChange={(e) => changed({ sleep_time: e.target.value })}
              />
            </label>
          </div>
          <p>
            {classDays.size
              ? `Class days in your next two weeks: ${[...classDays]
                  .sort()
                  .map((d) => studentWeekdays[d])
                  .join(", ")}`
              : "Add your classes to use class-day wake times. You can adjust every day below."}
          </p>
          <div className={styles.pair}>
            <label>
              Class-day wake time
              <input
                type="time"
                value={wakes[firstClassDay]}
                disabled={!classDays.size}
                required
                onChange={(e) =>
                  changed({
                    wake_times: wakes.map((v: string, i: number) =>
                      classDays.has(i) ? e.target.value : v,
                    ),
                  })
                }
              />
            </label>
            <label>
              Other-day wake time
              <input
                type="time"
                value={wakes[firstOtherDay]}
                required
                onChange={(e) =>
                  changed({
                    wake_times: wakes.map((v: string, i: number) =>
                      !classDays.has(i) ? e.target.value : v,
                    ),
                  })
                }
              />
            </label>
          </div>
          <label>
            Travel buffer (minutes)
            <input
              type="number"
              min={0}
              max={240}
              required
              value={draft.travel_minutes ?? 15}
              onChange={(e) =>
                changed({ travel_minutes: Number(e.target.value) })
              }
            />
          </label>
          <details>
            <summary>Adjust each day</summary>
            <div className={styles.days}>
              {studentWeekdays.map((day, i) => (
                <label key={day}>
                  {day}
                  <input
                    aria-label={`${day} wake time`}
                    type="time"
                    required
                    value={wakes[i]}
                    onChange={(e) =>
                      changed({
                        wake_times: wakes.map((v: string, n: number) =>
                          n === i ? e.target.value : v,
                        ),
                      })
                    }
                  />
                </label>
              ))}
            </div>
          </details>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={alarm.enabled}
              onChange={(e) =>
                changed({ alarm: { ...alarm, enabled: e.target.checked } })
              }
            />
            Enable saved alarm plan
          </label>
          <div className={styles.alarmDays}>
            {studentWeekdays.map((day, i) => (
              <label key={day}>
                <input
                  type="checkbox"
                  checked={alarm.days.includes(i)}
                  onChange={(e) =>
                    changed({
                      alarm: {
                        ...alarm,
                        days: e.target.checked
                          ? [...alarm.days, i]
                          : alarm.days.filter((d) => d !== i),
                      },
                    })
                  }
                />
                {day}
              </label>
            ))}
          </div>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={alarm.smart}
              onChange={(e) =>
                changed({ alarm: { ...alarm, smart: e.target.checked } })
              }
            />
            Smart alarm: first class minus preparation and walking time
          </label>
          <div className={styles.pair}>
            <label>
              Preparation minutes
              <input
                type="number"
                min={0}
                max={180}
                value={alarm.prep_minutes}
                onChange={(e) =>
                  changed({
                    alarm: { ...alarm, prep_minutes: Number(e.target.value) },
                  })
                }
              />
            </label>
            <label>
              Alarm sound
              <select
                value={alarm.sound}
                onChange={(e) =>
                  changed({
                    alarm: {
                      ...alarm,
                      sound: e.target.value as "gentle" | "chime",
                    },
                  })
                }
              >
                <option value="gentle">Gentle</option>
                <option value="chime">Chime</option>
              </select>
            </label>
            <label>
              Snooze length
              <select
                value={alarm.snooze_minutes}
                onChange={(e) =>
                  changed({
                    alarm: {
                      ...alarm,
                      snooze_minutes: Number(e.target.value) as 5 | 10 | 15,
                    },
                  })
                }
              >
                {[5, 10, 15].map((n) => (
                  <option key={n} value={n}>
                    {n} minutes
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={alarm.skip_date === tomorrow}
              onChange={(e) =>
                changed({
                  alarm: {
                    ...alarm,
                    skip_date: e.target.checked ? tomorrow : null,
                  },
                })
              }
            />
            Skip tomorrow’s alarm plan
          </label>
          <button type="submit" className={styles.primary}>
            Review changes
          </button>
        </fieldset>
      </form>
      {preview && (
        <RoutineChangeReview
          preview={preview}
          draft={draft}
          busy={busy}
          onApprove={() => void submit(true)}
          onCancel={() => setPreview(null)}
        />
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
