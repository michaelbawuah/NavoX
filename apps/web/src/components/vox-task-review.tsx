"use client";
import { useEffect, useRef, useState } from "react";
import {
  readStudyTask,
  readTaskChangePreview,
  routineRequest,
  type TaskChangePreview,
} from "../lib/student-planning";
import type { VoxPlanReview } from "../lib/vox-plan-review";
import { TaskChangeReview } from "./task-change-review";
import styles from "./vox-call-controls.module.css";
export function VoxTaskReview({
  proposal,
  accountKey,
  onClose,
}: {
  proposal: Extract<VoxPlanReview, { kind: "task" }>;
  accountKey: string;
  onClose: () => void;
}) {
  const [review, setReview] = useState<TaskChangePreview | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const operation = useRef(false);
  const path = proposal.task_id ? `/tasks/${proposal.task_id}` : "/tasks";
  const payload = {
    ...proposal.task,
    request_id: proposal.preview_id,
    ...(proposal.task_id ? { revision: proposal.revision } : {}),
  };
  // biome-ignore lint/correctness/useExhaustiveDependencies: The proposal is immutable and keyed by request ID; retry reloads its exact review.
  useEffect(() => {
    let current = true;
    ++generation.current;
    operation.current = false;
    setBusy(false);
    setReview(null);
    setNotice("");
    routineRequest(
      accountKey,
      `${path}/review`,
      readTaskChangePreview,
      "POST",
      payload,
    )
      .then((value) => {
        if (!current) return;
        if (value.preview_hash !== proposal.preview_hash)
          throw new Error(
            "Your tasks or schedule changed. Ask Vox for a fresh review.",
          );
        setReview(value);
      })
      .catch((error) => {
        if (current)
          setNotice(
            error instanceof Error
              ? error.message
              : "Could not check this task.",
          );
      });
    return () => {
      current = false;
      ++generation.current;
    };
  }, [accountKey, proposal.preview_id, proposal.preview_hash, retry]);
  async function approve() {
    if (!review || operation.current) return;
    const ownerGeneration = generation.current;
    operation.current = true;
    setBusy(true);
    setNotice("");
    try {
      await routineRequest(
        accountKey,
        `${path}/confirm`,
        readStudyTask,
        "POST",
        { ...payload, preview_hash: review.preview_hash },
      );
      if (generation.current !== ownerGeneration) return;
      setReview(null);
      setNotice("Task saved. Ask Vox to plan it around your day.");
      window.dispatchEvent(new Event("navox:study-plan-changed"));
    } catch (error) {
      if (generation.current === ownerGeneration)
        setNotice(
          error instanceof Error
            ? error.message
            : "Could not confirm this task.",
        );
    } finally {
      if (generation.current === ownerGeneration) {
        operation.current = false;
        setBusy(false);
      }
    }
  }
  return (
    <section className={styles.webResult} aria-label="Vox task review">
      {review ? (
        <TaskChangeReview
          preview={review}
          busy={busy}
          onApprove={() => void approve()}
          onCancel={onClose}
        />
      ) : !notice ? (
        <p role="status">Checking your task details…</p>
      ) : null}
      {notice && <p role="status">{notice}</p>}
      {!review && notice && !notice.startsWith("Task saved.") && (
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
