"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { uploadCourseMaterial } from "../lib/student-coursework";
import {
  readStudentTimetable,
  type StudentCourse,
} from "../lib/student-schedule";

const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export function VoxTextFile({
  text,
  accountKey,
  onSaved,
}: {
  text: string;
  accountKey: string;
  onSaved: (savedText: string) => void;
}) {
  const [courses, setCourses] = useState<StudentCourse[]>([]),
    [course, setCourse] = useState("");
  const [title, setTitle] = useState("Vox notes"),
    [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [attempt, setAttempt] = useState(0);
  const alive = useRef(true),
    retry = useRef<{ key: string; id: string } | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry explicitly reloads the same private class list.
  useEffect(() => {
    alive.current = true;
    const abort = new AbortController();
    setLoading(true);
    void fetch(`${base}/student/timetable?days=1`, {
      credentials: "include",
      cache: "no-store",
      headers: { "X-NavoX-Account": accountKey },
      signal: AbortSignal.any([abort.signal, AbortSignal.timeout(12000)]),
    })
      .then(async (response) => {
        if (!response.ok) throw Error();
        return readStudentTimetable(await response.json());
      })
      .then((data) => {
        if (!abort.signal.aborted) {
          setCourses(data.courses.filter((row) => !row.archived));
          setNotice("");
        }
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setNotice("Classes couldn’t load. Try again.");
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => {
      alive.current = false;
      abort.abort();
    };
  }, [accountKey, attempt]);
  async function save() {
    if (busy || !course || !title.trim() || [...text].length > 200000) return;
    setBusy(true);
    setNotice("");
    const key = JSON.stringify([course, title.trim(), text]);
    const id =
      retry.current?.key === key ? retry.current.id : crypto.randomUUID();
    retry.current = { key, id };
    try {
      const result = await uploadCourseMaterial(course, {
        title,
        kind: "note",
        revision: 0,
        request_id: id,
        account_key: accountKey,
        content: new Blob([text], { type: "text/plain" }),
        media_type: "text/plain",
      });
      if (!alive.current) return;
      if (!result.available)
        throw Error(
          "The saved file is no longer available. Check your class materials.",
        );
      onSaved(text);
    } catch (error) {
      if (alive.current)
        setNotice(
          error instanceof Error
            ? error.message
            : "The file could not be saved. Try again.",
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section aria-label="Save long text as a file">
      <p>
        Your entire text will be saved privately in the selected class. Saved
        files remain after you hang up; you can delete them from class
        materials.
      </p>
      {loading ? (
        <p role="status">Loading classes…</p>
      ) : (
        <>
          {courses.length ? (
            <>
              <label>
                File title
                <input
                  value={title}
                  maxLength={160}
                  disabled={busy}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label>
                Save to class
                <select
                  value={course}
                  disabled={busy}
                  onChange={(e) => setCourse(e.target.value)}
                >
                  <option value="">Choose a class</option>
                  {courses.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={
                  busy || !course || !title.trim() || [...text].length > 200000
                }
                onClick={() => void save()}
              >
                {busy ? "Saving…" : "Save file to this class"}
              </button>
            </>
          ) : (
            !notice && (
              <p>
                <Link href="/classes">Add a class</Link> before saving a course
                file, or download the text below.
              </p>
            )
          )}
        </>
      )}
      {[...text].length > 200000 && (
        <p>
          Course files support up to 200,000 characters. Download the full text
          below.
        </p>
      )}
      {notice && (
        <p role="status">
          {notice}{" "}
          <button
            type="button"
            disabled={busy}
            onClick={() => setAttempt((value) => value + 1)}
          >
            Reload classes
          </button>
        </p>
      )}
    </section>
  );
}
