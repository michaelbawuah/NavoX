"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { studentClassDraftKey } from "../lib/student-voice";
import type { VoxPlanReview } from "../lib/vox-plan-review";
import styles from "./vox-call-controls.module.css";

/** A private draft handoff only. The existing class and meeting editors own saving. */
export function VoxClassReview({
  proposal,
  accountKey,
  onClose,
}: {
  proposal: Extract<VoxPlanReview, { kind: "class" }>;
  accountKey: string;
  onClose: () => void;
}) {
  const router = useRouter();
  const [notice, setNotice] = useState("");
  function open() {
    try {
      window.sessionStorage.setItem(
        studentClassDraftKey(accountKey),
        JSON.stringify({
          accountKey,
          transcript: proposal.transcript,
          createdAt: new Date().toISOString(),
        }),
      );
    } catch {
      setNotice(
        "This browser could not carry the note over. Open Classes and enter these details.",
      );
      return;
    }
    onClose();
    // This keeps the app-scoped Vox connection alive while opening editable fields.
    router.push("/classes?add=1");
    window.dispatchEvent(new Event("navox:class-draft-ready"));
  }
  return (
    <section className={styles.webResult} aria-label="Vox class review">
      <h3>Review your class</h3>
      <p>{proposal.transcript}</p>
      <p>
        Check the course and term dates, then its days, times and location.
        Nothing has been added yet.
      </p>
      <button type="button" onClick={open}>
        Open class review
      </button>
      <button type="button" onClick={onClose}>
        Dismiss
      </button>
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
