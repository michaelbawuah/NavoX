"use client";

import { useEffect, useState } from "react";
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

export function AISettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [configuration, activity] = await Promise.all([
          fetch(`${api}/ai/settings`, {
            credentials: "include",
            signal: controller.signal,
          }),
          fetch(`${api}/ai/activity`, {
            credentials: "include",
            signal: controller.signal,
          }),
        ]);
        if (!configuration.ok || !activity.ok)
          throw new Error("AI settings could not be loaded.");
        setSettings(await configuration.json());
        setRuns((await activity.json()).runs);
      } catch {
        if (!controller.signal.aborted)
          setMessage("AI settings could not be loaded.");
      }
    }
    void load();
    return () => controller.abort();
  }, []);

  async function save() {
    if (!settings) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`${api}/ai/settings`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: settings.revision,
          preferred_provider: settings.preferred_provider,
          allow_fallback: settings.allow_fallback,
        }),
      });
      if (!response.ok) throw new Error();
      setSettings(await response.json());
      setMessage("AI preferences saved.");
    } catch {
      setMessage("Preferences could not be saved. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.controlCard}>
      <h2>AI preferences</h2>
      <p>
        NavoX chooses an eligible model for each task. Your workspace privacy
        rules always apply.
      </p>
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
              value={settings.preferred_provider ?? ""}
              onChange={(event) =>
                setSettings({
                  ...settings,
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
              checked={settings.allow_fallback}
              onChange={(event) =>
                setSettings({
                  ...settings,
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
