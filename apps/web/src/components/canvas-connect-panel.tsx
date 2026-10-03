"use client";
import { useEffect, useRef, useState } from "react";
import {
  type CanvasInstitution,
  canvasAuthorizationUrl,
  canvasInstitutions,
  canvasPermissions,
  canvasSelection,
} from "../lib/canvas-setup";
import styles from "./file-import-panel.module.css";

export function CanvasConnectPanel({
  apiBaseUrl,
  onClose,
}: {
  apiBaseUrl: string;
  onClose: () => void;
}) {
  const [institutions, setInstitutions] = useState<CanvasInstitution[]>([]);
  const [institutionId, setInstitutionId] = useState("");
  const institution = institutions.find((item) => item.id === institutionId);
  const [selected, setSelected] = useState<string[]>([
    "academic.courses.read",
    "academic.assignments.read",
  ]);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    void fetch(`${apiBaseUrl}/connectors/canvas-lms/setup`, {
      credentials: "include",
      cache: "no-store",
      signal: abort.signal,
    })
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            "Canvas needs an institution-approved developer key before connecting.",
          );
        const data = await response.json();
        if (!abort.signal.aborted) {
          const available = canvasInstitutions(data.institutions);
          setInstitutions(available);
          setInstitutionId(available.length === 1 ? available[0].id : "");
        }
      })
      .catch((failure) => {
        if (!abort.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "Canvas setup is unavailable.",
          );
      });
    return () => {
      abort.abort();
      controller.current?.abort();
    };
  }, [apiBaseUrl]);
  function toggle(permission: string, checked: boolean) {
    const next = new Set(selected);
    if (checked) next.add(permission);
    else next.delete(permission);
    if (!next.has("academic.assignments.read"))
      next.delete("academic.submissions.read");
    setSelected([...next]);
    setConsent(false);
  }
  async function connect() {
    if (pending || !institution || !consent) return;
    setPending(true);
    setError("");
    const abort = new AbortController();
    controller.current = abort;
    try {
      const response = await fetch(
        `${apiBaseUrl}/connectors/canvas-lms/connect`,
        {
          method: "POST",
          credentials: "include",
          cache: "no-store",
          signal: abort.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            request_id: crypto.randomUUID(),
            capabilities: canvasSelection(selected),
            institution_id: institution.id,
            confirmed: true,
          }),
        },
      );
      if (!response.ok)
        throw new Error(
          "Canvas authorization could not start. Check deployment setup and try again.",
        );
      const data = await response.json();
      if (!abort.signal.aborted)
        window.location.assign(
          canvasAuthorizationUrl(data.authorization_url, institution.origin),
        );
    } catch (failure) {
      if (!abort.signal.aborted)
        setError(
          failure instanceof Error
            ? failure.message
            : "Canvas authorization failed.",
        );
    } finally {
      if (!abort.signal.aborted) setPending(false);
    }
  }
  return (
    <section className={styles.panel} aria-labelledby="canvas-connect-heading">
      <div className={styles.heading}>
        <h3 id="canvas-connect-heading">Connect your Canvas</h3>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <label htmlFor="canvas-institution">Your school</label>
      <select
        id="canvas-institution"
        value={institutionId}
        disabled={pending || institutions.length === 0}
        onChange={(event) => {
          setInstitutionId(event.target.value);
          setConsent(false);
        }}
      >
        <option value="">Choose a school</option>
        {institutions.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </select>
      <p>
        Authorize NavoX on your institution’s Canvas website
        {institution ? ` (${institution.origin})` : ""}. Your Canvas password
        and personal access tokens are never entered here.
      </p>
      {!institutions.length && !error ? (
        <p>No approved schools are available yet.</p>
      ) : null}
      <fieldset disabled={pending}>
        <legend>Choose what NavoX may read</legend>
        {canvasPermissions.map(([key, label]) => (
          <label key={key} style={{ display: "block", marginBlock: "0.6rem" }}>
            <input
              type="checkbox"
              checked={selected.includes(key)}
              disabled={
                key === "academic.courses.read" ||
                (key === "academic.submissions.read" &&
                  !selected.includes("academic.assignments.read"))
              }
              onChange={(e) => toggle(key, e.target.checked)}
            />{" "}
            {label}
          </label>
        ))}
      </fieldset>
      <p>
        Read-only. NavoX cannot submit assignments or change Canvas. Selected
        source text may be processed by the configured AI provider. Calendar
        reads cover the past 30 days and next 180 days.
      </p>
      <label>
        <input
          type="checkbox"
          checked={consent}
          disabled={pending}
          onChange={(e) => setConsent(e.target.checked)}
        />{" "}
        I approve these read permissions and source processing.
      </label>
      {error ? <p role="alert">{error}</p> : null}
      <button
        type="button"
        disabled={pending || !consent || !institution}
        onClick={() => void connect()}
      >
        {pending ? "Opening Canvas…" : "Continue to Canvas"}
      </button>
    </section>
  );
}
