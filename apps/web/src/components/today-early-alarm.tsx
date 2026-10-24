"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type AlarmOccurrence,
  type RoutinePreview,
  readRoutinePreview,
  readStudyPreferences,
  routineRequest,
  type StudyPreferences,
  studyPreferencesDraft,
} from "../lib/student-planning";
import {
  readStudentTimetable,
  studentClock,
  studentRequest,
} from "../lib/student-schedule";
import { earlyClassAlarm } from "../lib/today-suggestions";
import { RoutineChangeReview } from "./routine-change-review";
import styles from "./today-home.module.css";

type Suggestion = NonNullable<ReturnType<typeof earlyClassAlarm>>;
type Review = {
  preview: RoutinePreview;
  draft: StudyPreferences;
  requestId: string;
};
type Props = {
  accountKey: string;
  alarm: AlarmOccurrence;
  timezone: string;
  now: number;
  onSaved: () => void;
};

export function TodayEarlyAlarm(props: Props) {
  return (
    <TodayEarlyAlarmAccount
      key={`${props.accountKey}:${props.alarm.day}:${props.alarm.enabled}:${props.alarm.scheduled_at}:${props.alarm.first_class_at}`}
      {...props}
    />
  );
}
function TodayEarlyAlarmAccount({
  accountKey,
  alarm,
  timezone,
  now,
  onSaved,
}: Props) {
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [notice, setNotice] = useState("");
  const alive = useRef(false);
  const epoch = useRef(0);
  const latest = useRef({ alarm, now });
  latest.current = { alarm, now };
  const load = useCallback(async () => {
    const turn = ++epoch.current;
    try {
      const [preferences, timetable] = await Promise.all([
        routineRequest(accountKey, "/preferences", readStudyPreferences),
        studentRequest(
          `/timetable?date=${alarm.day}&days=1`,
          readStudentTimetable,
          "GET",
          undefined,
          accountKey,
        ),
      ]);
      if (!alive.current || turn !== epoch.current) return;
      setSuggestion(
        earlyClassAlarm(
          preferences,
          timetable,
          latest.current.alarm,
          alarm.day,
          latest.current.now,
        ),
      );
    } catch {
      if (alive.current && turn === epoch.current) setSuggestion(null);
    }
  }, [accountKey, alarm.day]);
  useEffect(() => {
    alive.current = true;
    setReview(null);
    setSuggestion(null);
    void load();
    const refresh = () => {
      setReview(null);
      void load();
    };
    window.addEventListener("navox:routine-changed", refresh);
    return () => {
      alive.current = false;
      ++epoch.current;
      window.removeEventListener("navox:routine-changed", refresh);
    };
  }, [load]);
  async function prepare() {
    if (!suggestion || busy) return;
    setBusy(true);
    setNotice("");
    setUncertain(false);
    const requestId = crypto.randomUUID();
    const draft = suggestion.preferences;
    try {
      const preview = await routineRequest(
        accountKey,
        "/preferences/preview",
        readRoutinePreview,
        "POST",
        {
          ...studyPreferencesDraft(draft),
          request_id: requestId,
        },
      );
      if (alive.current) setReview({ preview, draft, requestId });
    } catch {
      if (alive.current)
        setNotice(
          "Your schedule may have changed. Check it again before reviewing.",
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function approve() {
    if (!review || busy) return;
    setBusy(true);
    setNotice("");
    try {
      await routineRequest(
        accountKey,
        "/preferences/confirm",
        readStudyPreferences,
        "POST",
        {
          ...studyPreferencesDraft(review.draft),
          request_id: review.requestId,
          preview_hash: review.preview.preview_hash,
        },
      );
      if (!alive.current) return;
      setReview(null);
      setSuggestion(null);
      setNotice(
        "Your reviewed wake-up plan is saved. Keep your phone alarm set until native ringing is available.",
      );
      window.dispatchEvent(new Event("navox:study-plan-changed"));
      window.dispatchEvent(new Event("navox:routine-changed"));
      onSaved();
    } catch {
      if (alive.current) {
        // Retain the same reviewed request ID so an uncertain response can be retried safely.
        setUncertain(true);
        setNotice(
          "Couldn’t confirm the result. Retry this approval to check the same change. Closing this review will not undo a change that may have saved.",
        );
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  if (dismissed || (!suggestion && !notice)) return null;
  return (
    <section
      className={styles.nightActions}
      aria-label="Earlier wake-up suggestion"
    >
      {suggestion && (
        <>
          <h2>
            {suggestion.first.kind === "exam"
              ? "An early exam"
              : "An early class"}
          </h2>
          <p>
            {suggestion.first.course_code || suggestion.first.course_name}{" "}
            starts at {studentClock(suggestion.first.start_at, timezone)}. With
            your saved preparation and walk time, wake at{" "}
            {studentClock(suggestion.scheduled_at, timezone)}?
          </p>
          <p>
            Smart mode adjusts enabled class-day alarms. Review every affected
            day and study block before approving.
          </p>
        </>
      )}
      {review ? (
        <RoutineChangeReview
          preview={review.preview}
          draft={review.draft}
          busy={busy}
          onApprove={() => void approve()}
          onCancel={() => setDismissed(true)}
          cancelLabel={uncertain ? "Close review" : "Keep current settings"}
        />
      ) : suggestion ? (
        <div>
          <button type="button" disabled={busy} onClick={() => void prepare()}>
            {busy ? "Checking…" : "Review alarm change"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setDismissed(true)}
          >
            Keep current alarm
          </button>
        </div>
      ) : null}
      {notice && <p role="status">{notice}</p>}
      {notice && !review && suggestion && (
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setNotice("");
            void load();
          }}
        >
          Check again
        </button>
      )}
    </section>
  );
}
