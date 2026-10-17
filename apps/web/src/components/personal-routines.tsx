"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  canEnableRoutine,
  type PersonalRoutine,
  type RoutineConfig,
  type RoutinePreview,
  type RoutineRecipe,
  type RoutineResult,
  type RoutineRun,
  readRoutine,
  readRoutineList,
  readRoutinePreview,
  readRoutineRuns,
  recipeDescriptions,
  recipeLabels,
  routineConfigError,
  routineConfigKey,
  routineConfiguration,
  routineDays,
  routineRecipes,
  routineSchedule,
  routineTemplate,
  routineTime,
} from "../lib/personal-routines";
import styles from "./personal-routines.module.css";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

class RoutineRequestError extends Error {
  constructor(
    message: string,
    readonly uncertain = false,
  ) {
    super(message);
  }
}

async function routineRequest(path = "", method = "GET", body?: unknown) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(`${apiBaseUrl}/routines${path}`, {
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
      throw new RoutineRequestError(
        typeof detail === "string"
          ? detail
          : "The routine could not be updated. Please try again.",
        method !== "GET" && path !== "/preview" && response.status >= 500,
      );
    }
    return payload;
  } catch (cause) {
    if (cause instanceof Error && cause.name === "AbortError")
      throw new RoutineRequestError(
        "The routine request took too long. Refresh before trying again.",
        method !== "GET" && path !== "/preview",
      );
    if (cause instanceof RoutineRequestError) throw cause;
    throw new RoutineRequestError(
      "The routine response could not be received. Refresh before trying again.",
      method !== "GET" && path !== "/preview",
    );
  } finally {
    clearTimeout(timeout);
  }
}

function errorText(cause: unknown): string {
  return cause instanceof Error
    ? cause.message
    : "Routines are unavailable. Please try again.";
}

export function RoutineResultView({
  result,
  timezone = "UTC",
}: {
  result: RoutineResult;
  timezone?: string;
}) {
  return (
    <div className={styles.result}>
      <p className={styles.resultTitle}>{result.headline}</p>
      <p className={styles.meta}>
        Saved data checked {routineTime(result.checked_at, timezone)}
      </p>
      {result.items.length ? (
        <ul>
          {result.items.map((item) => (
            <li key={`${item.kind}-${item.id}`}>
              <Link
                href={
                  item.kind === "thread_watch"
                    ? "/inbox#thread-watches"
                    : "/planner"
                }
              >
                {item.title}
              </Link>
              <span className={styles.meta}>
                {item.status.replaceAll("_", " ")}
                {item.due_at ? ` · ${routineTime(item.due_at, timezone)}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p>
          No matching saved items right now. Future runs can change as your
          saved data changes.
        </p>
      )}
      <p className={styles.meta}>{result.notice}</p>
    </div>
  );
}

type PreviewState = RoutinePreview & { configKey: string };
type HistoryState = { loading: boolean; error: string; runs: RoutineRun[] };

export function PersonalRoutines({
  paused = false,
  timezone = "UTC",
}: {
  paused?: boolean;
  timezone?: string;
}) {
  const [routines, setRoutines] = useState<PersonalRoutine[] | null>(null);
  const [serverPaused, setServerPaused] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [config, setConfig] = useState<RoutineConfig>(() =>
    routineTemplate("morning_priorities", timezone),
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [now, setNow] = useState(0);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [history, setHistory] = useState<Record<string, HistoryState>>({});
  const [openHistory, setOpenHistory] = useState<string | null>(null);
  const mounted = useRef(true);
  const busyRef = useRef<string | null>(null);
  const configRef = useRef(config);
  const listSequence = useRef(0);
  const previewSequence = useRef(0);
  const historySequence = useRef<Record<string, number>>({});
  const formRef = useRef<HTMLFormElement>(null);
  const previousPause = useRef(paused);
  const isPaused = paused || serverPaused;

  const refresh = useCallback(async () => {
    const request = ++listSequence.current;
    setLoading(true);
    try {
      const payload = readRoutineList(await routineRequest());
      if (mounted.current && request === listSequence.current) {
        setRoutines(payload.routines);
        setServerPaused(payload.paused);
        setUncertain(false);
      }
    } catch (cause) {
      if (mounted.current && request === listSequence.current)
        setError(errorText(cause));
    } finally {
      if (mounted.current && request === listSequence.current)
        setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      ++listSequence.current;
      ++previewSequence.current;
    };
  }, [refresh]);

  useEffect(() => {
    if (previousPause.current === paused) return;
    previousPause.current = paused;
    void refresh();
  }, [paused, refresh]);

  useEffect(() => {
    if (!preview) return;
    setNow(Date.now());
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.max(0, Date.parse(preview.expires_at) - Date.now()) + 20,
    );
    return () => clearTimeout(timer);
  }, [preview]);

  function updateConfig(next: RoutineConfig) {
    configRef.current = next;
    setConfig(next);
    ++previewSequence.current;
    setPreview(null);
    if (!uncertain) setError("");
    setNotice("");
  }

  function begin(operation: string) {
    if (busyRef.current || uncertain || loading || routines === null)
      return false;
    busyRef.current = operation;
    setBusy(operation);
    setError("");
    setNotice("");
    // A preceding GET cannot replace a successful mutation with an older list.
    ++listSequence.current;
    setLoading(false);
    return true;
  }

  function finish() {
    busyRef.current = null;
    if (mounted.current) setBusy(null);
  }

  function writeError(cause: unknown) {
    if (!mounted.current) return;
    const unknownOutcome =
      !(cause instanceof RoutineRequestError) || cause.uncertain;
    if (unknownOutcome) setUncertain(true);
    setError(
      `${errorText(cause)}${unknownOutcome ? " Refresh your routines to check what was saved before another change." : ""}`,
    );
  }

  async function previewConfig(event: FormEvent) {
    event.preventDefault();
    const validation = routineConfigError(configRef.current);
    if (validation) {
      setError(validation);
      return;
    }
    if (isPaused || !begin("preview")) return;
    const request = ++previewSequence.current;
    const draft = routineConfiguration(configRef.current);
    const key = routineConfigKey(draft);
    setPreview(null);
    try {
      const payload = readRoutinePreview(
        await routineRequest("/preview", "POST", draft),
      );
      if (
        mounted.current &&
        request === previewSequence.current &&
        key === routineConfigKey(configRef.current)
      ) {
        setPreview({ ...payload, configKey: key });
        setNow(Date.now());
      }
    } catch (cause) {
      if (mounted.current && request === previewSequence.current)
        setError(errorText(cause));
    } finally {
      finish();
    }
  }

  async function save(enabled: boolean) {
    const draft = routineConfiguration(configRef.current);
    const validation = routineConfigError(draft);
    if (validation) {
      setError(validation);
      return;
    }
    if (
      enabled &&
      (isPaused || !canEnableRoutine(draft, preview, Date.now()))
    ) {
      setError("Preview this exact schedule again before enabling it.");
      return;
    }
    if (!begin(enabled ? "enable" : "save")) return;
    const identifier = editingId;
    const receipt = enabled ? preview?.preview_token : undefined;
    setPreview(null); // Receipts are single-use, including an uncertain request outcome.
    ++previewSequence.current;
    try {
      const saved = readRoutine(
        await routineRequest(
          identifier ? `/${encodeURIComponent(identifier)}` : "",
          identifier ? "PUT" : "POST",
          { ...draft, enabled, ...(receipt ? { preview_token: receipt } : {}) },
        ),
      );
      if (!mounted.current) return;
      setRoutines((current) => [
        ...(current ?? []).filter((row) => row.id !== saved.id),
        saved,
      ]);
      setEditingId(saved.id);
      if (saved.schedule_status === "dispatch_failed") {
        setError(
          "This routine was saved paused because scheduling could not start. Preview and enable it again to retry.",
        );
      } else {
        setNotice(
          enabled && saved.enabled
            ? "Routine enabled. Its results will appear here after each run."
            : "Routine saved paused.",
        );
      }
    } catch (cause) {
      writeError(cause);
    } finally {
      finish();
    }
  }

  function edit(routine: PersonalRoutine, resuming = false) {
    if (busyRef.current) return;
    setEditingId(routine.id);
    setDeleteId(null);
    updateConfig(routineConfiguration(routine));
    if (resuming)
      setNotice("Preview this routine, then choose Enable this routine.");
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    formRef.current
      ?.querySelector<HTMLInputElement>("input")
      ?.focus({ preventScroll: true });
  }

  async function pauseRoutine(routine: PersonalRoutine) {
    if (!begin(`pause:${routine.id}`)) return;
    try {
      const saved = readRoutine(
        await routineRequest(`/${encodeURIComponent(routine.id)}`, "PUT", {
          ...routineConfiguration(routine),
          enabled: false,
        }),
      );
      if (!mounted.current) return;
      setRoutines((current) =>
        (current ?? []).map((row) => (row.id === saved.id ? saved : row)),
      );
      setNotice(`${saved.name} paused.`);
      if (editingId === saved.id) setPreview(null);
    } catch (cause) {
      writeError(cause);
    } finally {
      finish();
    }
  }

  async function remove(routine: PersonalRoutine) {
    if (!begin(`delete:${routine.id}`)) return;
    try {
      await routineRequest(`/${encodeURIComponent(routine.id)}`, "DELETE");
      if (!mounted.current) return;
      historySequence.current[routine.id] =
        (historySequence.current[routine.id] ?? 0) + 1;
      setRoutines((current) =>
        (current ?? []).filter((row) => row.id !== routine.id),
      );
      setDeleteId(null);
      setNotice("Routine deleted.");
      if (editingId === routine.id) {
        setEditingId(null);
        updateConfig(routineTemplate("morning_priorities", timezone));
        setNotice("Routine deleted.");
      }
    } catch (cause) {
      writeError(cause);
    } finally {
      finish();
    }
  }

  async function showHistory(routine: PersonalRoutine) {
    setOpenHistory(routine.id);
    const request = (historySequence.current[routine.id] ?? 0) + 1;
    historySequence.current[routine.id] = request;
    setHistory((current) => ({
      ...current,
      [routine.id]: { loading: true, error: "", runs: [] },
    }));
    try {
      const runs = readRoutineRuns(
        await routineRequest(`/${encodeURIComponent(routine.id)}/runs`),
      );
      if (mounted.current && historySequence.current[routine.id] === request)
        setHistory((current) => ({
          ...current,
          [routine.id]: { loading: false, error: "", runs },
        }));
    } catch (cause) {
      if (mounted.current && historySequence.current[routine.id] === request)
        setHistory((current) => ({
          ...current,
          [routine.id]: { loading: false, error: errorText(cause), runs: [] },
        }));
    }
  }

  const previewCurrent = canEnableRoutine(config, preview, now);
  const canEnable = !isPaused && previewCurrent;
  const writesBlocked = !!busy || uncertain || loading || routines === null;
  return (
    <section
      id="personal-routines"
      className={styles.panel}
      aria-labelledby="routines-heading"
    >
      <div className={styles.heading}>
        <div>
          <h2 id="routines-heading">Your routines</h2>
          <p>
            Set a time to review what needs attention. Preview the result before
            you enable it.
          </p>
        </div>
        <button
          type="button"
          disabled={loading || !!busy}
          onClick={() => {
            setError("");
            void refresh();
          }}
        >
          Refresh
        </button>
      </div>
      {isPaused && (
        <p className={styles.notice}>
          NavoX is paused. Routines will skip their checks. You can still pause
          or delete a routine.
        </p>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      <form
        ref={formRef}
        onSubmit={(event) => void previewConfig(event)}
        className={styles.form}
      >
        <h3>{editingId ? "Edit routine" : "Create a routine"}</h3>
        <fieldset disabled={!!busy} className={styles.fields}>
          <legend className={styles.srOnly}>Routine settings</legend>
          <label>
            Template
            <select
              value={config.recipe}
              onChange={(event) => {
                const recipe = event.target.value as RoutineRecipe;
                updateConfig({ ...routineTemplate(recipe, config.timezone) });
              }}
            >
              {routineRecipes.map((recipe) => (
                <option key={recipe} value={recipe}>
                  {recipeLabels[recipe]}
                </option>
              ))}
            </select>
          </label>
          <p className={styles.description}>
            {recipeDescriptions[config.recipe]}
          </p>
          <label>
            Name
            <input
              required
              maxLength={100}
              value={config.name}
              onChange={(event) =>
                updateConfig({ ...config, name: event.target.value })
              }
            />
          </label>
          <div className={styles.scheduleFields}>
            <label>
              Time
              <input
                type="time"
                required
                value={`${String(config.hour).padStart(2, "0")}:${String(config.minute).padStart(2, "0")}`}
                onChange={(event) => {
                  const [hour, minute] = event.target.value
                    .split(":")
                    .map(Number);
                  updateConfig({ ...config, hour, minute });
                }}
              />
            </label>
            <label>
              Timezone
              <input
                required
                maxLength={64}
                value={config.timezone}
                placeholder="America/New_York"
                onChange={(event) =>
                  updateConfig({ ...config, timezone: event.target.value })
                }
              />
            </label>
          </div>
          <fieldset className={styles.days}>
            <legend>Days</legend>
            {routineDays.map((day, index) => (
              <label key={day}>
                <input
                  type="checkbox"
                  checked={config.days_of_week.includes(index)}
                  onChange={(event) =>
                    updateConfig({
                      ...config,
                      days_of_week: event.target.checked
                        ? [...config.days_of_week, index]
                        : config.days_of_week.filter(
                            (value) => value !== index,
                          ),
                    })
                  }
                />
                {day}
              </label>
            ))}
          </fieldset>
        </fieldset>
        <p className={styles.meta}>
          Uses saved tasks and thread-watch results. Quiet hours and the NavoX
          pause control apply. This does not refresh connected apps or send
          email.
        </p>
        <div className={styles.actions}>
          <button type="submit" disabled={writesBlocked || isPaused}>
            {busy === "preview" ? "Preparing preview…" : "Preview once"}
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={writesBlocked || !canEnable}
            onClick={() => void save(true)}
          >
            {busy === "enable" ? "Enabling…" : "Enable this routine"}
          </button>
          <button
            type="button"
            disabled={writesBlocked}
            onClick={() => void save(false)}
          >
            {busy === "save" ? "Saving…" : "Save paused"}
          </button>
          {editingId && (
            <button
              type="button"
              disabled={!!busy}
              onClick={() => {
                setEditingId(null);
                updateConfig(routineTemplate("morning_priorities", timezone));
              }}
            >
              New routine
            </button>
          )}
        </div>
        {preview && (
          <div className={styles.preview}>
            <h3>Preview</h3>
            <RoutineResultView
              result={preview.result}
              timezone={config.timezone}
            />
            <p className={styles.meta}>
              {previewCurrent
                ? isPaused
                  ? "This preview is ready. Resume NavoX before enabling the routine."
                  : "This preview is ready. Enabling saves this exact schedule."
                : "This preview has expired. Preview again before enabling."}
            </p>
          </div>
        )}
      </form>
      <div className={styles.saved} aria-busy={loading}>
        <h3>Saved routines</h3>
        {loading && <p role="status">Loading your routines…</p>}
        {!loading && routines?.length === 0 && (
          <p>No routines yet. Start with a preview above.</p>
        )}
        {!loading && routines === null && !error && (
          <p>Refresh to load your routines.</p>
        )}
        {routines?.map((routine) => (
          <article key={routine.id} className={styles.card}>
            <div className={styles.heading}>
              <h4>{routine.name}</h4>
              <span className={styles.badge}>
                {routine.enabled
                  ? isPaused
                    ? "NavoX paused"
                    : "Enabled"
                  : "Paused"}
              </span>
            </div>
            <p className={styles.meta}>{routineSchedule(routine)}</p>
            {routine.schedule_status === "dispatch_failed" ? (
              <p className={styles.error}>
                Scheduling did not start. Preview and enable again to retry.
              </p>
            ) : routine.enabled && routine.next_run_at ? (
              <p>
                Next run: {routineTime(routine.next_run_at, routine.timezone)}
              </p>
            ) : null}
            {routine.last_run_at && (
              <p className={styles.meta}>
                Last run attempt{" "}
                {routineTime(routine.last_run_at, routine.timezone)}
              </p>
            )}
            {routine.last_result && (
              <details className={styles.details}>
                <summary>Latest saved result</summary>
                <RoutineResultView
                  result={routine.last_result}
                  timezone={routine.timezone}
                />
              </details>
            )}
            <div className={styles.actions}>
              <button
                type="button"
                disabled={!!busy}
                onClick={() => edit(routine)}
              >
                Edit
              </button>
              {routine.enabled ? (
                <button
                  type="button"
                  disabled={writesBlocked}
                  onClick={() => void pauseRoutine(routine)}
                >
                  Pause
                </button>
              ) : (
                <button
                  type="button"
                  disabled={!!busy || isPaused}
                  onClick={() => edit(routine, true)}
                >
                  Preview to resume
                </button>
              )}
              <button
                type="button"
                disabled={history[routine.id]?.loading}
                onClick={() => {
                  if (openHistory === routine.id) setOpenHistory(null);
                  else void showHistory(routine);
                }}
              >
                {openHistory === routine.id ? "Hide history" : "Run history"}
              </button>
              <button
                type="button"
                disabled={writesBlocked}
                onClick={() => setDeleteId(routine.id)}
              >
                Delete
              </button>
            </div>
            {deleteId === routine.id && (
              <div className={styles.confirm}>
                <p>Delete this routine and its saved run history?</p>
                <button
                  type="button"
                  disabled={writesBlocked}
                  onClick={() => void remove(routine)}
                >
                  Delete routine
                </button>
                <button
                  type="button"
                  disabled={!!busy}
                  onClick={() => setDeleteId(null)}
                >
                  Keep routine
                </button>
              </div>
            )}
            {openHistory === routine.id && (
              <div
                className={styles.history}
                aria-busy={history[routine.id]?.loading}
              >
                <h5>Recent runs</h5>
                {history[routine.id]?.loading ? (
                  <p role="status">Loading run history…</p>
                ) : history[routine.id]?.error ? (
                  <p role="alert">
                    {history[routine.id].error}{" "}
                    <button
                      type="button"
                      onClick={() => void showHistory(routine)}
                    >
                      Retry history
                    </button>
                  </p>
                ) : history[routine.id]?.runs.length === 0 ? (
                  <p>
                    No runs yet. Results appear after the first scheduled check.
                  </p>
                ) : (
                  history[routine.id]?.runs.map((run) => (
                    <details key={run.id} className={styles.details}>
                      <summary>
                        {routineTime(run.checked_at, routine.timezone)} ·{" "}
                        {run.status}
                      </summary>
                      <p>{run.notice}</p>
                      {run.result && (
                        <RoutineResultView
                          result={run.result}
                          timezone={routine.timezone}
                        />
                      )}
                    </details>
                  ))
                )}
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
