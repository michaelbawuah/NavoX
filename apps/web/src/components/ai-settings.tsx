"use client";

import { useEffect, useRef, useState } from "react";
import { observeAccountSession } from "../lib/account-session";
import styles from "./today-workspace.module.css";

const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
type Settings = {
  configured: boolean;
  revision: number;
  preferred_provider: string | null;
  allow_fallback: boolean;
  available_providers: string[];
};
type Run = {
  id: string;
  provider: string | null;
  model: string | null;
  status: string;
  latency_ms: number;
  estimated_cost: string | null;
  fallback_count: number;
};

type View = { accountKey: string; settings: Settings; runs: Run[] };
function readSettings(value: unknown): Settings {
  if (!value || typeof value !== "object") throw new Error();
  const row = value as Settings;
  if (
    typeof row.configured !== "boolean" ||
    !Number.isSafeInteger(row.revision) ||
    row.revision < 0 ||
    !(
      row.preferred_provider === null ||
      (typeof row.preferred_provider === "string" &&
        row.preferred_provider.length <= 100)
    ) ||
    typeof row.allow_fallback !== "boolean" ||
    !Array.isArray(row.available_providers) ||
    row.available_providers.length > 100 ||
    row.available_providers.some(
      (provider) => typeof provider !== "string" || provider.length > 100,
    )
  )
    throw new Error();
  return row;
}
function readRuns(value: unknown): Run[] {
  if (
    !value ||
    typeof value !== "object" ||
    !("runs" in value) ||
    !Array.isArray(value.runs) ||
    value.runs.length > 100
  )
    throw new Error();
  return value.runs.map((value: unknown) => {
    if (!value || typeof value !== "object") throw new Error();
    const row = value as Run;
    const text = (input: unknown, maximum = 200): input is string =>
      typeof input === "string" && input.length <= maximum;
    if (
      !text(row.id, 128) ||
      !row.id ||
      !(row.provider === null || text(row.provider)) ||
      !(row.model === null || text(row.model)) ||
      !text(row.status, 100) ||
      !row.status ||
      !Number.isFinite(row.latency_ms) ||
      row.latency_ms < 0 ||
      !Number.isSafeInteger(row.fallback_count) ||
      row.fallback_count < 0 ||
      !(
        row.estimated_cost === null ||
        (text(row.estimated_cost, 100) &&
          /^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(row.estimated_cost) &&
          Number.isFinite(Number(row.estimated_cost)))
      )
    )
      throw new Error();
    return row;
  });
}

export function AISettings({ accountKey }: { accountKey: string }) {
  const [view, setView] = useState<View | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const epoch = useRef(0),
    active = useRef(false),
    saving = useRef(false);
  const request = useRef<AbortController | null>(null);
  const observer = useRef<ReturnType<typeof observeAccountSession> | null>(
    null,
  );
  const visible = view?.accountKey === accountKey ? view : null;
  const settings = visible?.settings ?? null;
  const runs = visible?.runs ?? [];
  useEffect(() => {
    let disposed = false;
    function clearPrivate() {
      ++epoch.current;
      active.current = false;
      saving.current = false;
      request.current?.abort();
      request.current = null;
      setView(null);
      setBusy(false);
      setLoading(true);
      setMessage("");
      setFailed(false);
    }
    async function load() {
      const current = epoch.current;
      const controller = new AbortController();
      request.current = controller;
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const [configuration, activity] = await Promise.all([
          fetch(`${api}/ai/settings`, {
            credentials: "include",
            cache: "no-store",
            headers: { "X-NavoX-Account": accountKey },
            signal: controller.signal,
          }),
          fetch(`${api}/ai/activity`, {
            credentials: "include",
            cache: "no-store",
            headers: { "X-NavoX-Account": accountKey },
            signal: controller.signal,
          }),
        ]);
        if (!configuration.ok || !activity.ok)
          throw new Error("AI settings could not be loaded.");
        const [settings, runs] = await Promise.all([
          configuration.json().then(readSettings),
          activity.json().then(readRuns),
        ]);
        if (
          !disposed &&
          active.current &&
          current === epoch.current &&
          !controller.signal.aborted
        )
          setView({ accountKey, settings, runs });
      } catch {
        if (!disposed && active.current && current === epoch.current) {
          setView(null);
          setFailed(true);
          setMessage("AI settings could not be loaded.");
        }
      } finally {
        clearTimeout(timeout);
        if (request.current === controller) request.current = null;
        if (!disposed && active.current && current === epoch.current)
          setLoading(false);
      }
    }
    const session = observeAccountSession({
      checking: clearPrivate,
      restored: (access) => {
        if (
          !access.account ||
          `${access.account.workspace.id}:${access.account.id}` !== accountKey
        ) {
          clearPrivate();
          setLoading(false);
          setFailed(true);
          setMessage("Your account changed. Reopen Settings to continue.");
          return;
        }
        active.current = true;
        void load();
      },
      unavailable: () => {
        clearPrivate();
        setLoading(false);
        setFailed(true);
        setMessage("Check your sign-in to open AI preferences.");
      },
    });
    observer.current = session;
    return () => {
      disposed = true;
      ++epoch.current;
      active.current = false;
      request.current?.abort();
      session.dispose();
      if (observer.current === session) observer.current = null;
    };
  }, [accountKey]);

  function changeSettings(update: Partial<Settings>) {
    if (!active.current || saving.current) return;
    setMessage("");
    setView((current) =>
      current?.accountKey === accountKey
        ? { ...current, settings: { ...current.settings, ...update } }
        : current,
    );
  }

  async function save() {
    if (!settings || !active.current || saving.current || failed) return;
    const current = epoch.current;
    const controller = new AbortController();
    request.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    saving.current = true;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`${api}/ai/settings`, {
        method: "PATCH",
        credentials: "include",
        cache: "no-store",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          "X-NavoX-Account": accountKey,
        },
        body: JSON.stringify({
          expected_revision: settings.revision,
          preferred_provider: settings.preferred_provider,
          allow_fallback: settings.allow_fallback,
        }),
      });
      if (!response.ok) throw new Error();
      const saved = readSettings(await response.json());
      if (
        active.current &&
        current === epoch.current &&
        !controller.signal.aborted
      ) {
        setView((previous) =>
          previous?.accountKey === accountKey
            ? { ...previous, settings: saved }
            : previous,
        );
        setMessage("AI preferences saved.");
      }
    } catch {
      if (active.current && current === epoch.current) {
        setView(null);
        setFailed(true);
        setMessage(
          "The result could not be checked. Reload AI preferences before saving again.",
        );
      }
    } finally {
      clearTimeout(timeout);
      if (request.current === controller) request.current = null;
      if (active.current && current === epoch.current) {
        saving.current = false;
        setBusy(false);
      }
    }
  }

  return (
    <section className={styles.controlCard}>
      <h2>AI preferences</h2>
      <p>
        NavoX chooses an eligible model for each task. Your privacy choices
        always apply.
      </p>
      {loading && (
        <p role="status">Checking your account and AI preferences…</p>
      )}
      {settings && (
        <div className={styles.approvalForm}>
          {!settings.configured && (
            <p>
              AI generation is not configured. You can continue managing your
              records and writing emails manually.
            </p>
          )}
          <label>
            Routing
            <select
              disabled={busy}
              value={settings.preferred_provider ?? ""}
              onChange={(event) =>
                changeSettings({
                  preferred_provider: event.target.value || null,
                })
              }
            >
              <option value="">Automatic</option>
              {settings.available_providers.map((provider) => (
                <option value={provider} key={provider}>
                  Prefer{" "}
                  {(
                    {
                      openai: "OpenAI",
                      gemini: "Gemini",
                      anthropic: "Claude",
                      xai: "Grok",
                    } as Record<string, string>
                  )[provider] ?? provider}
                </option>
              ))}
              {settings.preferred_provider &&
                !settings.available_providers.includes(
                  settings.preferred_provider,
                ) && (
                  <option value={settings.preferred_provider} disabled>
                    Saved preference is unavailable
                  </option>
                )}
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              disabled={busy}
              checked={settings.allow_fallback}
              onChange={(event) =>
                changeSettings({
                  allow_fallback: event.target.checked,
                })
              }
            />{" "}
            Allow an eligible fallback if a provider is unavailable
          </label>
          <button type="button" disabled={busy} onClick={() => void save()}>
            {busy ? "Saving…" : "Save preferences"}
          </button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
      {failed && !loading && (
        <button type="button" onClick={() => observer.current?.refresh()}>
          Reload AI preferences
        </button>
      )}
      {runs.length > 0 && (
        <details>
          <summary>Recent AI activity</summary>
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Result</th>
                <th>Time</th>
                <th>Estimated cost</th>
                <th>Fallbacks</th>
              </tr>
            </thead>
            <tbody>
              {runs.slice(0, 10).map((run) => (
                <tr key={run.id}>
                  <td>
                    {run.provider ?? "No eligible provider"}
                    {run.model ? ` · ${run.model}` : ""}
                  </td>
                  <td>{run.status}</td>
                  <td>{(run.latency_ms / 1000).toFixed(1)}s</td>
                  <td>
                    {run.estimated_cost === null
                      ? "Unknown"
                      : `$${Number(run.estimated_cost).toFixed(4)} USD`}
                  </td>
                  <td>{run.fallback_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            These records do not contain your email text or generated replies.
          </p>
        </details>
      )}
    </section>
  );
}
