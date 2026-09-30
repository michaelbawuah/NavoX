"use client";

import { useRef, useState } from "react";
import { SearchRequestError, searchRequest } from "../lib/search";

export function KnowledgeRefresh({ resourceId }: { resourceId: string }) {
  const requestId = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const refresh = async () => {
    if (busy || queued) return;
    setBusy(true);
    requestId.current ??= crypto.randomUUID();
    try {
      const result = await searchRequest<{ state: "REFRESHING" | "ERROR" }>(
        `/knowledge/resources/${encodeURIComponent(resourceId)}/refresh`,
        {
          method: "POST",
          body: JSON.stringify({ request_id: requestId.current }),
        },
      );
      setQueued(result.state === "REFRESHING");
      setNotice(
        result.state === "REFRESHING"
          ? "Refresh queued. Search again after the source finishes syncing."
          : "Refresh could not be queued. These results are still cached.",
      );
    } catch (error) {
      setNotice(
        error instanceof SearchRequestError && error.status === 409
          ? "This source needs a new import or a refresh from Connections."
          : "Refresh unavailable. Check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <span style={{ display: "grid", gap: "0.5rem", minWidth: 0 }}>
      <button
        type="button"
        onClick={() => void refresh()}
        disabled={busy || queued}
        style={{ minHeight: "2.75rem" }}
      >
        {busy
          ? "Requesting refresh…"
          : queued
            ? "Refresh queued"
            : "Refresh source"}
      </button>
      {notice && <span role="status">{notice}</span>}
    </span>
  );
}
