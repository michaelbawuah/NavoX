"use client";
import { useEffect, useState } from "react";
import {
  readStudyOpenings,
  routineRequest,
  type StudyOpenings,
} from "../lib/student-planning";
export function useStudyOpenings(accountKey: string, days = 1, revision = 0) {
  const [value, setValue] = useState<{
    data: StudyOpenings;
    started: number;
  } | null>(null);
  const [error, setError] = useState(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Plan revisions invalidate previously checked openings.
  useEffect(() => {
    let alive = true,
      working = false;
    setValue(null);
    setError(false);
    const load = async () => {
      if (working || document.visibilityState === "hidden") return;
      working = true;
      const started = Date.now();
      try {
        const data = await routineRequest(
          accountKey,
          `/openings?days=${days}`,
          readStudyOpenings,
        );
        if (alive) {
          setValue({ data, started });
          setError(false);
        }
      } catch {
        if (alive) {
          setValue(null);
          setError(true);
        }
      } finally {
        working = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 60000);
    const visible = () => void load();
    document.addEventListener("visibilitychange", visible);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [accountKey, days, revision]);
  const data =
    value &&
    Date.now() - value.started <
      Date.parse(value.data.expires_at) - Date.parse(value.data.checked_at)
      ? value.data
      : null;
  return { data, error };
}
