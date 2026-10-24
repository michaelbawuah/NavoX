"use client";
import { useEffect, useRef, useState } from "react";
import {
  type RoutinePreview,
  readRoutinePreview,
  readStudyPreferences,
  routineRequest,
  studyPreferencesDraft,
} from "../lib/student-planning";
import type { VoxPlanReview } from "../lib/vox-plan-review";
import { RoutineChangeReview } from "./routine-change-review";
import styles from "./vox-call-controls.module.css";

export function VoxRoutineReview({
  proposal,
  accountKey,
  onClose,
}: {
  proposal: Extract<VoxPlanReview, { kind: "routine" }>;
  accountKey: string;
  onClose: () => void;
}) {
  const [review, setReview] = useState<RoutinePreview | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const alive = useRef(false);
  const payload = {
    ...studyPreferencesDraft(proposal.preferences),
    request_id: proposal.preview_id,
  };
  // biome-ignore lint/correctness/useExhaustiveDependencies: A fixed proposal is keyed by its request ID; retry reloads its exact review.
  useEffect(() => {
    let current = true;
    alive.current = true;
    setReview(null);
    setNotice("");
    routineRequest(
      accountKey,
      "/preferences/preview",
      readRoutinePreview,
      "POST",
      payload,
    )
      .then((value) => {
        if (!current) return;
        if (value.preview_hash !== proposal.preview_hash)
          throw new Error(
            "Your routine or schedule changed. Ask Vox for a fresh review.",
          );
        setReview(value);
      })
      .catch((error) => {
        if (current)
          setNotice(
            error instanceof Error
              ? error.message
              : "Could not check these changes.",
          );
      });
    return () => {
      current = false;
      alive.current = false;
    };
  }, [accountKey, proposal.preview_id, proposal.preview_hash, retry]);
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
        { ...payload, preview_hash: review.preview_hash },
      );
      if (!alive.current) return;
      setReview(null);
      setNotice("Your reviewed routine and alarm changes are saved.");
      window.dispatchEvent(new Event("navox:study-plan-changed"));
      window.dispatchEvent(new Event("navox:routine-changed"));
    } catch (error) {
      if (alive.current)
        setNotice(
          error instanceof Error
            ? error.message
            : "Could not confirm these changes.",
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section className={styles.webResult} aria-label="Vox routine review">
      <p>Keep NavoX open for web alarms. They cannot ring a locked phone.</p>
      {review ? (
        <RoutineChangeReview
          preview={review}
          draft={proposal.preferences}
          busy={busy}
          onApprove={() => void approve()}
          onCancel={onClose}
        />
      ) : !notice ? (
        <p role="status">Checking your routine and alarm changes…</p>
      ) : null}
      {notice && <p role="status">{notice}</p>}
      {!review && notice && !notice.includes("are saved") && (
        <button type="button" onClick={() => setRetry((v) => v + 1)}>
          Retry review
        </button>
      )}
      {!review && (
        <button type="button" onClick={onClose}>
          Close review
        </button>
      )}
    </section>
  );
}
