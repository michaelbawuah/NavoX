"use client";

import type {
  EvidenceResource,
  KnowledgeResourceType,
  RecentSearch,
  SearchExclusion,
  SearchMode,
  SearchQueryBody,
  SearchResponse,
} from "@navox/contracts";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  addExclusion,
  answerNotice,
  clearRecentSearches,
  coverageNotes,
  exclusiveEndOfDay,
  loadExclusions,
  loadRecentSearches,
  needsFreshSearch,
  RequestGate,
  removeExclusion,
  resourceTypeLabel,
  runLiveSearch,
  runSearch,
  SearchRequestError,
  safeSourceUrl,
  searchTime,
  startOfDay,
  waitForSearchRefresh,
} from "../lib/search";
import { KnowledgeAsk } from "./knowledge-ask";
import { KnowledgeConflicts } from "./knowledge-conflicts";
import { KnowledgeRefresh } from "./knowledge-refresh";
import { NavoXEmptyState, NavoXErrorState, NavoXSkeleton } from "./navox-ui";
import styles from "./search-workspace.module.css";
import { WorkspaceShell } from "./workspace-shell";

const modes: [SearchMode, string][] = [
  ["AUTO", "Auto"],
  ["SEARCH", "Search"],
  ["ASK", "Ask"],
];

const filterTypes: KnowledgeResourceType[] = [
  "EMAIL",
  "EMAIL_THREAD",
  "CALENDAR_EVENT",
  "DOCUMENT",
  "FILE",
  "CANVAS_ASSIGNMENT",
  "CANVAS_ANNOUNCEMENT",
  "MEETING",
  "COMMITMENT",
  "SUBSCRIPTION",
  "NEWS_STORY",
  "TASK",
];

export function ResultCard({
  resource,
  onExcludeSource,
  onExcludeType,
}: {
  resource: EvidenceResource;
  onExcludeSource?: (connectionId: string) => void;
  onExcludeType?: (resourceType: KnowledgeResourceType) => void;
}) {
  const url = safeSourceUrl(resource.canonical_url);
  const connectionId = resource.provenance?.connection_id;
  return (
    <article className={styles.result}>
      <header>
        <span className={styles.kind}>
          {resourceTypeLabel(resource.source_type)}
        </span>
        <h3>{resource.title ?? "Untitled record"}</h3>
        <p className={styles.meta}>
          <span>
            {resource.origin === "CONNECTED"
              ? "From your sources"
              : "NavoX record"}
          </span>
          <span aria-hidden="true">·</span>
          <span>Updated {searchTime(resource.source_updated_at)}</span>
        </p>
      </header>
      {resource.excerpts.map((excerpt) => (
        <blockquote key={`${resource.resource_id}-${excerpt.chunk_index ?? 0}`}>
          {excerpt.section_title && <cite>{excerpt.section_title}</cite>}
          <p>{excerpt.text}</p>
        </blockquote>
      ))}
      <footer>
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            referrerPolicy="no-referrer"
          >
            Open at source
            <span className={styles.srOnly}> (opens in a new tab)</span>
          </a>
        ) : (
          <span className={styles.muted}>No source link available</span>
        )}
        {resource.origin === "CONNECTED" && (
          <KnowledgeRefresh
            key={`${resource.resource_id}:${resource.fresh_until ?? "unknown"}`}
            resourceId={resource.resource_id}
          />
        )}
        {((connectionId && onExcludeSource) || onExcludeType) && (
          <details className={styles.resultOptions}>
            <summary>Search preferences</summary>
            <div>
              {connectionId && onExcludeSource && (
                <button
                  type="button"
                  className={styles.quiet}
                  onClick={() => onExcludeSource(connectionId)}
                >
                  Hide this source
                </button>
              )}
              {onExcludeType && (
                <button
                  type="button"
                  className={styles.quiet}
                  onClick={() => onExcludeType(resource.source_type)}
                >
                  Hide {resourceTypeLabel(resource.source_type).toLowerCase()}
                </button>
              )}
            </div>
          </details>
        )}
      </footer>
    </article>
  );
}

export function SearchWorkspace() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("AUTO");
  const [resourceType, setResourceType] = useState<string>("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [recent, setRecent] = useState<RecentSearch[]>([]);
  const [exclusions, setExclusions] = useState<SearchExclusion[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resultsHeading = useRef<HTMLHeadingElement>(null);
  const controller = useRef<AbortController | null>(null);
  const pendingLive = useRef<{ key: string; requestId: string } | null>(null);
  const searchGate = useRef(new RequestGate());
  const sidebarGate = useRef(new RequestGate());

  const refreshSidebar = useCallback(async () => {
    const token = sidebarGate.current.next();
    try {
      const [history, hidden] = await Promise.all([
        loadRecentSearches(),
        loadExclusions(),
      ]);
      if (!sidebarGate.current.isCurrent(token)) return;
      setRecent(history);
      setExclusions(hidden);
    } catch (failure) {
      if (!sidebarGate.current.isCurrent(token)) return;
      if (failure instanceof SearchRequestError && failure.status === 401) {
        setError(failure.message);
      }
    }
  }, []);

  useEffect(() => {
    void refreshSidebar();
    return () => controller.current?.abort();
  }, [refreshSidebar]);

  const submit = useCallback(
    async (event?: FormEvent<HTMLFormElement>, override?: string) => {
      event?.preventDefault();
      // Ask mode is owned by the grounded-answer panel; this form never posts.
      if (mode === "ASK") return;
      const value = (override ?? query).trim();
      if (!value) return;
      controller.current?.abort();
      const active = new AbortController();
      controller.current = active;
      const token = searchGate.current.next();
      setBusy(true);
      setError(null);
      try {
        const payload: SearchQueryBody = {
          query: value,
          mode,
          types: resourceType
            ? [resourceType as KnowledgeResourceType]
            : undefined,
          date_range:
            from || to
              ? {
                  start: from ? startOfDay(from) : null,
                  end: to ? exclusiveEndOfDay(to) : null,
                }
              : null,
          limit: 20,
        };
        let result: SearchResponse;
        if (needsFreshSearch(value)) {
          const key = JSON.stringify(payload);
          if (pendingLive.current?.key !== key) {
            pendingLive.current = { key, requestId: crypto.randomUUID() };
          }
          result = await runLiveSearch(
            payload,
            pendingLive.current.requestId,
            active.signal,
          );
          pendingLive.current = null;
        } else {
          result = await runSearch(payload, active.signal);
        }
        if (!searchGate.current.isCurrent(token)) return;
        setResponse(result);
        setQuery(value);
        setBusy(false);
        resultsHeading.current?.focus();
        await refreshSidebar();
        // Poll only cached retrieval. Source dispatch happens once, with one ID;
        // another query or exclusion aborts this bounded progressive update.
        if (result.refresh?.queued) {
          for (let attempt = 0; attempt < 6; attempt += 1) {
            await waitForSearchRefresh(active.signal);
            if (!searchGate.current.isCurrent(token)) return;
            const current = await runSearch(payload, active.signal, false);
            if (!searchGate.current.isCurrent(token)) return;
            const fresh = current.results.every(
              (item) =>
                item.origin !== "CONNECTED" ||
                (item.fresh_until && Date.parse(item.fresh_until) > Date.now()),
            );
            setResponse({
              ...current,
              refresh: fresh
                ? { ...result.refresh, queued: 0, state: "NOT_NEEDED" }
                : result.refresh,
            });
            if (fresh) break;
          }
        }
      } catch (failure) {
        if (failure instanceof Error && failure.name === "AbortError") return;
        if (!searchGate.current.isCurrent(token)) return;
        // A failed search must not keep showing results the caller invalidated.
        setResponse(null);
        setError(
          failure instanceof SearchRequestError
            ? failure.message
            : "We couldn’t run that search. Please try again.",
        );
      } finally {
        if (searchGate.current.isCurrent(token)) setBusy(false);
      }
    },
    [from, mode, query, refreshSidebar, resourceType, to],
  );

  const hide = useCallback(
    async (payload: Parameters<typeof addExclusion>[0]) => {
      // Invalidate in-flight work and the displayed results before the write, so
      // excluded material is never left on screen if the refresh fails.
      searchGate.current.invalidate();
      sidebarGate.current.invalidate();
      setResponse(null);
      try {
        await addExclusion(payload);
        await refreshSidebar();
        await submit(undefined, query);
      } catch {
        setError("We couldn’t save that preference. Please try again.");
      }
    },
    [query, refreshSidebar, submit],
  );

  const unhide = useCallback(
    async (exclusionId: string) => {
      sidebarGate.current.invalidate();
      try {
        await removeExclusion(exclusionId);
        await refreshSidebar();
      } catch {
        setError("We couldn’t update that preference. Please try again.");
      }
    },
    [refreshSidebar],
  );

  const clearHistory = useCallback(async () => {
    controller.current?.abort();
    searchGate.current.invalidate();
    setBusy(false);
    sidebarGate.current.invalidate();
    try {
      await clearRecentSearches();
      setRecent([]);
    } catch {
      setError("We couldn’t clear your recent searches. Please try again.");
    }
  }, []);

  const notice = response ? answerNotice(response) : null;
  const notes = response ? coverageNotes(response) : [];

  return (
    <WorkspaceShell current="Search">
      <div className={styles.workspace}>
        <a className={styles.skipLink} href="#search-main">
          Skip to search
        </a>
        <div id="search-main" className={styles.main}>
          <section className={styles.intro}>
            <h1>Find what you need</h1>
            <p>
              Search your email, events, and saved information in one place.
            </p>
          </section>
          <form className={styles.form} onSubmit={submit} aria-busy={busy}>
            {mode !== "ASK" && (
              <div className={styles.queryRow}>
                <label className={styles.field} htmlFor="search-query">
                  <span>What are you looking for?</span>
                  <input
                    id="search-query"
                    name="query"
                    value={query}
                    maxLength={2000}
                    placeholder="Find an email, event, task, or document…"
                    onChange={(event) => setQuery(event.target.value)}
                    required
                    autoComplete="off"
                  />
                </label>
                <button
                  className={styles.searchButton}
                  type="submit"
                  disabled={busy || !query.trim()}
                >
                  {busy ? "Searching…" : "Search"}
                </button>
              </div>
            )}
            <details className={styles.options}>
              <summary>
                Filters &amp; options{" "}
                {from || to ? "· Custom dates" : "· All time"}
              </summary>
              <div className={styles.controls}>
                <fieldset className={styles.modes}>
                  <legend>Mode</legend>
                  {modes.map(([value, label]) => (
                    <label key={value} data-active={mode === value}>
                      <input
                        type="radio"
                        name="mode"
                        value={value}
                        checked={mode === value}
                        onChange={() => setMode(value)}
                      />
                      {label}
                    </label>
                  ))}
                </fieldset>
                {mode !== "ASK" && (
                  <>
                    <label className={styles.field} htmlFor="search-type">
                      <span>Type</span>
                      <select
                        id="search-type"
                        value={resourceType}
                        onChange={(event) =>
                          setResourceType(event.target.value)
                        }
                      >
                        <option value="">Any type</option>
                        {filterTypes.map((value) => (
                          <option key={value} value={value}>
                            {resourceTypeLabel(value)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className={styles.field} htmlFor="search-from">
                      <span>From</span>
                      <input
                        id="search-from"
                        type="date"
                        value={from}
                        onChange={(event) => setFrom(event.target.value)}
                      />
                    </label>
                    <label className={styles.field} htmlFor="search-to">
                      <span>To</span>
                      <input
                        id="search-to"
                        type="date"
                        value={to}
                        onChange={(event) => setTo(event.target.value)}
                      />
                    </label>
                  </>
                )}
              </div>
            </details>
          </form>

          {mode === "ASK" ? (
            <KnowledgeAsk initialQuestion={query} />
          ) : (
            <div className={styles.columns}>
              <section className={styles.results} aria-live="polite">
                <div className={styles.resultsHeader}>
                  <h2 ref={resultsHeading} tabIndex={-1}>
                    Results
                  </h2>
                  {response && (
                    <p className={styles.meta}>
                      {response.results.length === 1
                        ? "1 result you can open"
                        : `${response.results.length} results you can open`}
                    </p>
                  )}
                </div>
                {busy && <NavoXSkeleton label="Searching your sources…" />}
                {error && (
                  <NavoXErrorState
                    message={error}
                    onRetry={() => void submit(undefined, query)}
                  />
                )}
                {!busy && !error && !response && (
                  <NavoXEmptyState title="Search your own sources">
                    <p>
                      Start with a name, subject, or phrase. Results link back
                      to your email, calendar, and other connected sources.
                    </p>
                    <a href="/connections" className={styles.emptyAction}>
                      Manage connected apps
                    </a>
                  </NavoXEmptyState>
                )}
                {!busy && response && (
                  <>
                    {notice && <p className={styles.notice}>{notice}</p>}
                    <KnowledgeConflicts conflicts={response.conflicts ?? []} />
                    {response.results.length === 0 ? (
                      <NavoXEmptyState title="No results you can open">
                        <p>
                          Try fewer words, widen the dates, or check that the
                          source is still connected.
                        </p>
                        <a href="/connections" className={styles.emptyAction}>
                          Check connected apps
                        </a>
                      </NavoXEmptyState>
                    ) : (
                      <ul className={styles.list}>
                        {response.results.map((resource) => (
                          <li
                            key={`${resource.source_type}-${resource.resource_id}`}
                          >
                            <ResultCard
                              resource={resource}
                              onExcludeSource={(connectionId) =>
                                void hide({
                                  scope: "SOURCE",
                                  source_connection_id: connectionId,
                                })
                              }
                              onExcludeType={(value) =>
                                void hide({
                                  scope: "TYPE",
                                  resource_type: value,
                                })
                              }
                            />
                          </li>
                        ))}
                      </ul>
                    )}
                    {response.structured_facts.length > 0 && (
                      <section className={styles.facts}>
                        <h3>Key dates</h3>
                        <ul>
                          {response.structured_facts.map((fact) => (
                            <li key={fact.fact_id}>
                              <strong>{fact.label}</strong>
                              <span>{fact.value}</span>
                              <small>{fact.authority}</small>
                            </li>
                          ))}
                        </ul>
                      </section>
                    )}
                    {notes.length > 0 && (
                      <section className={styles.notes}>
                        <h3>What this search didn’t cover</h3>
                        <ul>
                          {notes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      </section>
                    )}
                    {response.suggested_followups.length > 0 && (
                      <section className={styles.followups}>
                        <h3>Making this search narrower</h3>
                        <ul>
                          {response.suggested_followups.map((suggestion) => (
                            <li key={suggestion}>{suggestion}</li>
                          ))}
                        </ul>
                      </section>
                    )}
                  </>
                )}
              </section>

              <aside
                className={styles.side}
                aria-label="Search history and preferences"
              >
                <details className={styles.panel}>
                  <summary>
                    Recent searches <span>{recent.length}</span>
                  </summary>
                  <div className={styles.panelContent}>
                    <div className={styles.panelHeader}>
                      {recent.length > 0 && (
                        <button
                          type="button"
                          className={styles.quiet}
                          onClick={() => void clearHistory()}
                        >
                          Clear history
                        </button>
                      )}
                    </div>
                    {recent.length === 0 ? (
                      <p className={styles.muted}>
                        Searches you run appear here. Clearing history never
                        removes your sources.
                      </p>
                    ) : (
                      <ul className={styles.chips}>
                        {recent.map((entry) => (
                          <li key={entry.id}>
                            <button
                              type="button"
                              className={styles.chip}
                              onClick={() =>
                                void submit(undefined, entry.query)
                              }
                            >
                              {entry.query}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </details>
                <details className={styles.panel}>
                  <summary>
                    Hidden from search <span>{exclusions.length}</span>
                  </summary>
                  <div className={styles.panelContent}>
                    {exclusions.length === 0 ? (
                      <p className={styles.muted}>
                        Nothing is hidden. Hide a source or a type from a result
                        to keep it out of future searches.
                      </p>
                    ) : (
                      <ul className={styles.chips}>
                        {exclusions.map((entry) => (
                          <li key={entry.id}>
                            <span className={styles.chip}>
                              {entry.scope === "TYPE"
                                ? resourceTypeLabel(
                                    entry.resource_type ?? "OTHER",
                                  )
                                : entry.scope === "SOURCE"
                                  ? "A connected source"
                                  : entry.scope === "FOLDER"
                                    ? "A folder"
                                    : "One record"}
                            </span>
                            <button
                              type="button"
                              className={styles.quiet}
                              onClick={() => void unhide(entry.id)}
                            >
                              Unhide
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </details>
              </aside>
            </div>
          )}
        </div>
      </div>
    </WorkspaceShell>
  );
}
