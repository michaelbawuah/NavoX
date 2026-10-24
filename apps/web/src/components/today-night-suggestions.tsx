"use client";
import { useState } from "react";
import type {
  StudyOpenings,
  StudyPlan,
  StudyTask,
} from "../lib/student-planning";
import { studentClock } from "../lib/student-schedule";
import { lateNightStudyMove } from "../lib/today-suggestions";
import { StudyBlockMove } from "./study-block-move";
import styles from "./today-home.module.css";

export function TodayNightSuggestions({
  accountKey,
  timezone,
  now,
  plan,
  tasks,
  openings,
  onSaved,
}: {
  accountKey: string;
  timezone: string;
  now: number;
  plan: StudyPlan | null;
  tasks: StudyTask[];
  openings: StudyOpenings | null;
  onSaved: () => void;
}) {
  const [dismissed, setDismissed] = useState<string | null>(null);
  const suggestion = lateNightStudyMove(plan, tasks, openings, now, timezone);
  if (!suggestion) return null;
  const key = `${suggestion.block.id}:${suggestion.block.revision}:${suggestion.start_at}`;
  if (dismissed === key) return null;
  return (
    <section className={styles.nightActions} aria-label="A later study time">
      <h2>Give your morning some room</h2>
      <p>
        Move {suggestion.block.title} from{" "}
        {studentClock(suggestion.block.start_at, timezone)}
        {" to "}
        {studentClock(suggestion.start_at, timezone)} today?
      </p>
      <p>
        Your calendar and routine were checked. Review the exact change before
        saving.
      </p>
      <StudyBlockMove
        key={key}
        block={suggestion.block}
        accountKey={accountKey}
        timezone={timezone}
        now={now}
        suggestedStart={suggestion.start_at}
        onSaved={onSaved}
        onKeepCurrent={() => setDismissed(key)}
      />
    </section>
  );
}
