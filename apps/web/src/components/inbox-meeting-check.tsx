"use client";

import { useEffect, useId, useMemo, useState } from "react";
import {
  meetingAlternatives,
  meetingCheck,
  meetingTimeLabel,
} from "../lib/inbox-availability";
import {
  type AvailabilitySnapshot,
  createStudentCalendarApi,
} from "../lib/student-calendar-api";
import { observeCalendarAccessChanges } from "../lib/student-calendar-privacy";
import { studyDeadlineChoices } from "../lib/student-planning";
import { addStudentDays, studentDateInZone } from "../lib/student-schedule";
import styles from "./communication-drafts.module.css";
import { useCalendarWorkspace } from "./use-calendar-workspace";

/** Explicit time selection avoids inventing a date or timezone from email prose. */
export function InboxMeetingCheck({
  accountKey,
  timezone,
  disabled,
  onUseTimes,
}: {
  accountKey: string;
  timezone: string;
  disabled: boolean;
  onUseTimes: (instructions: string) => void;
}) {
  const id = useId();
  const api = useMemo(() => createStudentCalendarApi(accountKey), [accountKey]);
  const [local, setLocal] = useState("");
  const [duration, setDuration] = useState(30);
  const [routine, setRoutine] = useState(false);
  const [travel, setTravel] = useState(false);
  const [snapshot, setSnapshot] = useState<AvailabilitySnapshot | null>(null);
  const [checkedStart, setCheckedStart] = useState("");
  const [notice, setNotice] = useState("");
  const state = useCalendarWorkspace(accountKey, api, () => {
    setSnapshot(null);
    setCheckedStart("");
    setNotice("");
  });
  const { cancel, run } = state;
  useEffect(
    () =>
      observeCalendarAccessChanges(() => {
        cancel();
        setSnapshot(null);
        setNotice("Calendar access changed. Check this time again.");
      }),
    [cancel],
  );
  useEffect(() => {
    if (!snapshot) return;
    const expire = () => {
      setSnapshot(null);
      setNotice(
        "These times need a fresh check before you use them in a reply.",
      );
    };
    const remaining = Date.parse(snapshot.expires_at) - Date.now();
    if (remaining <= 0) {
      expire();
      return;
    }
    const timer = setTimeout(expire, Math.min(remaining, 60_000));
    return () => clearTimeout(timer);
  }, [snapshot]);
  const changed = () => {
    cancel();
    setSnapshot(null);
    setNotice("");
    state.setError("");
  };
  async function check() {
    changed();
    await run(
      async (signal) => {
        const preferences = await api.preferences(signal);
        if (preferences.timezone !== timezone)
          throw new Error(
            "Your timezone changed. Open Inbox again to check the time in your saved timezone.",
          );
        const choices = studyDeadlineChoices(local, timezone);
        if (choices.length !== 1)
          throw new Error(
            "That time falls in a clock change. Choose an unambiguous local time.",
          );
        const start = choices[0];
        if (Date.parse(start) <= Date.now())
          throw new Error("Choose a future meeting time.");
        const day = studentDateInZone(start, timezone);
        const ends = studyDeadlineChoices(
          `${addStudentDays(day, 7)}T23:59`,
          timezone,
        );
        if (ends.length !== 1)
          throw new Error(
            "Choose a different search window around the clock change.",
          );
        const result = await api.preview(
          {
            start_at: new Date(Date.parse(start) - 10 * 60_000).toISOString(),
            end_at: ends[0],
            duration_minutes: duration,
            preferences_revision: preferences.revision,
            expected_timezone: timezone,
            routine_confirmed: routine && preferences.revision > 0,
            travel_assumption: travel ? "zero" : "unknown",
            include_short_slots: true,
          },
          signal,
        );
        return { result, start };
      },
      ({ result, start }) => {
        setSnapshot(result);
        setCheckedStart(start);
      },
    );
  }
  const verdict = snapshot
    ? meetingCheck(snapshot, checkedStart, duration)
    : null;
  const alternatives = snapshot ? meetingAlternatives(snapshot, duration) : [];
  const choices =
    snapshot && verdict === "clear"
      ? [
          {
            start_at: checkedStart,
            end_at: new Date(
              Date.parse(checkedStart) + duration * 60_000,
            ).toISOString(),
          },
        ]
      : alternatives;
  function useTimes() {
    if (
      !snapshot ||
      disabled ||
      !choices.length ||
      Date.parse(snapshot.expires_at) <= Date.now()
    ) {
      setSnapshot(null);
      setNotice("Check the times again before using them in your reply.");
      return;
    }
    const text = choices
      .map((choice) =>
        meetingTimeLabel(choice.start_at, choice.end_at, timezone),
      )
      .join(" or ");
    onUseTimes(
      `Ask whether ${text} (${timezone.replaceAll("_", " ")}) works. These options were just checked against my connected calendars, saved classes and routine. Do not invent personal reasons or promise that a meeting has been booked.`,
    );
    setNotice(
      "The checked times were added to your reply instructions. Review the draft and check again if plans change before sending.",
    );
  }
  return (
    <details className={styles.notice}>
      <summary>Check a meeting time before replying</summary>
      <p>
        Enter the time from the email in your saved timezone. NavoX checks your
        calendar, classes, study work and routine without creating an event.
      </p>
      <fieldset
        className={styles.meetingFields}
        disabled={disabled || state.busy || !state.ready}
      >
        <label htmlFor={`${id}-time`}>
          Proposed time · {timezone.replaceAll("_", " ")}
        </label>
        <input
          id={`${id}-time`}
          type="datetime-local"
          value={local}
          onChange={(event) => {
            changed();
            setLocal(event.target.value);
          }}
        />
        <label htmlFor={`${id}-duration`}>Duration</label>
        <select
          id={`${id}-duration`}
          value={duration}
          onChange={(event) => {
            changed();
            setDuration(Number(event.target.value));
          }}
        >
          {[15, 30, 45, 60, 90, 120].map((minutes) => (
            <option key={minutes} value={minutes}>
              {minutes} minutes
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={routine}
            onChange={(event) => {
              changed();
              setRoutine(event.target.checked);
            }}
          />
          My saved wake, sleep and protected times are up to date.
        </label>
        <label>
          <input
            type="checkbox"
            checked={travel}
            onChange={(event) => {
              changed();
              setTravel(event.target.checked);
            }}
          />
          Assume no extra travel around commitments for this check.
        </label>
        <button
          type="button"
          disabled={!local || state.busy}
          onClick={() => void check()}
        >
          {state.busy ? "Checking…" : "Check this time"}
        </button>
      </fieldset>
      <p>
        Ten minutes before and after are kept clear. Missing calendar access or
        unknown travel stays unconfirmed.
      </p>
      {state.error && <p role="alert">{state.error}</p>}
      {notice && <p role="status">{notice}</p>}
      {snapshot && (
        <div role="status">
          <p>
            {verdict === "clear"
              ? "This time fits your checked schedule."
              : verdict === "conflict"
                ? "This time conflicts with your saved schedule or its buffers."
                : "This time could not be confirmed. Check your calendar access, routine and travel assumptions."}
          </p>
          {choices.length > 0 && (
            <>
              <ul>
                {choices.map((choice) => (
                  <li key={choice.start_at}>
                    {meetingTimeLabel(choice.start_at, choice.end_at, timezone)}
                  </li>
                ))}
              </ul>
              <button type="button" disabled={disabled} onClick={useTimes}>
                Use {choices.length === 1 ? "this time" : "these times"} in
                reply instructions
              </button>
            </>
          )}
          <p>
            Only checked sources are covered. This does not book a meeting or
            send an email.
          </p>
        </div>
      )}
    </details>
  );
}
