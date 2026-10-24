"use client";
import Link from "next/link";
import { useState } from "react";
import { setupWakeTimes } from "../lib/routine-setup";
import {
  type StudyPreferences,
  studyPreferencesDraft,
} from "../lib/student-planning";
import { studentWeekdays } from "../lib/student-schedule";
import { NoaArtwork } from "./noa-artwork";
import styles from "./planner-redesign.module.css";
import { useVoxRequest } from "./use-vox-request";

export function PlannerWelcome({
  preferences,
  hasClasses,
  classDays = [],
  disabled,
  onCapture,
  onRoutine,
}: {
  preferences: StudyPreferences;
  hasClasses: boolean;
  classDays?: number[];
  disabled: boolean;
  onCapture: (text: string) => void;
  onRoutine: (
    value: ReturnType<typeof studyPreferencesDraft>,
  ) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const wakes =
    preferences.wake_times ?? Array<string>(7).fill(preferences.wake_time);
  const [classWake, setClassWake] = useState(wakes[classDays[0] ?? 0]);
  const [otherWake, setOtherWake] = useState(
    wakes[[5, 6, 0, 1, 2, 3, 4].find((day) => !classDays.includes(day)) ?? 5],
  );
  const [classWakeChanged, setClassWakeChanged] = useState(false);
  const [otherWakeChanged, setOtherWakeChanged] = useState(false);
  const [sleep, setSleep] = useState(preferences.sleep_time);
  const [focus, setFocus] = useState("");
  const [saved, setSaved] = useState(false);
  const vox = useVoxRequest();
  return (
    <section className={styles.welcome}>
      <header className={styles.hero}>
        <div className={styles.noa}>
          <span>let’s plan it</span>
          <NoaArtwork size={180} mood="focus" />
        </div>
        <div>
          <h2>Tell me what’s due. I’ll find the time.</h2>
          <p>
            NavoX splits big work into study blocks and fits them around your
            classes, shifts and sleep. You see every plan before it’s saved.
          </p>
        </div>
      </header>
      <form
        className={styles.capture}
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) onCapture(text.trim());
        }}
      >
        <label htmlFor="planner-capture">What’s due?</label>
        <div>
          <input
            id="planner-capture"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={200}
            placeholder="e.g. ECE lab report, Friday, about 4 hours"
            disabled={disabled}
            required
          />
          <button
            type="button"
            aria-label="Tell Vox what’s due"
            disabled={disabled || !vox.available || vox.busy}
            onClick={() =>
              void vox.ask(
                "Help me capture what is due. Ask for the task, course, deadline and effort, then show me the details and proposed study plan before saving anything.",
              )
            }
          >
            Mic
          </button>
          <button
            type="submit"
            className={styles.primary}
            disabled={disabled || !text.trim()}
          >
            Plan it
          </button>
        </div>
        <div className={styles.shortcuts}>
          <span>Quick start:</span>
          {[
            "Problem set",
            "Lab report",
            "Essay",
            "Exam prep",
            "Group project",
          ].map((value) => (
            <button
              type="button"
              key={value}
              onClick={() => setText(value)}
              disabled={disabled}
            >
              {value}
            </button>
          ))}
        </div>
        {vox.notice && <p role="status">{vox.notice}</p>}
      </form>
      <div className={styles.welcomeColumns}>
        <form
          className={styles.card}
          onSubmit={async (e) => {
            e.preventDefault();
            const windows =
              focus === "morning"
                ? [
                    {
                      weekdays: [0, 1, 2, 3, 4, 5, 6],
                      start_time: "08:00",
                      end_time: "12:00",
                    },
                  ]
                : focus === "afternoon"
                  ? [
                      {
                        weekdays: [0, 1, 2, 3, 4, 5, 6],
                        start_time: "12:00",
                        end_time: "17:00",
                      },
                    ]
                  : focus === "evening"
                    ? [
                        {
                          weekdays: [0, 1, 2, 3, 4, 5, 6],
                          start_time: "17:00",
                          end_time: "21:00",
                        },
                      ]
                    : preferences.focus_windows;
            setSaved(
              await onRoutine({
                ...studyPreferencesDraft(preferences),
                wake_times: setupWakeTimes(
                  preferences,
                  classDays,
                  classWakeChanged ? classWake : null,
                  otherWakeChanged ? otherWake : null,
                ),
                sleep_time: sleep,
                focus_windows: windows,
              }),
            );
          }}
        >
          <h3>Your routine</h3>
          <p>So plans leave room for sleep and your best focus hours.</p>
          <label>
            Class-day wake time
            <input
              aria-label="Class-day wake time"
              type="time"
              required
              value={classWake}
              onChange={(e) => {
                setClassWake(e.target.value);
                setClassWakeChanged(true);
                setSaved(false);
              }}
              disabled={disabled || classDays.length === 0}
            />
          </label>
          <label>
            Weekend &amp; non-class-day wake time
            <input
              aria-label="Weekend and non-class-day wake time"
              type="time"
              required
              value={otherWake}
              disabled={disabled || classDays.length === 7}
              onChange={(event) => {
                setOtherWake(event.target.value);
                setOtherWakeChanged(true);
                setSaved(false);
              }}
            />
          </label>
          <p>
            {classDays.length
              ? `Your upcoming class days: ${classDays.map((day) => studentWeekdays[day]).join(", ")}. A weekend with class uses your class-day time.`
              : "Add your class times to choose class-day wake times. For now, the non-class-day time applies every day."}
          </p>
          <label>
            I’m usually asleep by
            <input
              aria-label="I’m usually asleep by"
              type="time"
              required
              value={sleep}
              onChange={(e) => {
                setSleep(e.target.value);
                setSaved(false);
              }}
              disabled={disabled}
            />
          </label>
          <label>
            I focus best in the
            <select
              value={focus}
              onChange={(e) => {
                setFocus(e.target.value);
                setSaved(false);
              }}
              disabled={disabled}
            >
              <option value="">Keep my saved hours</option>
              <option value="morning">Morning</option>
              <option value="afternoon">Afternoon</option>
              <option value="evening">Evening</option>
            </select>
          </label>
          <button type="submit" disabled={disabled}>
            Review routine
          </button>
          {saved && <p role="status">Routine saved.</p>}
        </form>
        <div className={styles.example}>
          <h3>How a plan looks</h3>
          <p>Example · a four-hour lab report due Friday</p>
          <div className={styles.exampleTask}>
            Lab report <span>~4 h</span>
          </div>
          <p aria-hidden="true" className={styles.arrow}>
            ↓
          </p>
          <div className={styles.exampleBlocks}>
            <span>Wed · 90 min</span>
            <span>Thu · 90 min</span>
            <span>Fri · 60 min</span>
          </div>
          <p>
            Three blocks in your free time, with room before the deadline.
            Review every change when you choose Done, Took longer or Skip.
          </p>
        </div>
      </div>
      {!hasClasses && (
        <div className={styles.classPrompt}>
          <span>Add your classes first, so plans fit around them.</span>
          <Link className="nx-button" href="/classes">
            Add classes
          </Link>
        </div>
      )}
    </section>
  );
}
