"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { StudyTask } from "../lib/student-planning";
import { studentClock } from "../lib/student-schedule";
import { type StudyWindowIntent, studyWindowKey } from "../lib/study-placement";
import styles from "./today-home.module.css";

export function TodayFreeWindow({
  accountKey,
  start,
  end,
  timezone,
  tasks,
}: {
  accountKey: string;
  start: string;
  end: string;
  timezone: string;
  tasks: StudyTask[];
}) {
  const router = useRouter();
  const [notice, setNotice] = useState("");
  const task = tasks
    .filter(
      (row) =>
        row.status === "active" &&
        row.remaining_minutes > 0 &&
        !row.source_needs_review &&
        (!row.due_at || Date.parse(row.due_at) > Date.parse(start)),
    )
    .sort(
      (a, b) =>
        (a.due_at ? Date.parse(a.due_at) : Infinity) -
        (b.due_at ? Date.parse(b.due_at) : Infinity),
    )[0];
  function open() {
    const intent: StudyWindowIntent = {
      accountKey,
      start_at: start,
      end_at: end,
      timezone,
      task_id: task?.id ?? null,
      created_at: new Date().toISOString(),
    };
    try {
      sessionStorage.setItem(
        studyWindowKey(accountKey),
        JSON.stringify(intent),
      );
    } catch {
      setNotice(
        "This browser couldn’t keep the selected window. Open Planner to choose a time.",
      );
      return;
    }
    router.push("/planner#study-window");
    window.dispatchEvent(new Event("navox:study-window-ready"));
  }
  return (
    <div>
      <button type="button" className={styles.rowTop} onClick={open}>
        <time dateTime={start}>{studentClock(start, timezone)}</time>
        <span className={styles.rowText}>
          <strong>Free until {studentClock(end, timezone)}</strong>
          <span>{task ? `Plan ${task.title} here` : "Plan new work here"}</span>
        </span>
        <span>Review →</span>
      </button>
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
