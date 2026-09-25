"use client";

import { useEffect, useRef, useState } from "react";
import {
  type EvidenceExcerpt,
  readSourceEvidence,
  SourceEvidenceError,
} from "../lib/source-evidence";
import styles from "./today-workspace.module.css";

type EvidenceState =
  | { status: "idle" | "loading" }
  | { status: "ready"; excerpts: EvidenceExcerpt[] }
  | { status: "error"; message: string };

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export function EvidencePassages({
  excerpts,
}: {
  excerpts: EvidenceExcerpt[];
}) {
  return (
    <div className={styles.evidencePassages}>
      {excerpts.map((excerpt) => (
        <div key={`${excerpt.source}:${excerpt.text}`}>
          <small>
            {excerpt.source === "subject" ? "Email subject" : "Email body"}
          </small>
          <blockquote>{excerpt.text}</blockquote>
        </div>
      ))}
    </div>
  );
}

export function SourceEvidence({
  evidenceId,
  paused,
}: {
  evidenceId: string;
  paused: boolean;
}) {
  const [state, setState] = useState<EvidenceState>({ status: "idle" });
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    if (paused) setState({ status: "idle" });
    return () => {
      request.current?.abort();
      request.current = null;
    };
  }, [paused]);

  async function load() {
    if (paused || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setState({ status: "loading" });
    const timeout = setTimeout(() => controller.abort(), 35_000);
    try {
      const excerpts = await readSourceEvidence(
        apiBaseUrl,
        evidenceId,
        controller.signal,
      );
      if (request.current === controller) {
        setState({ status: "ready", excerpts });
      }
    } catch (error) {
      if (request.current === controller) {
        setState({
          status: "error",
          message: controller.signal.aborted
            ? "The source request timed out. Try again."
            : error instanceof SourceEvidenceError
              ? error.message
              : "The source could not be loaded. Try again later.",
        });
      }
    } finally {
      clearTimeout(timeout);
      if (request.current === controller) request.current = null;
    }
  }

  return (
    <div className={styles.sourceReader}>
      {!paused && state.status === "ready" ? (
        <>
          <EvidencePassages excerpts={state.excerpts} />
          <button onClick={() => setState({ status: "idle" })} type="button">
            Hide source text
          </button>
        </>
      ) : (
        <button
          disabled={paused || state.status === "loading"}
          onClick={() => void load()}
          type="button"
        >
          {state.status === "loading" ? "Loading source…" : "View source text"}
        </button>
      )}
      {!paused && state.status === "error" && (
        <p role="status">{state.message}</p>
      )}
      {paused && <p>Resume NavoX to view source text.</p>}
    </div>
  );
}
