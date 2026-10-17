"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  appendMemorySelection,
  type MemoryKind,
  memoryKindLabels,
  memoryKinds,
  type PersonalMemory,
  readPersonalMemories,
} from "../lib/personal-memory";
import styles from "./personal-memory.module.css";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

class MemoryRequestError extends Error {
  constructor(
    message: string,
    readonly uncertain = false,
  ) {
    super(message);
  }
}

async function memoryRequest(
  path = "",
  method = "GET",
  body?: unknown,
): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetch(`${apiBaseUrl}/memory${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
    const payload: unknown =
      response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? payload.detail
          : null;
      throw new MemoryRequestError(
        typeof detail === "string"
          ? detail
          : "Your saved preferences could not be updated.",
        method !== "GET" && response.status >= 500,
      );
    }
    return payload;
  } catch (cause) {
    if (!(cause instanceof MemoryRequestError))
      throw new MemoryRequestError(
        cause instanceof Error && cause.name === "AbortError"
          ? "Saved preferences took too long to respond."
          : "Saved preferences could not be reached.",
        method !== "GET",
      );
    throw cause;
  } finally {
    clearTimeout(timer);
  }
}

export function SavedMemoryPicker({
  value,
  onChange,
  maxLength,
  disabled = false,
  kinds,
}: {
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  disabled?: boolean;
  kinds?: readonly string[];
}) {
  const [open, setOpen] = useState(false);
  const [memories, setMemories] = useState<PersonalMemory[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  useEffect(
    () => () => {
      ++sequence.current;
    },
    [],
  );
  async function load() {
    const request = ++sequence.current;
    setLoading(true);
    setError("");
    setLoadFailed(false);
    try {
      const rows = readPersonalMemories(await memoryRequest());
      if (request === sequence.current) {
        setMemories(rows);
        setSelected([]);
      }
    } catch (cause) {
      if (request === sequence.current) {
        setLoadFailed(true);
        setError(
          cause instanceof Error
            ? cause.message
            : "Saved preferences could not be loaded.",
        );
      }
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }
  const available = (memories ?? []).filter(
    (row) => row.enabled && (!kinds || kinds.includes(row.kind)),
  );
  return (
    <div className={styles.picker}>
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        onClick={() => {
          setOpen((shown) => !shown);
          if (!open) void load();
        }}
      >
        Use saved preferences
      </button>
      {open && (
        <div className={styles.pickerBody} aria-busy={loading}>
          <p>
            Choose what to add to this message. Review the visible text before
            you send.
          </p>
          {error && (
            <p role="alert" className={styles.error}>
              {error}{" "}
              <button
                type="button"
                disabled={loading}
                onClick={() => void load()}
              >
                Retry
              </button>
            </p>
          )}
          {loading ? (
            <p role="status">Loading your saved preferences…</p>
          ) : memories && available.length === 0 && !error ? (
            <p>
              No enabled preferences for this message.{" "}
              <Link href="/settings#saved-memory">Save a preference</Link>
            </p>
          ) : null}
          {!loading &&
            available.map((row) => (
              <label className={styles.choice} key={row.id}>
                <input
                  type="checkbox"
                  disabled={disabled || loadFailed}
                  checked={selected.includes(row.id)}
                  onChange={(event) => {
                    setError("");
                    setSelected((ids) =>
                      event.target.checked
                        ? [...ids, row.id]
                        : ids.filter((id) => id !== row.id),
                    );
                  }}
                />
                <span>
                  <strong>{row.label}</strong>
                  <span>{row.content}</span>
                </span>
              </label>
            ))}
          <div className={styles.actions}>
            <button
              type="button"
              disabled={
                disabled || loading || loadFailed || selected.length === 0
              }
              onClick={() => {
                const result = appendMemorySelection(
                  value,
                  available,
                  selected,
                  maxLength,
                );
                if (result.error) {
                  setError(result.error);
                  return;
                }
                onChange(result.value);
                setOpen(false);
                setSelected([]);
                setError("");
              }}
            >
              Add to this message
            </button>
            <Link href="/settings#saved-memory">Manage saved preferences</Link>
          </div>
        </div>
      )}
    </div>
  );
}

export function PersonalMemoryPanel() {
  const contentId = useId();
  const [memories, setMemories] = useState<PersonalMemory[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [kind, setKind] = useState<MemoryKind>("reply_length");
  const [label, setLabel] = useState("");
  const [content, setContent] = useState("short");
  const [forgetId, setForgetId] = useState<string | null>(null);
  const uncertainWrite = useRef(false);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    const request = ++sequence.current;
    setLoading(true);
    setError("");
    setLoadFailed(false);
    try {
      const rows = readPersonalMemories(await memoryRequest());
      if (request === sequence.current) {
        setMemories(rows);
        if (uncertainWrite.current) {
          uncertainWrite.current = false;
          setEditingId(null);
          setKind("project_fact");
          setLabel("");
          setContent("");
          setForgetId(null);
          setNotice(
            "Preferences refreshed. Review the saved list before making another change.",
          );
        }
      }
    } catch (cause) {
      if (request === sequence.current) {
        setLoadFailed(true);
        setError(
          cause instanceof Error
            ? cause.message
            : "Saved preferences could not be loaded.",
        );
      }
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
    return () => {
      ++sequence.current;
    };
  }, [load]);
  function reset() {
    setEditingId(null);
    setLabel("");
    setContent(
      kind === "reply_length" ? "short" : kind === "tone" ? "friendly" : "",
    );
  }
  async function change(
    path: string,
    method: string,
    body?: unknown,
    message = "Saved preferences updated.",
  ) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await memoryRequest(path, method, body);
      setNotice(message);
      setForgetId(null);
      if (
        method === "POST" ||
        (editingId &&
          path === `/${editingId}` &&
          (method === "DELETE" ||
            (method === "PATCH" &&
              typeof body === "object" &&
              body !== null &&
              "content" in body)))
      )
        reset();
      await load();
    } catch (cause) {
      if (cause instanceof MemoryRequestError && cause.uncertain) {
        uncertainWrite.current = true;
        setLoadFailed(true);
      }
      setError(
        cause instanceof Error
          ? `${cause.message}${uncertainWrite.current ? " The change may have been saved. Reload preferences to check before making another change." : ""}`
          : "Saved preferences could not be updated.",
      );
    } finally {
      setBusy(false);
    }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    const fields = {
      label: label.trim() || memoryKindLabels[kind],
      content: content.trim(),
    };
    void change(
      editingId ? `/${editingId}` : "",
      editingId ? "PATCH" : "POST",
      editingId ? fields : { ...fields, kind },
      "Saved. You can choose this preference when writing a message.",
    );
  }
  const choices =
    kind === "reply_length"
      ? ["short", "balanced", "detailed"]
      : kind === "tone"
        ? ["friendly", "professional", "direct"]
        : null;
  return (
    <section
      id="saved-memory"
      className={styles.panel}
      aria-labelledby="saved-memory-heading"
      aria-busy={loading}
    >
      <div className={styles.heading}>
        <div>
          <p className={styles.kicker}>Make it personal</p>
          <h2 id="saved-memory-heading">What NavoX remembers</h2>
        </div>
        <span>
          {memories
            ? `${memories.length} / 30 saved`
            : "Your saved preferences"}
        </span>
      </div>
      <p>
        Save your writing preferences and project context. Choose them with{" "}
        <strong>Use saved preferences</strong> when asking a question or
        drafting a reply.
      </p>
      <p className={styles.muted}>
        You save these yourself. Turning one off keeps it here and removes it
        from the message picker.
      </p>
      {error && (
        <p className={styles.error} role="alert">
          {error}{" "}
          <button
            type="button"
            disabled={loading || busy}
            onClick={() => void load()}
          >
            Reload preferences
          </button>
        </p>
      )}
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {loading && !memories ? (
        <p role="status">Loading saved preferences…</p>
      ) : memories?.length === 0 && !error ? (
        <p className={styles.empty}>
          Start with how you like replies written, or a project detail you often
          repeat.
        </p>
      ) : null}
      <ul className={styles.list}>
        {(memories ?? []).map((row) => (
          <li key={row.id} data-enabled={row.enabled}>
            <div>
              <span className={styles.meta}>
                {memoryKindLabels[row.kind]} ·{" "}
                {row.enabled ? "Available in your picker" : "Turned off"}
              </span>
              <h3>{row.label}</h3>
              <p>{row.content}</p>
              <small>
                Saved by you
                {Number.isFinite(Date.parse(row.updated_at))
                  ? ` · Updated ${new Date(row.updated_at).toLocaleDateString()}`
                  : ""}
              </small>
            </div>
            <div className={styles.actions}>
              <button
                type="button"
                disabled={busy || loading || loadFailed}
                onClick={() => {
                  setEditingId(row.id);
                  setKind(row.kind);
                  setLabel(row.label);
                  setContent(row.content);
                  setNotice("");
                }}
              >
                Edit
              </button>
              <button
                type="button"
                disabled={busy || loading || loadFailed}
                onClick={() =>
                  void change(`/${row.id}`, "PATCH", { enabled: !row.enabled })
                }
              >
                {row.enabled ? "Turn off" : "Turn on"}
              </button>
              <button
                type="button"
                disabled={busy || loading || loadFailed}
                onClick={() => setForgetId(row.id)}
              >
                Forget
              </button>
            </div>
            {forgetId === row.id && (
              <div className={styles.confirm}>
                <p>
                  Forget “{row.label}”? This removes the saved preference. Text
                  already added to a message stays in that message.
                </p>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void change(
                      `/${row.id}`,
                      "DELETE",
                      undefined,
                      "Preference forgotten.",
                    )
                  }
                >
                  Forget this preference
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setForgetId(null)}
                >
                  Keep it
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={save} className={styles.form}>
        <h3>{editingId ? "Edit saved preference" : "Save something useful"}</h3>
        <div className={styles.row}>
          <label>
            Type
            <select
              disabled={busy || !!editingId}
              value={kind}
              onChange={(event) => {
                const next = event.target.value as MemoryKind;
                setKind(next);
                setContent(
                  next === "reply_length"
                    ? "short"
                    : next === "tone"
                      ? "friendly"
                      : "",
                );
              }}
            >
              {memoryKinds.map((value) => (
                <option key={value} value={value}>
                  {memoryKindLabels[value]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Label
            <input
              disabled={busy}
              maxLength={80}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder={memoryKindLabels[kind]}
            />
          </label>
        </div>
        <label htmlFor={contentId}>
          {memoryKindLabels[kind]}
          {choices ? (
            <select
              id={contentId}
              disabled={busy}
              value={content}
              onChange={(event) => setContent(event.target.value)}
            >
              {choices.map((value) => (
                <option key={value} value={value}>
                  {value[0].toUpperCase() + value.slice(1)}
                </option>
              ))}
            </select>
          ) : (
            <textarea
              id={contentId}
              disabled={busy}
              value={content}
              required
              maxLength={
                kind === "signoff" ? 120 : kind === "study_hours" ? 200 : 500
              }
              rows={3}
              onChange={(event) => setContent(event.target.value)}
              placeholder={
                kind === "signoff"
                  ? "Best, Michael"
                  : kind === "study_hours"
                    ? "Weekdays after 6 p.m."
                    : "A detail you want to reuse in future questions."
              }
            />
          )}
        </label>
        <div className={styles.actions}>
          <button
            type="submit"
            disabled={busy || loading || loadFailed || !content.trim()}
          >
            {busy ? "Saving…" : editingId ? "Save changes" : "Save preference"}
          </button>
          {editingId && (
            <button type="button" disabled={busy} onClick={reset}>
              Cancel edit
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
