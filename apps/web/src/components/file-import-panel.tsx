"use client";
import { useEffect, useRef, useState } from "react";
import {
  canConfirmImport,
  type ImportFormat,
  type ImportPreview,
  importFormat,
  validPreview,
} from "../lib/file-import";
import styles from "./file-import-panel.module.css";

interface Props {
  apiBaseUrl: string;
  onClose: () => void;
  onImported: () => Promise<void>;
}
export function FileImportPanel({ apiBaseUrl, onClose, onImported }: Props) {
  const [source, setSource] = useState<{
    content: string;
    format: ImportFormat;
  } | null>(null);
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [consent, setConsent] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const requestId = useRef<string | null>(null);
  useEffect(
    () => () => {
      ++version.current;
      controller.current?.abort();
    },
    [],
  );
  async function choose(file: File | undefined) {
    const current = ++version.current;
    controller.current?.abort();
    setSource(null);
    setPreview(null);
    setConsent(false);
    setError("");
    setPending(false);
    requestId.current = null;
    if (!file) return;
    try {
      const format = importFormat(file.name, file.size);
      const content = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
      if (current !== version.current) return;
      setSource({ format, content });
      setName(file.name.slice(0, 120));
    } catch (failure) {
      if (current === version.current)
        setError(
          failure instanceof Error ? failure.message : "Unable to read file.",
        );
    }
  }
  async function submit(confirm: boolean) {
    if (!source || pending || (confirm && (!consent || !preview))) return;
    const current = ++version.current;
    const abort = new AbortController();
    controller.current = abort;
    setPending(true);
    setError("");
    try {
      if (confirm && !requestId.current)
        requestId.current = crypto.randomUUID();
      const response = await fetch(
        `${apiBaseUrl}/connectors/generic-import/${confirm ? "connect" : "preview"}`,
        {
          method: "POST",
          credentials: "include",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          signal: abort.signal,
          body: JSON.stringify({
            ...source,
            ...(confirm
              ? {
                  name,
                  preview_hash: preview?.preview_hash,
                  confirmed: true,
                  request_id: requestId.current,
                }
              : {}),
          }),
        },
      );
      const result: unknown = await response.json();
      if (!response.ok) {
        const detail =
          result && typeof result === "object" && "detail" in result
            ? result.detail
            : null;
        throw new Error(
          typeof detail === "string" && detail.length < 500
            ? detail
            : "The import could not be completed.",
        );
      }
      if (current !== version.current) return;
      if (confirm) {
        setSource(null);
        setPreview(null);
        setConsent(false);
        await onImported();
        if (current === version.current) onClose();
      } else {
        setPreview(validPreview(result));
        setConsent(false);
        requestId.current = null;
      }
    } catch (failure) {
      if (current === version.current && !abort.signal.aborted)
        setError(
          failure instanceof Error
            ? failure.message
            : "The import could not be completed.",
        );
    } finally {
      if (current === version.current) setPending(false);
    }
  }
  return (
    <section className={styles.panel} aria-labelledby="file-import-heading">
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>FILE IMPORT</p>
          <h3 id="file-import-heading">Preview first. Connect when ready.</h3>
        </div>
        <button type="button" onClick={onClose} disabled={pending}>
          Close
        </button>
      </div>
      <p>
        Import an immutable snapshot of individual ICS events, CSV rows, or JSON
        records. No passwords or API keys. Selecting a file does not upload it.
      </p>
      <label className={styles.field}>
        Choose a file (2 MB, up to 1,000 records)
        <input
          type="file"
          accept=".ics,.csv,.json"
          disabled={pending}
          onChange={(e) => void choose(e.target.files?.[0])}
        />
      </label>
      <label className={styles.field}>
        Snapshot name
        <input
          type="text"
          maxLength={120}
          value={name}
          disabled={pending}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <p className={styles.help}>
        CSV/JSON: use title, subject, or name; optional id, description, due_at,
        start_at, end_at, status, and source_url. Other fields are not imported.
        Dates use ISO 8601. ICS recurrence and custom time-zone rules require a
        later release; alarms and attachments are never executed or fetched.
      </p>
      <button
        type="button"
        disabled={!source || pending}
        onClick={() => void submit(false)}
      >
        {pending && !preview ? "Checking file…" : "Preview without AI"}
      </button>
      {preview ? (
        <div className={styles.preview}>
          <strong>
            {preview.count} records · {preview.timezone}
          </strong>
          <p>{preview.notes}</p>
          <ul>
            {preview.examples.map((r) => (
              <li key={`${r.resource_type}:${r.id}`}>
                <span>{r.title}</span>
                <small>{r.resource_type}</small>
              </li>
            ))}
          </ul>
          <label className={styles.consent}>
            <input
              type="checkbox"
              checked={consent}
              disabled={pending}
              onChange={(e) => setConsent(e.target.checked)}
            />
            <span>
              I authorize NavoX to save this encrypted snapshot and send its
              mapped content to the configured AI provider for operational
              extraction. This does not grant write or execution permission.
            </span>
          </label>
          <button
            type="button"
            disabled={
              !canConfirmImport(preview, consent, pending) || !name.trim()
            }
            onClick={() => void submit(true)}
          >
            {pending ? "Saving snapshot…" : "Confirm and process"}
          </button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
    </section>
  );
}
