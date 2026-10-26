"use client";
import { useEffect, useId, useRef, useState } from "react";
import {
  conversationMemoryRequest,
  MemoryClientError,
  readMemoryFacts,
} from "../lib/conversation-memory";
import {
  consumeMemoryCandidateDraft,
  type MemorySaveCandidate,
} from "../lib/vox-memory-save-review";
import styles from "./memory-privacy.module.css";

export function MemorySaveReviewPanel({
  accountKey,
  disabled,
  onSaved,
  onUncertain,
}: {
  accountKey: string;
  disabled: boolean;
  onSaved: () => void;
  onUncertain: () => void;
}) {
  const [review, setReview] = useState<MemorySaveCandidate | null>(null);
  const [reviewOwner, setReviewOwner] = useState(accountKey);
  const [agreement, setAgreement] = useState(false);
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [notice, setNotice] = useState("");
  const epoch = useRef(0);
  const id = useId();
  useEffect(() => {
    const load = () => {
      let candidate: MemorySaveCandidate | null = null;
      try {
        candidate = consumeMemoryCandidateDraft(
          window.sessionStorage,
          accountKey,
        );
      } catch {
        /* The manual fact editor remains available. */
      }
      if (!candidate) return;
      ++epoch.current;
      setReviewOwner(accountKey);
      setReview(candidate);
      setAgreement(false);
      setBusy(false);
      setLocked(false);
      setNotice("");
    };
    load();
    window.addEventListener("navox:memory-save-review-ready", load);
    return () => {
      ++epoch.current;
      window.removeEventListener("navox:memory-save-review-ready", load);
    };
  }, [accountKey]);
  useEffect(() => {
    if (!review || busy) return;
    const timer = setTimeout(
      () => {
        ++epoch.current;
        setReview(null);
        setNotice("That memory review expired. Ask Vox for a new review.");
      },
      Math.max(0, Date.parse(review.expires_at) - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [review, busy]);
  async function save() {
    if (
      !review ||
      reviewOwner !== accountKey ||
      disabled ||
      busy ||
      locked ||
      (review.sensitive && !agreement)
    )
      return;
    const current = epoch.current;
    setBusy(true);
    setNotice("");
    try {
      await conversationMemoryRequest(
        accountKey,
        "/candidates/confirm",
        (value) => readMemoryFacts([value])[0],
        "POST",
        {
          review_id: review.id,
          proposal: review.proposal,
          confirmation: "SAVE MEMORY",
          sensitive_agreement: agreement,
        },
      );
      if (current !== epoch.current) return;
      setReview(null);
      setNotice("Memory saved. You can correct or forget it below.");
      onSaved();
    } catch (cause) {
      if (current !== epoch.current) return;
      setLocked(true);
      if (cause instanceof MemoryClientError && cause.uncertain) {
        setNotice(
          "The save result could not be confirmed. Reload your memory before making another change.",
        );
        onUncertain();
      } else
        setNotice(
          "This review could not be saved. It may have expired or changed. Ask Vox for a new review.",
        );
    } finally {
      if (current === epoch.current) setBusy(false);
    }
  }
  if (reviewOwner !== accountKey) return null;
  if (!review)
    return notice ? (
      <p role="status" className={styles.notice}>
        {notice}
      </p>
    ) : null;
  return (
    <section className={styles.group} aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>Save this memory?</h3>
      <p>
        <strong>{review.label}</strong>
      </p>
      <p className={styles.prewrap}>{review.content}</p>
      <p className={styles.muted}>
        From a completed Vox turn. This exact statement will be linked to its
        source conversation. You can edit or delete the saved fact later.
      </p>
      {review.sensitive && (
        <p className={styles.sensitiveNote}>
          This may contain private details. It stays out of voice recall and
          will not be spoken automatically.
        </p>
      )}
      <label className={styles.check}>
        <input
          type="checkbox"
          checked={agreement}
          disabled={disabled || busy || locked}
          onChange={(e) => setAgreement(e.target.checked)}
        />
        <span>
          I separately agree to save any health, family or other private details
          in this exact statement{review.sensitive ? " (required)." : "."}
        </span>
      </label>
      <div className={styles.actions}>
        <button
          type="button"
          disabled={
            disabled || busy || locked || (review.sensitive && !agreement)
          }
          onClick={() => void save()}
        >
          {busy ? "Saving…" : "Save this memory"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            ++epoch.current;
            setReview(null);
            setNotice("This statement was not saved as a fact.");
          }}
        >
          Don’t save this
        </button>
      </div>
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
