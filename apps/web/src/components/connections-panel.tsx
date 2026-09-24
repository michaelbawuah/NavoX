"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type ConnectorEntry,
  connectionPage,
  formatConnectionTime,
  googleAuthorizationUrl,
  healthLabel,
  type ManagedConnection,
  matchesConnection,
  sourceStatus,
} from "../lib/connection-management";
import styles from "./connections-panel.module.css";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401)
      throw new Error("Your session expired. Sign in again.");
    if (response.status === 403)
      throw new Error("This operation is not authorized.");
    if (response.status === 429)
      throw new Error("A cooldown is active. Try again after it ends.");
    if (response.status === 409)
      throw new Error(
        "The connection changed or is not ready. Refresh its status before retrying.",
      );
    if (response.status === 503)
      throw new Error(
        "This service is not configured or is temporarily unavailable.",
      );
    throw new Error(
      "The request could not be completed. Refresh and try again.",
    );
  }
  return (await response.json()) as T;
}

interface ConnectionsPanelProps {
  agentPaused: boolean;
  onConnectionsChanged: () => Promise<void>;
}

export function ConnectionsPanel({
  agentPaused,
  onConnectionsChanged,
}: ConnectionsPanelProps) {
  const [catalog, setCatalog] = useState<ConnectorEntry[]>([]);
  const [connections, setConnections] = useState<ManagedConnection[]>([]);
  const [tab, setTab] = useState<"connected" | "browse">("connected");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const mounted = useRef(false);
  const version = useRef(0);
  const busy = useRef(false);
  const controller = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const requestVersion = ++version.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    try {
      const [entries, rows] = await Promise.all([
        requestJson<ConnectorEntry[]>("/connectors", {
          signal: nextController.signal,
        }),
        requestJson<ManagedConnection[]>("/connections", {
          signal: nextController.signal,
        }),
      ]);
      if (!Array.isArray(entries) || !Array.isArray(rows)) {
        throw new Error(
          "The connection overview returned an invalid response.",
        );
      }
      if (!mounted.current || requestVersion !== version.current) return;
      setCatalog(entries);
      setConnections(rows);
      setRefreshedAt(new Date().toISOString());
      setError("");
    } catch (failure) {
      if (
        !mounted.current ||
        requestVersion !== version.current ||
        nextController.signal.aborted
      )
        return;
      setError(
        failure instanceof Error
          ? failure.message
          : "Connection status is unavailable.",
      );
    } finally {
      if (mounted.current && requestVersion === version.current)
        setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => {
      if (!busy.current && document.visibilityState === "visible")
        void refresh();
    }, 15000);
    return () => {
      mounted.current = false;
      ++version.current;
      controller.current?.abort();
      window.clearInterval(timer);
    };
  }, [refresh]);

  async function perform(
    key: string,
    path: string,
    successMessage: string,
    redirect = false,
    source?: string,
  ) {
    if (busy.current) return;
    busy.current = true;
    ++version.current;
    controller.current?.abort();
    setPending(key);
    setError("");
    setNotice("");
    try {
      const body = await requestJson<{ authorization_url?: string }>(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          ...(source ? { source } : {}),
        }),
      });
      if (!mounted.current) return;
      if (redirect) {
        window.location.assign(googleAuthorizationUrl(body.authorization_url));
        return;
      }
      setNotice(successMessage);
      await refresh();
      try {
        await onConnectionsChanged();
      } catch {
        if (mounted.current)
          setError(
            "The operation succeeded, but other workspace controls could not refresh. Reload before using them.",
          );
      }
    } catch (failure) {
      if (mounted.current)
        setError(
          failure instanceof Error
            ? failure.message
            : "The operation did not complete.",
        );
    } finally {
      busy.current = false;
      if (mounted.current) setPending(null);
    }
  }

  const visibleConnections = connectionPage(
    connections.filter((connection) => matchesConnection(connection, query)),
    page,
  );
  const search = query.trim().toLocaleLowerCase();
  const visibleCatalog = connectionPage(
    catalog.filter((entry) =>
      `${entry.name} ${entry.category} ${entry.description}`
        .toLocaleLowerCase()
        .includes(search),
    ),
    page,
  );
  const pagination = tab === "connected" ? visibleConnections : visibleCatalog;

  return (
    <section aria-labelledby="connections-heading" className={styles.panel}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>CONNECT YOUR WORLD</p>
          <h2 id="connections-heading">Your connections. Your control.</h2>
          <p className={styles.copy}>
            See what is connected, what NavoX may read, and how recently each
            source synced.
          </p>
        </div>
        <button
          type="button"
          disabled={pending !== null || loading}
          onClick={() => void refresh()}
        >
          Refresh status
        </button>
      </div>
      {agentPaused ? (
        <p className={styles.warning}>
          The agent is paused. Resuming a connection does not resume the agent
          or start a sync.
        </p>
      ) : null}
      <div className={styles.toolbar}>
        <nav className={styles.switcher} aria-label="Connection views">
          <button
            type="button"
            aria-pressed={tab === "connected"}
            onClick={() => {
              setTab("connected");
              setPage(0);
            }}
          >
            Connected{refreshedAt ? ` (${connections.length})` : ""}
          </button>
          <button
            type="button"
            aria-pressed={tab === "browse"}
            onClick={() => {
              setTab("browse");
              setPage(0);
            }}
          >
            Browse connectors
          </button>
        </nav>
        <label className={styles.search}>
          <span>Find a connection</span>
          <input
            type="search"
            value={query}
            placeholder="Search apps, accounts or permissions"
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
          />
        </label>
      </div>
      {error ? (
        <p role="alert" className={styles.warning}>
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      ) : null}
      {loading ? (
        <p role="status" className={styles.empty}>
          Loading saved connection status…
        </p>
      ) : null}
      {!loading && refreshedAt !== null && tab === "connected" ? (
        <div className={styles.grid}>
          {visibleConnections.items.map((connection) => (
            <article key={connection.id} className={styles.card}>
              <div className={styles.cardHeading}>
                <div>
                  <h3>{connection.name}</h3>
                  <p className={styles.account}>
                    {connection.account_label ?? "Workspace connection"}
                  </p>
                </div>
                <span className={styles.badge} data-health={connection.health}>
                  {healthLabel(connection.health)}
                </span>
              </div>
              <div className={styles.sources}>
                {connection.sources.map((source) => (
                  <section
                    key={source.id}
                    className={styles.source}
                    aria-label={source.name}
                  >
                    <div>
                      <h4>{source.name}</h4>
                      <p>{sourceStatus(source)}</p>
                      <small>
                        Last completed sync:{" "}
                        {formatConnectionTime(source.last_synced_at)}
                      </small>
                      {source.retry_at ? (
                        <small>
                          Retry after {formatConnectionTime(source.retry_at)}
                        </small>
                      ) : null}
                    </div>
                    {source.authorized ? (
                      <button
                        type="button"
                        disabled={
                          pending !== null || agentPaused || !source.can_sync
                        }
                        onClick={() =>
                          void perform(
                            `${connection.id}:${source.id}`,
                            `/connections/${connection.id}/sync`,
                            "Sync queued. Completion will appear in the source status; queuing is not completion.",
                            false,
                            source.id,
                          )
                        }
                      >
                        {pending === `${connection.id}:${source.id}`
                          ? "Queuing…"
                          : "Sync now"}
                      </button>
                    ) : null}
                  </section>
                ))}
              </div>
              <details className={styles.permissions}>
                <summary>
                  Granted permissions ({connection.permissions.length})
                </summary>
                {connection.permissions.length === 0 ? (
                  <p>
                    Account identity only. No source read permission is granted.
                  </p>
                ) : (
                  <ul>
                    {connection.permissions.map((permission) => (
                      <li key={permission.name}>
                        <span>{permission.label}</span>
                        <small>{permission.mode}</small>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
              <div className={styles.actions}>
                {connection.can_pause ? (
                  <button
                    type="button"
                    disabled={pending !== null}
                    onClick={() =>
                      void perform(
                        `${connection.id}:pause`,
                        `/connections/${connection.id}/pause`,
                        "Connection paused. Saved knowledge, credentials and sync progress were retained.",
                      )
                    }
                  >
                    {pending === `${connection.id}:pause`
                      ? "Pausing…"
                      : "Pause"}
                  </button>
                ) : null}
                {connection.can_resume ? (
                  <button
                    type="button"
                    disabled={pending !== null}
                    onClick={() =>
                      void perform(
                        `${connection.id}:resume`,
                        `/connections/${connection.id}/resume`,
                        "Connection resumed. Sync remains subject to permissions, agent state and provider cooldowns.",
                      )
                    }
                  >
                    {pending === `${connection.id}:resume`
                      ? "Resuming…"
                      : "Resume"}
                  </button>
                ) : null}
                {connection.can_reauthorize ? (
                  <button
                    type="button"
                    disabled={pending !== null}
                    onClick={() =>
                      void perform(
                        `${connection.id}:reauthorize`,
                        `/connections/${connection.id}/reauthorize`,
                        "",
                        true,
                      )
                    }
                  >
                    {pending === `${connection.id}:reauthorize`
                      ? "Opening Google…"
                      : "Reconnect"}
                  </button>
                ) : null}
              </div>
              <p className={styles.footnote}>
                {connection.connector_id === "google-workspace"
                  ? "Reconnect requests existing permissions only. Add read access below in Connected understanding. "
                  : ""}
                Pause is not disconnect or data deletion.
              </p>
            </article>
          ))}
          {visibleConnections.items.length === 0 ? (
            <div className={styles.empty}>
              <h3>
                {connections.length === 0
                  ? "No connected accounts yet"
                  : "No matching connections"}
              </h3>
              <p>Browse available connectors or try another search.</p>
              <button
                type="button"
                onClick={() => {
                  setTab("browse");
                  setQuery("");
                  setPage(0);
                }}
              >
                Browse connectors
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      {!loading && refreshedAt !== null && tab === "browse" ? (
        <div className={styles.grid}>
          {visibleCatalog.items.map((entry) => (
            <article key={entry.id} className={styles.card}>
              <div className={styles.cardHeading}>
                <div>
                  <p className={styles.eyebrow}>{entry.category}</p>
                  <h3>{entry.name}</h3>
                </div>
                <span className={styles.badge}>
                  {entry.availability === "available"
                    ? "Available"
                    : "Not enabled"}
                </span>
              </div>
              <p className={styles.copy}>{entry.description}</p>
              <p className={styles.footnote}>
                Authentication: {entry.authentication}
              </p>
              <button
                type="button"
                disabled={
                  pending !== null || entry.availability !== "available"
                }
                onClick={() =>
                  void perform(
                    `connect:${entry.id}`,
                    `/connectors/${entry.id}/connect`,
                    "",
                    true,
                  )
                }
              >
                {pending === `connect:${entry.id}`
                  ? "Opening authorization…"
                  : entry.setup_label}
              </button>
              {entry.id === "google-workspace" ? (
                <p className={styles.footnote}>
                  Linking requests account identity only. Reading Gmail or
                  Calendar requires separate consent. Sending email always
                  requires separate permission and exact-action approval.
                </p>
              ) : null}
            </article>
          ))}
          {visibleCatalog.items.length === 0 ? (
            <p className={styles.empty}>
              No matching connectors. Try another search.
            </p>
          ) : null}
        </div>
      ) : null}
      <div className={styles.footer}>
        <small>
          Overview checked: {formatConnectionTime(refreshedAt)}. Checking status
          does not read source content.
        </small>
        {pagination.pages > 1 ? (
          <div className={styles.pagination}>
            <button
              type="button"
              disabled={pagination.page === 0}
              onClick={() => setPage(pagination.page - 1)}
            >
              Previous
            </button>
            <span>
              Page {pagination.page + 1} of {pagination.pages}
            </span>
            <button
              type="button"
              disabled={pagination.page + 1 === pagination.pages}
              onClick={() => setPage(pagination.page + 1)}
            >
              Next
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
