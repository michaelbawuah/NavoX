"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import type { AvailabilityOption } from "../lib/student-availability-options";
import {
  createStudentAvailabilitySuggestionsApi,
  type MeetingPurpose,
  type MeetingSuggestionReason,
  meetingOption,
  type StudentAvailabilitySuggestionsApi,
} from "../lib/student-availability-suggestions-api";
import {
  type AvailabilityPreviewInput,
  type AvailabilitySnapshot,
  createStudentCalendarApi,
  type StudentCalendarApi,
} from "../lib/student-calendar-api";
import { observeCalendarAccessChanges } from "../lib/student-calendar-privacy";
import {
  type StudyPreferences,
  studyDeadlineChoices,
  studyLocalDateTime,
} from "../lib/student-planning";
import {
  addStudentDays,
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentWeekdays,
} from "../lib/student-schedule";
import styles from "./student-calendar.module.css";
import { StudentCalendarScheduling } from "./student-calendar-scheduling";
import { useCalendarWorkspace } from "./use-calendar-workspace";

const reasons = {
  coverage_unknown: "Some required sources could not be fully checked.",
  routine_unconfirmed: "Your waking hours and protected time are unconfirmed.",
  travel_unknown: "Travel time is unknown.",
  movable_study: "Study work overlaps this window and has not been replanned.",
};
const suggestionLabels: Record<MeetingSuggestionReason, string> = {
  within_their_hours: "Within the sourced availability",
  right_after_commitment: "Keeps your day together",
  right_before_commitment: "Fits before your next commitment",
  lunch_time: "During lunch hours",
  soonest: "Earliest suitable option",
  clear_of_deadlines: "Clear of the checked deadlines",
  moves_study_block: "Needs reviewed study moves",
};
type RankedOption = AvailabilityOption & { reasons: MeetingSuggestionReason[] };

function sourceLabel(sourceId: string, index: number) {
  if (sourceId === "student-timetable") return "Classes and fixed commitments";
  if (sourceId === "student-study-plan") return "Saved study plan";
  if (sourceId === "student-calendar-actions")
    return "Saved NavoX calendar events";
  if (sourceId.startsWith("google-")) return "Google calendar access";
  if (sourceId.startsWith("microsoft-")) return "Outlook calendar access";
  if (sourceId.startsWith("icloud-")) return "Apple calendar access";
  return `Other required source ${index + 1}`;
}

function timeRange(start: string, end: string, timezone: string) {
  const first = studentDateInZone(start, timezone);
  const last = studentDateInZone(end, timezone);
  return `${studentDayLabel(first)} · ${studentClock(start, timezone)}–${
    first === last ? "" : `${studentDayLabel(last)} `
  }${studentClock(end, timezone)}`;
}

function PositionedOption({
  option,
  index,
  timezone,
}: {
  option: RankedOption;
  index: number;
  timezone: string;
}) {
  return (
    <li className={styles.option}>
      <p className={styles.eyebrow}>Option {index + 1}</p>
      <p className={styles.time}>
        {timeRange(option.start_at, option.end_at, timezone)}
      </p>
      <p className={styles.hint}>
        {option.duration_minutes} minutes · 10-minute buffers before and after
      </p>
      <p className={styles.hint}>
        Including buffers: {studentClock(option.reserved_start_at, timezone)}–
        {studentClock(option.reserved_end_at, timezone)}
      </p>
      {option.reasons.length > 0 && (
        <p className={styles.hint}>
          {option.reasons.map((reason) => suggestionLabels[reason]).join(" · ")}
        </p>
      )}
      {!!option.displaced_study_block_ids?.length && (
        <p className={styles.warning}>
          Requires moving {option.displaced_study_block_ids.length} study
          {option.displaced_study_block_ids.length === 1 ? " block" : " blocks"}
          . Review the exact replacement times before approving. Nothing has
          moved.
        </p>
      )}
    </li>
  );
}

interface AvailabilityProps {
  accountId: string;
  timezone: string;
  api?: StudentCalendarApi;
  suggestionsApi?: StudentAvailabilitySuggestionsApi;
}

export function StudentAvailability({
  collapsible = false,
  ...props
}: AvailabilityProps & { collapsible?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const content = (
    <AvailabilityWorkspace
      key={`${props.accountId}:${props.timezone}`}
      {...props}
    />
  );
  return collapsible ? (
    <details
      className={styles.disclosure}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>Find time for a meeting</summary>
      {expanded ? content : null}
    </details>
  ) : (
    content
  );
}

function AvailabilityWorkspace({
  accountId,
  timezone,
  api: suppliedApi,
  suggestionsApi: suppliedSuggestionsApi,
}: AvailabilityProps) {
  const id = useId();
  const api = useMemo(
    () => suppliedApi ?? createStudentCalendarApi(accountId),
    [accountId, suppliedApi],
  );
  const suggestionsApi = useMemo(
    () =>
      suppliedSuggestionsApi ??
      createStudentAvailabilitySuggestionsApi(accountId),
    [accountId, suppliedSuggestionsApi],
  );
  const [preferences, setPreferences] = useState<StudyPreferences | null>(null);
  const [snapshot, setSnapshot] = useState<AvailabilitySnapshot | null>(null);
  const [searchRequest, setSearchRequest] =
    useState<AvailabilityPreviewInput | null>(null);
  const [options, setOptions] = useState<RankedOption[]>([]);
  const studyWindows = snapshot?.result.provisional_openings.some(
    (opening) =>
      opening.reasons.length === 1 && opening.reasons[0] === "movable_study",
  );
  const [purpose, setPurpose] = useState<MeetingPurpose>("other");
  const [duration, setDuration] = useState(30);
  const [routine, setRoutine] = useState<"" | "confirmed" | "unknown">("");
  const [travel, setTravel] = useState<"" | "unknown" | "zero">("");
  const [from, setFrom] = useState(
    () =>
      `${studyLocalDateTime(new Date().toISOString(), timezone).slice(0, 10)}T09:00`,
  );
  const [until, setUntil] = useState(() => {
    const today = studentDateInZone(new Date().toISOString(), timezone);
    return `${addStudentDays(today, 6)}T19:00`;
  });
  const [notice, setNotice] = useState("");
  const attemptedPreferences = useRef(false);
  const inputZone = useRef(timezone);
  const privateState = useCalendarWorkspace(accountId, api, () => {
    setPreferences(null);
    setSnapshot(null);
    setSearchRequest(null);
    setOptions([]);
    setRoutine("");
    setTravel("");
    setNotice("");
    attemptedPreferences.current = false;
  });
  const { run, cancel } = privateState;
  const accessChanged = useCallback(() => {
    cancel();
    setSnapshot(null);
    setSearchRequest(null);
    setOptions([]);
    setNotice(
      "Calendar access changed. Check openings again before making a plan.",
    );
  }, [cancel]);
  useEffect(() => observeCalendarAccessChanges(accessChanged), [accessChanged]);
  const loadPreferences = useCallback(() => {
    setSnapshot(null);
    setSearchRequest(null);
    setOptions([]);
    setPreferences(null);
    setRoutine("");
    setNotice("");
    void run(
      (signal) => api.preferences(signal),
      (current) => {
        if (current.timezone !== inputZone.current) {
          const today = studentDateInZone(
            new Date().toISOString(),
            current.timezone,
          );
          setFrom(`${today}T09:00`);
          setUntil(`${addStudentDays(today, 6)}T19:00`);
          inputZone.current = current.timezone;
          setNotice(
            "Your timezone changed. The search window was reset to your saved timezone.",
          );
        }
        setPreferences(current);
      },
    );
  }, [api, run]);
  useEffect(() => {
    if (!privateState.ready || attemptedPreferences.current) return;
    attemptedPreferences.current = true;
    loadPreferences();
  }, [privateState.ready, loadPreferences]);

  useEffect(() => {
    if (!snapshot) return;
    const remaining = Math.min(
      60_000,
      Date.parse(snapshot.expires_at) - Date.now(),
    );
    const expire = () => {
      setSnapshot(null);
      setSearchRequest(null);
      setOptions([]);
      setNotice(
        "These times need a fresh check. Search again before making a plan.",
      );
    };
    if (remaining <= 0) {
      expire();
      return;
    }
    const timer = window.setTimeout(expire, remaining);
    return () => window.clearTimeout(timer);
  }, [snapshot]);

  const changeSearch = () => {
    privateState.cancel();
    setSnapshot(null);
    setSearchRequest(null);
    setOptions([]);
    setNotice("");
    privateState.setError("");
  };
  const search = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!preferences || !routine || !travel) return;
    changeSearch();
    let start: string;
    let end: string;
    try {
      const starts = studyDeadlineChoices(from, preferences.timezone);
      const ends = studyDeadlineChoices(until, preferences.timezone);
      if (starts.length !== 1 || ends.length !== 1)
        throw new Error(
          "A chosen local time is missing or repeated during a clock change. Choose another time.",
        );
      [start] = starts;
      [end] = ends;
      if (Date.parse(end) <= Date.parse(start))
        throw new Error("Choose an end time after the start time.");
    } catch (cause) {
      privateState.setError(
        cause instanceof Error ? cause.message : "Choose a valid time window.",
      );
      return;
    }
    const requestedDuration = duration;
    void run(
      (signal) =>
        suggestionsApi.suggest(
          {
            purpose,
            start_at: start,
            end_at: end,
            duration_minutes: requestedDuration,
            include_short_slots: false,
            routine_confirmed: routine === "confirmed",
            travel_assumption: travel,
            preferences_revision: preferences.revision,
            expected_timezone: preferences.timezone,
          },
          signal,
        ),
      ({ snapshot: current, selection }) => {
        if (current.input.timezone !== preferences.timezone)
          throw new Error("Your timezone changed. Reload your saved routine.");
        if (Date.parse(current.expires_at) <= Date.now())
          throw new Error(
            "This check expired before it finished. Find openings again.",
          );
        setOptions(
          selection.suggestions.map((item) => ({
            ...meetingOption(item),
            reasons: item.reasons,
          })),
        );
        setSnapshot(current);
        setSearchRequest({
          start_at: start,
          end_at: end,
          duration_minutes: requestedDuration,
          include_short_slots: false,
          routine_confirmed: routine === "confirmed",
          travel_assumption: travel,
          preferences_revision: preferences.revision,
          expected_timezone: preferences.timezone,
        });
      },
    );
  };
  return (
    <section className={styles.panel} aria-labelledby={`${id}-heading`}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Make room for something</p>
          <h2 id={`${id}-heading`}>When am I free?</h2>
        </div>
        <Link href="/connections">Manage calendar access →</Link>
      </div>
      <p>
        Check connected calendars alongside saved classes, fixed commitments and
        study work. Calendar event names stay private.
      </p>
      {preferences ? (
        <div className={styles.routine}>
          <h3>Your saved routine</h3>
          {preferences.revision === 0 && (
            <p className={styles.warning}>
              These are default hours. Save your routine in Planner before
              treating them as confirmed. You can still review provisional
              windows.
            </p>
          )}
          <p>
            Waking hours: {preferences.wake_time}–{preferences.sleep_time} ·{" "}
            {preferences.timezone.replaceAll("_", " ")}
          </p>
          {preferences.never_schedule.length > 0 ? (
            <ul className={styles.coverage}>
              {[
                ...new Map(
                  preferences.never_schedule.map((window) => [
                    `${window.start_time}:${window.end_time}:${window.weekdays.join(",")}`,
                    window,
                  ]),
                ).values(),
              ].map((window) => (
                <li
                  key={`${window.start_time}:${window.end_time}:${window.weekdays.join(",")}`}
                >
                  Protected:{" "}
                  {window.weekdays
                    .map((day) => studentWeekdays[day])
                    .join(", ")}{" "}
                  · {window.start_time}–{window.end_time}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>
              No additional protected hours are saved.
            </p>
          )}
          <button
            type="button"
            className={styles.secondary}
            disabled={!privateState.ready || privateState.busy}
            onClick={loadPreferences}
          >
            Reload saved routine
          </button>
        </div>
      ) : (
        <div className={styles.actions}>
          <p className={styles.hint}>
            Load your saved routine before checking for openings.
          </p>
          <button
            type="button"
            disabled={!privateState.ready || privateState.busy}
            onClick={loadPreferences}
          >
            {privateState.busy ? "Loading…" : "Load saved routine"}
          </button>
        </div>
      )}
      <form onSubmit={search}>
        <fieldset
          className={styles.fields}
          disabled={!privateState.ready || !preferences || privateState.busy}
        >
          <label htmlFor={`${id}-purpose`}>
            What are you making time for?
            <select
              id={`${id}-purpose`}
              value={purpose}
              onChange={(event) => {
                changeSearch();
                const next = event.target.value as MeetingPurpose;
                setPurpose(next);
                setDuration(
                  next === "lunch" ||
                    next === "project_meeting" ||
                    next === "study_session"
                    ? 60
                    : 30,
                );
              }}
            >
              <option value="other">A meeting</option>
              <option value="lunch">Lunch</option>
              <option value="coffee">Coffee</option>
              <option value="office_hours">Office hours</option>
              <option value="project_meeting">Project meeting</option>
              <option value="study_session">Study session</option>
            </select>
          </label>
          <label htmlFor={`${id}-from`}>
            From · {(preferences?.timezone ?? timezone).replaceAll("_", " ")}
            <input
              id={`${id}-from`}
              type="datetime-local"
              value={from}
              required
              onChange={(event) => {
                changeSearch();
                setFrom(event.target.value);
              }}
            />
          </label>
          <label htmlFor={`${id}-until`}>
            Until · {(preferences?.timezone ?? timezone).replaceAll("_", " ")}
            <input
              id={`${id}-until`}
              type="datetime-local"
              value={until}
              required
              onChange={(event) => {
                changeSearch();
                setUntil(event.target.value);
              }}
            />
          </label>
          <label htmlFor={`${id}-duration`}>
            Meeting duration
            <select
              id={`${id}-duration`}
              value={duration}
              onChange={(event) => {
                changeSearch();
                setDuration(Number(event.target.value));
              }}
            >
              {[15, 30, 45, 60, 90, 120].map((minutes) => (
                <option value={minutes} key={minutes}>
                  {minutes} minutes
                </option>
              ))}
            </select>
          </label>
          <label htmlFor={`${id}-routine`}>
            Is this routine up to date?
            <select
              id={`${id}-routine`}
              value={routine}
              required
              onChange={(event) => {
                changeSearch();
                setRoutine(event.target.value as typeof routine);
              }}
            >
              <option value="">Choose for this search</option>
              <option value="confirmed" disabled={preferences?.revision === 0}>
                Yes, use these hours
              </option>
              <option value="unknown">I haven’t confirmed them</option>
            </select>
          </label>
          <label htmlFor={`${id}-travel`}>
            Travel around commitments
            <select
              id={`${id}-travel`}
              value={travel}
              required
              onChange={(event) => {
                changeSearch();
                setTravel(event.target.value as typeof travel);
              }}
            >
              <option value="">Choose for this search</option>
              <option value="unknown">Travel time is unknown</option>
              <option value="zero">
                Assume no extra travel for this search
              </option>
            </select>
          </label>
        </fieldset>
        <div className={styles.actions}>
          <button
            type="submit"
            disabled={
              !privateState.ready ||
              privateState.busy ||
              !preferences ||
              !routine ||
              !travel
            }
          >
            {privateState.busy ? "Checking…" : "Find openings"}
          </button>
          <span className={styles.hint}>
            10-minute buffers before and after · up to 14 days
          </span>
        </div>
      </form>
      {!privateState.ready && (
        <p role="status">Checking your current account…</p>
      )}
      {notice && <p role="status">{notice}</p>}
      {privateState.error && (
        <p className={styles.error} role="alert">
          {privateState.error}
        </p>
      )}
      {snapshot && (
        <div className={styles.results} role="status">
          <h3>
            {options.length === 2
              ? "Two times to consider"
              : options.length === 1
                ? options[0].displaced_study_block_ids?.length
                  ? "One time to consider"
                  : "One time fits"
                : studyWindows
                  ? "No study-compatible option was confirmed in this check"
                  : "No confirmed option fits this search"}
          </h3>
          {snapshot.result.status === "needs_routine" && (
            <p className={styles.warning}>
              Confirm your saved routine before any time can be called clear.
            </p>
          )}
          <p className={styles.hint}>
            {options.some((option) => option.displaced_study_block_ids?.length)
              ? "Checked against calendar and fixed commitments. Marked study moves still require your exact review."
              : "Clear within the checked sources and saved schedule."}{" "}
            Other commitments may be missing. Times shown in{" "}
            {snapshot.result.timezone.replaceAll("_", " ")}.
          </p>
          {options.length > 0 && (
            <ol className={styles.options}>
              {options.map((option, index) => (
                <PositionedOption
                  option={option}
                  index={index}
                  timezone={snapshot.result.timezone}
                  key={option.start_at}
                />
              ))}
            </ol>
          )}
          {options.length < 2 && (
            <p>
              {studyWindows
                ? "Other study-compatible times may exist. Try a narrower window or shorter duration."
                : options.length === 1
                  ? "A second distinct option did not fit with your duration and buffers."
                  : "NavoX has not invented a free time. Try a different window, confirm missing information or use a shorter duration."}
            </p>
          )}
          {options.some(
            (option) => option.displaced_study_block_ids?.length,
          ) && (
            <p className={styles.hint}>
              Study replacement checks cover a limited set of plans. Narrow your
              search to check other times.
            </p>
          )}
          {snapshot.result.provisional_openings.length > 0 && (
            <details>
              <summary>Windows that still need checking</summary>
              {snapshot.result.provisional_openings
                .slice(0, 3)
                .map((opening) => (
                  <article className={styles.warning} key={opening.start_at}>
                    <p>
                      {timeRange(
                        opening.start_at,
                        opening.end_at,
                        snapshot.result.timezone,
                      )}
                    </p>
                    <ul>
                      {opening.reasons.map((reason) => (
                        <li key={reason}>{reasons[reason]}</li>
                      ))}
                    </ul>
                  </article>
                ))}
              {snapshot.result.provisional_openings.length > 3 && (
                <p className={styles.hint}>
                  More unchecked windows exist. Narrow your search to review
                  them.
                </p>
              )}
            </details>
          )}
          <ul className={styles.coverage} aria-label="Checked source coverage">
            {snapshot.source_summary.map((source, index) => (
              <li key={source.source_id}>
                {sourceLabel(source.source_id, index)}:{" "}
                {source.state === "complete" ? "checked" : "not fully checked"}
                {source.calendar_count > 0
                  ? ` · ${source.calendar_count} calendars`
                  : ""}
              </li>
            ))}
          </ul>
          {snapshot.source_summary.some(
            (source) => source.source_id === "google-calendar-availability",
          ) && (
            <p>
              <Link href="/connections">
                Connect Google Calendar to check its busy times.
              </Link>
            </p>
          )}
          <p className={styles.hint}>
            Checked at{" "}
            {studentClock(snapshot.checked_at, snapshot.result.timezone)}.
            Recheck before booking. No event, invitation, email or study-plan
            change was made.
          </p>
        </div>
      )}
      <StudentCalendarScheduling
        accountId={accountId}
        options={options}
        availability={searchRequest}
      />
    </section>
  );
}
