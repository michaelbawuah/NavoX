"use client";
import { useCallback, useEffect, useState } from "react";
import { NoaArtwork } from "./noa-artwork";
import styles from "./routine-alarm-settings.module.css";
export type NoaChoice = {
  look: "girl" | "boy";
  vibe: "midnight" | "lavender" | "sunset" | "matcha";
  revision: number;
  study_streak?: number;
};
export const noaVibes = {
  midnight: "#1B2230",
  lavender: "#B69CFF",
  sunset: "#FF8A5B",
  matcha: "#5BD38C",
};
const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
async function request(
  accountKey: string,
  body?: NoaChoice,
): Promise<NoaChoice> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`${api}/student/noa`, {
      method: body ? "PUT" : "GET",
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
      headers: {
        "X-NavoX-Account": accountKey,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body
        ? {
            body: JSON.stringify({
              look: body.look,
              vibe: body.vibe,
              revision: body.revision,
            }),
          }
        : {}),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok)
      throw new Error(
        response.status === 409
          ? "Your Noa choice changed. Reload it and try again."
          : "Noa’s preferences couldn’t load. Try again.",
      );
    if (
      !result ||
      !["girl", "boy"].includes(result.look) ||
      !(result.vibe in noaVibes) ||
      !Number.isInteger(result.revision)
    )
      throw new Error("Noa’s preferences couldn’t load. Try again.");
    return result;
  } finally {
    clearTimeout(timeout);
  }
}
export function useNoaChoice(accountKey: string, revision = 0) {
  const [value, setValue] = useState<NoaChoice | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const reload = useCallback(() => setRetry((n) => n + 1), []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: retries and confirmed study revisions deliberately reload saved state.
  useEffect(() => {
    let live = true;
    setValue(null);
    setError("");
    const load = () => {
      void request(accountKey)
        .then((v) => {
          if (live) {
            setValue(v);
            setError("");
          }
        })
        .catch(() => {
          if (live) setError("Noa’s preferences couldn’t load.");
        });
    };
    load();
    window.addEventListener("navox:noa-changed", load);
    return () => {
      live = false;
      window.removeEventListener("navox:noa-changed", load);
    };
    // A confirmed study outcome refreshes the real streak.
  }, [accountKey, retry, revision]);
  return { value, error, reload };
}
export function NoaSettings({ accountKey }: { accountKey: string }) {
  const { value, error, reload } = useNoaChoice(accountKey);
  const [draft, setDraft] = useState<NoaChoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    setDraft(value);
  }, [value]);
  async function save() {
    if (!draft || busy) return;
    setBusy(true);
    setNotice("");
    try {
      await request(accountKey, draft);
      window.dispatchEvent(new Event("navox:noa-changed"));
      setNotice("Noa’s look is saved.");
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Couldn’t save. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.card} aria-labelledby="noa-settings-heading">
      <h2 id="noa-settings-heading">Voice &amp; Noa</h2>
      <p>Choose the Noa you’ll see on Today.</p>
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={reload}>
            Retry
          </button>
        </p>
      )}
      {draft && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <NoaArtwork
            size={150}
            look={draft.look}
            hoodie={noaVibes[draft.vibe]}
          />
          <fieldset disabled={busy}>
            <legend>Noa’s look</legend>
            {(["girl", "boy"] as const).map((look) => (
              <label key={look}>
                <input
                  type="radio"
                  name="noa-look"
                  checked={draft.look === look}
                  onChange={() => setDraft({ ...draft, look })}
                />
                {look === "girl" ? "Girl" : "Boy"}
              </label>
            ))}
          </fieldset>
          <label>
            Vibe{" "}
            <select
              value={draft.vibe}
              disabled={busy}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  vibe: e.target.value as NoaChoice["vibe"],
                })
              }
            >
              {Object.keys(noaVibes).map((v) => (
                <option key={v} value={v}>
                  {v[0].toUpperCase() + v.slice(1)}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save Noa’s look"}
          </button>
        </form>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
