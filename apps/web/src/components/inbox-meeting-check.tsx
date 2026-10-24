"use client";

import { useEffect, useId, useMemo, useState } from "react";
import {
  meetingAlternatives,
  meetingCheck,
  meetingTimeLabel,
} from "../lib/inbox-availability";
import {
  type InboxMeetingSource,
  inferInboxMeeting,
} from "../lib/inbox-meeting-proposal";
import {
  type AvailabilitySnapshot,
  createStudentCalendarApi,
} from "../lib/student-calendar-api";
import { observeCalendarAccessChanges } from "../lib/student-calendar-privacy";
import {
  studyDeadlineChoices,
  studyLocalDateTime,
} from "../lib/student-planning";
import { addStudentDays, studentDateInZone } from "../lib/student-schedule";
import styles from "./communication-drafts.module.css";
import { useCalendarWorkspace } from "./use-calendar-workspace";

interface Props {
  accountKey: string;
  timezone: string;
  disabled: boolean;
  source?: InboxMeetingSource | null;
  onUseTimes: (instructions: string) => void;
}

/** Changing the selected source or owner retires every prior review and check. */
export function InboxMeetingCheck(props: Props) {
  const source =
    props.source?.accountKey === props.accountKey ? props.source : null;
  return (
    <MeetingReview
      key={`${props.accountKey}:${props.timezone}:${source?.key ?? "manual"}`}
      {...props}
      source={source}
    />
  );
}

function MeetingReview({
  accountKey,
  timezone,
  disabled,
  source,
  onUseTimes,
}: Props) {
  const id = useId();
  const api = useMemo(() => createStudentCalendarApi(accountKey), [accountKey]);
  const proposal = useMemo(
    () => (source ? inferInboxMeeting(source, timezone) : null),
    [source, timezone],
  );
  const [local, setLocal] = useState(proposal?.localStart ?? "");
  const [duration, setDuration] = useState(proposal?.durationMinutes ?? 30);
  // A review binds to the exact interpreted source, including a later reread.
  const [review, setReview] = useState<{ proposal: typeof proposal } | null>(
    null,
  );
  const reviewed = review !== null && review.proposal === proposal;
  const [routine, setRoutine] = useState(false);
  const [travel, setTravel] = useState(false);
  const [checked, setChecked] = useState<{
    snapshot: AvailabilitySnapshot;
    start: string;
    proposal: typeof proposal;
  } | null>(null);
  const snapshot =
    checked && checked.proposal === proposal ? checked.snapshot : null;
  const [notice, setNotice] = useState("");
  const state = useCalendarWorkspace(accountKey, api, () => {
    setChecked(null);
    setReview(null);
    setNotice("");
  });
  const { cancel, run } = state;
  useEffect(() => {
    cancel();
    setLocal(proposal?.localStart ?? "");
    setDuration(proposal?.durationMinutes ?? 30);
    setReview(null);
    setChecked(null);
    setNotice("");
  }, [proposal, cancel]);
  useEffect(
    () =>
      observeCalendarAccessChanges(() => {
        cancel();
        setChecked(null);
        setReview(null);
        setNotice("Calendar access changed. Review and check this time again.");
      }),
    [cancel],
  );
  useEffect(() => {
    if (!snapshot) return;
    const expire = () => {
      setChecked(null);
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
  const changed = (clearReview = true) => {
    cancel();
    setChecked(null);
    if (clearReview) setReview(null);
    setNotice("");
    state.setError("");
  };
  async function check() {
    if (!reviewed || disabled || state.busy || !state.ready) return;
    changed(false);
    await run(async (signal) => {
      const preferences = await api.preferences(signal);
      if (signal.aborted) throw new Error("This meeting check was cancelled.");
      if (preferences.timezone !== timezone)
        throw new Error(
          "Your timezone changed. Open Inbox again to check the time in your saved timezone.",
        );
      if (!Number.isInteger(duration) || duration < 1 || duration > 480)
        throw new Error("Choose a duration from 1 to 480 minutes.");
      let choices: string[];
      try {
        choices = studyDeadlineChoices(local, timezone);
      } catch {
        throw new Error("Choose a valid meeting date and time.");
      }
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
      return { snapshot: result, start, proposal };
    }, setChecked);
  }
  const verdict =
    snapshot && checked
      ? meetingCheck(snapshot, checked.start, duration)
      : null;
  const alternatives = snapshot ? meetingAlternatives(snapshot, duration) : [];
  const choices =
    snapshot && checked && verdict === "clear"
      ? [
          {
            start_at: checked.start,
            end_at: new Date(
              Date.parse(checked.start) + duration * 60_000,
            ).toISOString(),
          },
        ]
      : alternatives;
  function useTimes() {
    if (
      !snapshot ||
      !reviewed ||
      !state.ready ||
      disabled ||
      !choices.length ||
      Date.parse(snapshot.expires_at) <= Date.now()
    ) {
      setChecked(null);
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
  const hasProposal = proposal !== null && proposal.state !== "none";
  let sent: string | null = null;
  try {
    if (source?.sentAt && Number.isFinite(Date.parse(source.sentAt)))
      sent = studyLocalDateTime(source.sentAt, timezone).replace("T", " at ");
  } catch {
    // The proposal already explains an unreadable saved timezone.
  }
  return (
    <details className={styles.notice} open={hasProposal}>
      <summary>
        {hasProposal
          ? "Review the meeting proposed in this email"
          : "Check a meeting time before replying"}
      </summary>
      {!state.ready ? (
        <p>Checking your account before showing this meeting review.</p>
      ) : (
        <>
          {hasProposal && proposal ? (
            <section
              aria-label="Meeting source evidence"
              className={styles.meetingEvidence}
            >
              <p>
                {proposal.state === "proposed"
                  ? "This is an interpretation to review, not confirmed availability."
                  : "The meeting details need your correction before availability can be checked."}
              </p>
              {sent && (
                <p>
                  Email sent {sent} · {timezone.replaceAll("_", " ")}
                </p>
              )}
              {proposal.evidence.map((evidence) => (
                <div key={`${evidence.field}:${evidence.start}`}>
                  <p>
                    <strong>
                      {evidence.field === "subject"
                        ? "Subject"
                        : "Email passage"}
                    </strong>
                  </p>
                  <blockquote>{evidence.quote}</blockquote>
                </div>
              ))}
              {proposal.issues.length > 0 && (
                <ul>
                  {proposal.issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              )}
              {proposal.assumptions.length > 0 && (
                <ul aria-label="Interpretation assumptions">
                  {proposal.assumptions.map((assumption) => (
                    <li key={assumption}>{assumption}</li>
                  ))}
                </ul>
              )}
            </section>
          ) : (
            <p>
              {source
                ? "No clear meeting proposal was found in this email. You can enter a meeting time yourself."
                : "Open the exact email you are replying to for a source-linked meeting review, or enter a meeting time yourself."}
            </p>
          )}
          <p>
            Review the intended date, time and duration in your saved timezone.
            Checking uses your current calendars, classes, study work and saved
            routine.
          </p>
          <fieldset
            className={styles.meetingFields}
            disabled={disabled || state.busy}
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
            <label htmlFor={`${id}-duration`}>Duration in minutes</label>
            <input
              id={`${id}-duration`}
              type="number"
              min={1}
              max={480}
              step={1}
              value={Number.isFinite(duration) && duration > 0 ? duration : ""}
              onChange={(event) => {
                changed();
                setDuration(Number(event.target.value));
              }}
            />
            <label>
              <input
                type="checkbox"
                checked={reviewed}
                onChange={(event) => {
                  changed(false);
                  setReview(event.target.checked ? { proposal } : null);
                }}
              />
              I checked the source, date, time, timezone and duration above.
            </label>
            <label>
              <input
                type="checkbox"
                checked={routine}
                onChange={(event) => {
                  changed(false);
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
                  changed(false);
                  setTravel(event.target.checked);
                }}
              />
              Assume no extra travel around commitments for this check.
            </label>
            <button
              type="button"
              disabled={
                !reviewed ||
                !local ||
                !Number.isInteger(duration) ||
                duration < 1 ||
                duration > 480 ||
                state.busy
              }
              onClick={() => void check()}
            >
              {state.busy ? "Checking…" : "Check this time"}
            </button>
          </fieldset>
          <p>
            Ten minutes before and after are kept clear. Missing calendar access
            or unknown travel stays unconfirmed. No calendar event is created.
          </p>
          {snapshot && (
            <div role="status">
              <p>
                {verdict === "clear"
                  ? "This time fits your checked schedule."
                  : verdict === "conflict"
                    ? "This time conflicts with your saved schedule or its buffers."
                    : "This time could not be confirmed. Check your calendar access, routine and travel assumptions."}
              </p>
              {choices.length > 0 ? (
                <>
                  {verdict !== "clear" && (
                    <p>
                      {choices.length === 1
                        ? "One checked alternative:"
                        : "Two checked alternatives:"}
                    </p>
                  )}
                  <ul>
                    {choices.map((choice) => (
                      <li key={choice.start_at}>
                        {meetingTimeLabel(
                          choice.start_at,
                          choice.end_at,
                          timezone,
                        )}
                      </li>
                    ))}
                  </ul>
                  <button type="button" disabled={disabled} onClick={useTimes}>
                    Use {choices.length === 1 ? "this time" : "these times"} in
                    reply instructions
                  </button>
                </>
              ) : (
                <p>
                  No confirmed alternative was found in this seven-day check.
                </p>
              )}
              <p>
                Only checked sources are covered. This does not book a meeting
                or send an email.
              </p>
            </div>
          )}
        </>
      )}
      {state.error && <p role="alert">{state.error}</p>}
      {notice && <p role="status">{notice}</p>}
    </details>
  );
}
