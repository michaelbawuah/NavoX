"use client";
import { useEffect, useRef, useState } from "react";
import {
  readStudyPlan,
  readStudyPreview,
  type StudyPlanPreview,
} from "../lib/student-planning";
import { studentRequest } from "../lib/student-schedule";
import type { VoxPlanReview as Proposal } from "../lib/vox-plan-review";
import { StudentStudyPlanPreview } from "./student-daily-plan";
import styles from "./vox-call-controls.module.css";

export function VoxPlanReview({
  proposal,
  accountKey,
  onClose,
}: {
  proposal: Extract<Proposal, { kind: "study-plan" }>;
  accountKey: string;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<StudyPlanPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const requestId = useRef(crypto.randomUUID());
  const alive = useRef(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Retry explicitly reloads the unchanged proposal.
  useEffect(() => {
    let current = true;
    alive.current = true;
    setPreview(null);
    setConfirmed(false);
    setNotice("");
    studentRequest(
      "/planning/previews/" + proposal.preview_id,
      readStudyPreview,
      "GET",
      undefined,
      accountKey,
    )
      .then((value) => {
        if (!current) return;
        if (value.preview_hash !== proposal.preview_hash)
          throw new Error("The proposal changed. Ask Vox for a new preview.");
        setPreview(value);
      })
      .catch((error) => {
        if (current)
          setNotice(
            error instanceof Error
              ? error.message
              : "Could not load this proposal.",
          );
      });
    return () => {
      current = false;
      alive.current = false;
    };
  }, [proposal.preview_id, proposal.preview_hash, accountKey, retry]);
  async function accept() {
    if (!preview || !confirmed || busy) return;
    setBusy(true);
    setNotice("");
    try {
      await studentRequest(
        "/planning/accept",
        readStudyPlan,
        "POST",
        {
          preview_id: preview.preview_id,
          preview_hash: preview.preview_hash,
          request_id: requestId.current,
          confirmed: true,
        },
        accountKey,
      );
      if (!alive.current) return;
      setPreview(null);
      setNotice("Your reviewed study plan is saved.");
      window.dispatchEvent(new Event("navox:study-plan-changed"));
    } catch (error) {
      if (alive.current)
        setNotice(
          error instanceof Error
            ? error.message
            : "Could not confirm the plan. Try again.",
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section className={styles.webResult} aria-label="Vox study plan review">
      {preview ? (
        <StudentStudyPlanPreview
          preview={preview}
          timezone={proposal.timezone}
          busy={busy}
          confirmed={confirmed}
          onConfirm={setConfirmed}
          onAccept={() => void accept()}
          onClose={onClose}
        />
      ) : !notice ? (
        <p role="status">Opening your study plan review…</p>
      ) : null}
      {notice && <p role="status">{notice}</p>}
      {!preview && notice && !notice.includes("is saved") && (
        <button type="button" onClick={() => setRetry((v) => v + 1)}>
          Retry review
        </button>
      )}
      {!preview && (
        <button type="button" onClick={onClose}>
          Close review
        </button>
      )}
    </section>
  );
}
