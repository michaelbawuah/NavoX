"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ConnectorEntry,
  ManagedConnection,
} from "../lib/connection-management";
import {
  googleSetupAvailable,
  type SetupConnection,
  type SetupSyncs,
  setupProgress,
  startGoogleSetup,
} from "../lib/setup-progress";
import type { SyncProgress, SyncSource } from "../lib/sync-progress";
import styles from "./guided-setup.module.css";
import {
  IntelligenceControls,
  type IntelligenceStatus,
} from "./intelligence-controls";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

const steps = [
  ["connect", "Connect an account"],
  ["access", "Choose access & sync"],
  ["review", "Review your day"],
] as const;

export interface GuidedSetupProps {
  connections: SetupConnection[];
  paused: boolean;
  itemCount: number;
  loading?: boolean;
  connectionsUnavailable?: boolean;
  onConnectionsChanged: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onAddTask?: () => void;
}

/** First use is explicit: mounting this panel never starts OAuth or source sync. */
export function GuidedSetup({
  connections,
  paused,
  itemCount,
  loading = false,
  connectionsUnavailable = false,
  onConnectionsChanged,
  onRefresh,
  onAddTask,
}: GuidedSetupProps) {
  const [catalog, setCatalog] = useState<ConnectorEntry[] | null>(null);
  const [managedConnections, setManagedConnections] = useState<
    ManagedConnection[]
  >([]);
  const [intelligenceStatus, setIntelligenceStatus] =
    useState<IntelligenceStatus | null>(null);
  const [readinessUnavailable, setReadinessUnavailable] = useState(false);
  const [syncs, setSyncs] = useState<SetupSyncs>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [overviewUnavailable, setOverviewUnavailable] = useState(false);
  const request = useRef(0);
  const mounted = useRef(false);
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);

  const refreshOverview = useCallback(async () => {
    const version = ++request.current;
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    const timeout = setTimeout(() => next.abort(), 10_000);
    try {
      const results = await Promise.all(
        ["/connectors", "/connections"].map(async (path) => {
          const response = await fetch(`${apiBaseUrl}${path}`, {
            credentials: "include",
            cache: "no-store",
            signal: next.signal,
          });
          if (!response.ok) throw new Error("Connection status unavailable");
          const payload: unknown = await response.json();
          if (!Array.isArray(payload))
            throw new Error("Invalid connection status");
          return payload;
        }),
      );
      if (!mounted.current || version !== request.current) return;
      setCatalog(results[0] as ConnectorEntry[]);
      setManagedConnections(results[1] as ManagedConnection[]);
      setOverviewUnavailable(false);
    } catch {
      if (mounted.current && version === request.current)
        setOverviewUnavailable(true);
    } finally {
      clearTimeout(timeout);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refreshOverview();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshOverview();
    }, 15_000);
    return () => {
      mounted.current = false;
      ++request.current;
      controller.current?.abort();
      window.clearInterval(timer);
    };
  }, [refreshOverview]);

  const onSyncProgress = useCallback(
    (connectionId: string, source: SyncSource, progress: SyncProgress) => {
      setSyncs((current) => ({
        ...current,
        [`${connectionId}:${source}`]: { connectionId, source, progress },
      }));
    },
    [],
  );
  const onStatusChange = useCallback((status: IntelligenceStatus | null) => {
    setIntelligenceStatus(status);
    if (status) setReadinessUnavailable(false);
  }, []);

  async function connectGoogle() {
    if (operation.current || !catalog || !googleSetupAvailable(catalog)) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      const url = await startGoogleSetup(apiBaseUrl);
      if (mounted.current) window.location.assign(url);
    } catch (failure) {
      if (mounted.current)
        setError(
          failure instanceof Error
            ? failure.message
            : "Google could not be connected.",
        );
    } finally {
      operation.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function retryConnections() {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      await onConnectionsChanged();
      await refreshOverview();
    } catch {
      if (mounted.current)
        setError("Your connected apps could not refresh. Please try again.");
    } finally {
      operation.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const progress = setupProgress({
    connections,
    managedConnections,
    syncs,
    paused,
    processingReady: readinessUnavailable
      ? false
      : (intelligenceStatus?.processing_ready ?? null),
    readinessMessage: readinessUnavailable
      ? "Reading availability could not be checked. Use Refresh Today below to try again, or add a task yourself."
      : intelligenceStatus?.readiness_message,
    itemCount: loading ? 0 : itemCount,
    connectionsUnavailable,
  });
  const stepIndex = steps.findIndex(([stage]) => stage === progress.stage);
  const showControls =
    connections.length > 0 &&
    !connectionsUnavailable &&
    progress.stage !== "review";

  return (
    <section className={styles.panel} aria-labelledby="guided-setup-heading">
      <div className={styles.topline}>
        <span className={styles.eyebrow}>A useful first step</span>
        <Link href="/connections">Manage apps</Link>
      </div>
      <ol className={styles.steps} aria-label="Get started">
        {steps.map(([stage, label], index) => (
          <li
            key={stage}
            data-complete={index < stepIndex}
            aria-current={stage === progress.stage ? "step" : undefined}
          >
            <span aria-hidden="true">
              {index < stepIndex ? "✓" : index + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>
      <div className={styles.introduction}>
        <h2 id="guided-setup-heading">{progress.title}</h2>
        <p
          role={
            ["syncing", "checking"].includes(progress.state)
              ? "status"
              : undefined
          }
        >
          {progress.message}
        </p>
      </div>
      {progress.state === "connect" && (
        <div className={styles.connect}>
          <button
            type="button"
            className={styles.primary}
            disabled={
              busy ||
              !catalog ||
              overviewUnavailable ||
              !googleSetupAvailable(catalog)
            }
            onClick={() => void connectGoogle()}
          >
            {busy ? "Opening Google…" : "Connect Google"}
          </button>
          <p>
            Connecting links your account only. You choose read access next.
            Sending email needs separate permission and your approval.
          </p>
          {catalog &&
            !overviewUnavailable &&
            !googleSetupAvailable(catalog) && (
              <p>
                Google connection is temporarily unavailable. You can start by
                adding a task.
              </p>
            )}
        </div>
      )}
      {(connectionsUnavailable || overviewUnavailable) && (
        <div className={styles.recovery}>
          <p role="alert">
            Connection status could not be checked. Retry to confirm what is
            available.
          </p>
          <button
            type="button"
            className={styles.secondary}
            disabled={busy}
            onClick={() => void retryConnections()}
          >
            {busy ? "Checking…" : "Retry connection status"}
          </button>
        </div>
      )}
      {progress.state === "reconnect" && (
        <Link className={styles.secondary} href="/connections">
          Reconnect or resume Google
        </Link>
      )}
      {showControls && (
        <IntelligenceControls
          guided
          connections={connections}
          paused={paused}
          onStatusChange={onStatusChange}
          onStatusUnavailable={() => setReadinessUnavailable(true)}
          onSyncProgress={onSyncProgress}
          onRefresh={async () => {
            await Promise.all([onRefresh(), refreshOverview()]);
          }}
        />
      )}
      <div className={styles.footer}>
        {onAddTask ? (
          <button
            type="button"
            className={styles.secondary}
            onClick={onAddTask}
          >
            Add my own task
          </button>
        ) : (
          <Link className={styles.secondary} href="/planner#capture-heading">
            Add my own task
          </Link>
        )}
        {progress.stage === "review" && (
          <a className={styles.primary} href="#daily-briefing">
            Review my day
          </a>
        )}
        <span className={styles.footnote}>
          You decide what gets connected and what gets done.
        </span>
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
