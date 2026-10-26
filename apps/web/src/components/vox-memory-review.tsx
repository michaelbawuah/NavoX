"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  type VoxMemoryReview as MemoryReview,
  saveMemoryForgetDraft,
} from "../lib/vox-memory-review";
import styles from "./vox-call-controls.module.css";

export function VoxMemoryReview({
  proposal,
  accountKey,
  onReview,
  onClose,
}: {
  proposal: MemoryReview;
  accountKey: string;
  onReview: () => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const [notice, setNotice] = useState("");
  function open() {
    try {
      saveMemoryForgetDraft(window.sessionStorage, accountKey, proposal.query);
    } catch {
      setNotice(
        "Open Settings → Memory & privacy and search for this subject. Nothing has been deleted.",
      );
      return;
    }
    onReview();
    router.push("/settings?section=memory#memory-privacy");
    window.dispatchEvent(new Event("navox:memory-forget-ready"));
  }
  return (
    <section className={styles.webResult} aria-label="Vox memory review">
      <h3>Review what to forget</h3>
      <p>Possible matches for “{proposal.query}”. Nothing has been deleted.</p>
      {proposal.matches.length ? (
        <ul>
          {proposal.matches.map((row) => (
            <li key={`${row.kind}:${row.id}`}>
              {row.label || "Untitled memory"} ·{" "}
              {row.kind === "conversation"
                ? "Conversation"
                : row.kind === "legacy_fact"
                  ? "Writing preference"
                  : "Saved fact"}
            </li>
          ))}
        </ul>
      ) : (
        <p>No matching memory was found in this check.</p>
      )}
      {(proposal.has_more_matches || !proposal.search_complete) && (
        <p>More memories may match. Check Settings before choosing.</p>
      )}
      <p>Review the source and exactly what will be removed in Settings.</p>
      <button type="button" onClick={open}>
        Review in Settings
      </button>
      <button type="button" onClick={onClose}>
        Keep my memory
      </button>
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
