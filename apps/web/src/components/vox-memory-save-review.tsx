"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  type VoxMemorySaveReview as Review,
  saveMemoryCandidateDraft,
} from "../lib/vox-memory-save-review";
import styles from "./vox-call-controls.module.css";

export function VoxMemorySaveReview({
  proposal,
  accountKey,
  onReview,
  onClose,
}: {
  proposal: Review;
  accountKey: string;
  onReview: () => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const timer = setTimeout(
      onClose,
      Math.max(0, Date.parse(proposal.review.expires_at) - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [proposal.review.expires_at, onClose]);
  function open() {
    try {
      saveMemoryCandidateDraft(
        window.sessionStorage,
        accountKey,
        proposal.review,
      );
    } catch {
      setNotice(
        "This private review could not be opened. Ask Vox for a new review, or add the fact in Settings.",
      );
      return;
    }
    onReview();
    router.push("/settings#memory-privacy");
    window.dispatchEvent(new Event("navox:memory-save-review-ready"));
  }
  return (
    <section className={styles.webResult} aria-label="Vox memory save review">
      <h3>Review a memory</h3>
      <p>
        Review this exact statement before saving it as a fact. Opening this
        review does not save a new fact.
      </p>
      <p>
        <strong>{proposal.review.label}</strong>
      </p>
      <p>{proposal.review.content}</p>
      {proposal.review.sensitive && (
        <p>
          This may contain private details. Review it in Settings; it will not
          be spoken automatically.
        </p>
      )}
      <button type="button" className={styles.secondary} onClick={open}>
        Review in Settings
      </button>
      <button type="button" className={styles.secondary} onClick={onClose}>
        Don’t save this
      </button>
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
