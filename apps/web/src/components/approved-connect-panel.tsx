"use client";

import { useEffect, useRef, useState } from "react";
import {
  type ApprovedServiceChoice,
  approvedConnectBody,
  approvedServiceChoices,
  approvedServicePaths,
  canConnectApprovedService,
} from "../lib/approved-connector-setup";
import styles from "./file-import-panel.module.css";

export function ApprovedConnectPanel({
  apiBaseUrl,
  kind,
  onClose,
  onConnected,
}: {
  apiBaseUrl: string;
  kind: "rest" | "mcp";
  onClose: () => void;
  onConnected: () => Promise<void>;
}) {
  const paths = approvedServicePaths(kind);
  const connectorLabel = kind === "rest" ? "REST" : "MCP";
  const [choices, setChoices] = useState<ApprovedServiceChoice[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [permissions, setPermissions] = useState<string[]>([]);
  const [consent, setConsent] = useState(false);
  const [hasToken, setHasToken] = useState(false);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const tokenInput = useRef<HTMLInputElement | null>(null);
  const requestId = useRef<string | null>(null);
  const choice = choices.find((entry) => entry.id === selectedId) ?? null;

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    void fetch(`${apiBaseUrl}${paths.options}`, {
      credentials: "include",
      cache: "no-store",
      signal: abort.signal,
    })
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            response.status === 401
              ? "Your session expired. Sign in again."
              : `No ${connectorLabel} service is configured for connection right now.`,
          );
        return approvedServiceChoices(await response.json());
      })
      .then((entries) => {
        if (!abort.signal.aborted) setChoices(entries);
      })
      .catch((failure) => {
        if (!abort.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : `${connectorLabel} setup unavailable.`,
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [apiBaseUrl, paths.options, connectorLabel]);

  function selectChoice(id: string) {
    setSelectedId(id);
    setPermissions([]);
    setConsent(false);
    setHasToken(false);
    if (tokenInput.current) tokenInput.current.value = "";
    requestId.current = null;
  }

  async function connect() {
    if (
      !choice ||
      pending ||
      !canConnectApprovedService(choice, permissions, consent, hasToken)
    )
      return;
    const stableRequestId = requestId.current ?? crypto.randomUUID();
    requestId.current = stableRequestId;
    const token =
      choice?.authentication === "api_token"
        ? tokenInput.current?.value
        : undefined;
    if (tokenInput.current) tokenInput.current.value = "";
    setHasToken(false);
    setPending(true);
    setError("");
    const abort = new AbortController();
    controller.current = abort;
    try {
      const response = await fetch(`${apiBaseUrl}${paths.connect}`, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        signal: abort.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          approvedConnectBody(
            kind,
            choice,
            permissions,
            token,
            stableRequestId,
          ),
        ),
      });
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? "Your session expired. Sign in again."
            : response.status === 409
              ? "This reviewed service changed or is unavailable. Close setup, refresh, and review permissions before retrying."
              : `${connectorLabel} connection could not be created. Check the reviewed service and try again.`,
        );
      const result: unknown = await response.json();
      if (
        !result ||
        typeof result !== "object" ||
        !("connection_id" in result) ||
        typeof result.connection_id !== "string"
      )
        throw new Error(
          `${connectorLabel} setup returned an invalid response. Refresh connection status.`,
        );
      if (abort.signal.aborted) return;
      await onConnected();
      if (!abort.signal.aborted) onClose();
    } catch (failure) {
      if (!abort.signal.aborted)
        setError(
          failure instanceof Error
            ? failure.message
            : `${connectorLabel} connection could not be created.`,
        );
    } finally {
      if (!abort.signal.aborted) setPending(false);
    }
  }

  return (
    <section
      className={styles.panel}
      aria-labelledby="approved-connect-heading"
    >
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>REVIEWED {connectorLabel} SERVICES</p>
          <h3 id="approved-connect-heading">Choose what NavoX may read</h3>
        </div>
        <button type="button" disabled={pending} onClick={onClose}>
          Close
        </button>
      </div>
      <p className={styles.help}>
        Only services configured by the workspace operator appear here. Select
        read permissions explicitly. NavoX may process the selected source
        content with its configured AI provider. No server address or tool
        instruction can be entered here.
      </p>
      {loading ? <p role="status">Loading approved services…</p> : null}
      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
      {!loading && choices.length === 0 ? (
        <p>No reviewed {connectorLabel} services are currently available.</p>
      ) : null}
      {choices.length > 0 ? (
        <label className={styles.field}>
          Approved service
          <select
            value={selectedId}
            disabled={pending}
            onChange={(event) => selectChoice(event.target.value)}
          >
            <option value="">Choose a service</option>
            {choices.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {choice ? (
        <>
          <fieldset disabled={pending}>
            <legend>Read permissions</legend>
            {choice.read_capabilities.map((capability) => (
              <label key={capability} className={styles.consent}>
                <input
                  type="checkbox"
                  checked={permissions.includes(capability)}
                  onChange={(event) => {
                    setPermissions((current) =>
                      event.target.checked
                        ? [...current, capability]
                        : current.filter((item) => item !== capability),
                    );
                    setConsent(false);
                    requestId.current = null;
                  }}
                />
                {capability}
              </label>
            ))}
          </fieldset>
          {choice.authentication === "api_token" ? (
            <label className={styles.field}>
              API token for {choice.name}
              <input
                ref={tokenInput}
                type="password"
                autoComplete="off"
                disabled={pending}
                onChange={(event) =>
                  setHasToken(event.target.value.trim().length > 0)
                }
              />
            </label>
          ) : (
            <p className={styles.help}>This service requires no API token.</p>
          )}
          <label className={styles.consent}>
            <input
              type="checkbox"
              checked={consent}
              disabled={pending || permissions.length === 0}
              onChange={(event) => setConsent(event.target.checked)}
            />
            I approve the selected read access and source processing.
          </label>
          <button
            type="button"
            disabled={
              pending ||
              !canConnectApprovedService(choice, permissions, consent, hasToken)
            }
            onClick={() => void connect()}
          >
            {pending ? "Connecting…" : "Connect approved service"}
          </button>
        </>
      ) : null}
    </section>
  );
}
