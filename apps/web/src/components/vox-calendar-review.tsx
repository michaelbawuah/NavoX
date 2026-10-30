"use client";
import Link from "next/link";
import type { VoxCalendarReview as Review } from "../lib/vox-calendar-review";
import styles from "./vox-call-controls.module.css";

function message(proposal: Review) {
  switch (proposal.status) {
    case "pending":
      return proposal.voice_approval_available
        ? "Listen to Vox’s complete action read-back before answering. You can also review and approve the exact details on screen."
        : "Review the exact event details and approve on screen before an event is added.";
    case "executing":
      return "The approved calendar action is being checked. Wait for a verified result.";
    case "uncertain":
      return "The event result has not been confirmed. Check calendar activity before trying again. Activity may include separately approved study-plan changes.";
    case "created":
      return proposal.voice_approval_available
        ? "Event creation was verified. Vox can read back an exact Undo action for your review; nothing has been deleted by opening this card."
        : "Event creation was verified. Check the saved receipt for current details and available actions.";
    case "changed":
      return "The calendar receipt changed. Review the current details before another action.";
    case "undoing":
      return "The approved calendar Undo is being checked. Wait for a verified result.";
    case "undone":
      return "The exact calendar Undo was verified. Review activity for any separately approved study-plan changes.";
    case "failed":
      return "The calendar action did not complete as requested. Check activity; separately approved study-plan changes may still be present.";
  }
}
export function VoxCalendarReview({
  proposal,
  onReview,
  onClose,
}: {
  proposal: Review;
  onReview: () => void;
  onClose: () => void;
}) {
  return (
    <section className={styles.webResult} aria-label="Vox calendar review">
      <h3>Calendar review</h3>
      <p role="status">{message(proposal)}</p>
      <p>
        Open calendar activity to fetch the current, account-checked receipt.
      </p>
      <Link href="/connections#calendar" onClick={onReview}>
        Review calendar activity
      </Link>
      <button type="button" className={styles.secondary} onClick={onClose}>
        Dismiss calendar review
      </button>
    </section>
  );
}
