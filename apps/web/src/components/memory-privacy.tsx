"use client";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  notifyAccountSessionChanged,
  notifyAccountSessionChanging,
  observeAccountSession,
} from "../lib/account-session";
import {
  MemoryClientError,
  type MemoryConversation,
  type MemoryFact,
  type MemoryHistory,
  type MemoryPrivacy,
  memorySourceLabel,
  readMemoryConversation,
  readMemoryFacts,
  readMemoryHistory,
  readMemoryPrivacy,
  conversationMemoryRequest as request,
} from "../lib/conversation-memory";
import {
  consumeMemoryForgetDraft,
  validMemoryForgetQuery,
} from "../lib/vox-memory-review";
import styles from "./memory-privacy.module.css";
import { PersonalMemoryPanel } from "./personal-memory";

function localDate(value: string, timezone?: string) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeZone: timezone,
    }).format(new Date(value));
  } catch {
    return "Date unavailable";
  }
}
const noContent = () => undefined;

export function MemoryPrivacyPanel({
  accountKey,
  timezone,
}: {
  accountKey: string;
  timezone?: string;
}) {
  const [privacy, setPrivacy] = useState<MemoryPrivacy | null>(null);
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [history, setHistory] = useState<MemoryHistory[] | null>(null);
  const [conversation, setConversation] = useState<MemoryConversation | null>(
    null,
  );
  const [historyOpen, setHistoryOpen] = useState(false);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [retention, setRetention] = useState("");
  const [disclosure, setDisclosure] = useState(false);
  const [editing, setEditing] = useState<MemoryFact | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [content, setContent] = useState("");
  const [sensitiveAgreement, setSensitiveAgreement] = useState(false);
  const [forget, setForget] = useState<MemoryFact | MemoryHistory | null>(null);
  const [deleteAll, setDeleteAll] = useState(false);
  const [deletePhrase, setDeletePhrase] = useState("");
  const [legacyOpen, setLegacyOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mustReload, setMustReload] = useState(false);
  const epoch = useRef(0),
    historyEpoch = useRef(0),
    mounted = useRef(false);
  const id = useId();
  const clearPrivate = useCallback(() => {
    ++epoch.current;
    ++historyEpoch.current;
    setReady(false);
    setPrivacy(null);
    setFacts([]);
    setHistory(null);
    setConversation(null);
    setConversationLoading(false);
    setQuery("");
    setRetention("");
    setDisclosure(false);
    setEditing(null);
    setFormOpen(false);
    setLabel("");
    setContent("");
    setSensitiveAgreement(false);
    setForget(null);
    setDeleteAll(false);
    setDeletePhrase("");
    setLegacyOpen(false);
    setBusy(false);
  }, []);
  const load = useCallback(async () => {
    const current = ++epoch.current;
    setLoading(true);
    setError("");
    try {
      const [saved, rows] = await Promise.all([
        request(accountKey, "/privacy", readMemoryPrivacy),
        request(accountKey, "/facts", readMemoryFacts),
      ]);
      if (mounted.current && current === epoch.current) {
        setPrivacy(saved);
        setFacts(rows);
        setMustReload(false);
        setRetention(
          saved.history_enabled ? String(saved.retention_days ?? "never") : "",
        );
        setDisclosure(false);
      }
    } catch {
      if (mounted.current && current === epoch.current)
        setError("Your memory couldn’t load. Try again.");
    } finally {
      if (mounted.current && current === epoch.current) setLoading(false);
    }
  }, [accountKey]);
  useEffect(() => {
    mounted.current = true;
    const observer = observeAccountSession(
      {
        checking: clearPrivate,
        restored: (access) => {
          if (
            !access.account ||
            `${access.account.workspace.id}:${access.account.id}` !== accountKey
          ) {
            clearPrivate();
            return;
          }
          setReady(true);
          void load();
        },
        unavailable: () => {
          clearPrivate();
          setLoading(false);
          setError("Check your sign-in to open your memory.");
        },
      },
      { restoreAccess: undefined },
    );
    return () => {
      mounted.current = false;
      observer.dispose();
      ++epoch.current;
      ++historyEpoch.current;
    };
  }, [accountKey, clearPrivate, load]);
  useEffect(() => {
    if (forget) document.getElementById(`${id}-forget`)?.focus();
  }, [forget, id]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: consume a one-use handoff only after fresh owner/privacy restoration.
  useEffect(() => {
    if (!ready || !privacy) return;
    const review = () => {
      let query: string | null = null;
      try {
        query = consumeMemoryForgetDraft(window.sessionStorage, accountKey);
      } catch {
        /* Settings still supports typed search. */
      }
      const url = new URL(window.location.href);
      if (
        !query &&
        url.searchParams.get("section") === "memory" &&
        validMemoryForgetQuery(url.searchParams.get("forget"))
      )
        query = url.searchParams.get("forget");
      if (!query) return;
      url.searchParams.delete("forget");
      window.history.replaceState(
        {},
        "",
        `${url.pathname}${url.search}#memory-privacy`,
      );
      setQuery(query);
      setLegacyOpen(true);
      setNotice(`Review matches for “${query}”. Nothing has been deleted.`);
      void searchHistory(undefined, query);
      document
        .getElementById("memory-privacy")
        ?.scrollIntoView?.({ block: "start" });
    };
    review();
    window.addEventListener("navox:memory-forget-ready", review);
    return () =>
      window.removeEventListener("navox:memory-forget-ready", review);
  }, [accountKey, ready, privacy?.generation]);
  async function searchHistory(event?: FormEvent, searchQuery = query) {
    event?.preventDefault();
    if (!ready || busy || mustReload) return;
    const current = ++historyEpoch.current,
      ownerEpoch = epoch.current;
    setError("");
    setConversation(null);
    setHistory(null);
    setHistoryOpen(true);
    try {
      const rows = await request(
        accountKey,
        `/history?query=${encodeURIComponent(searchQuery.trim())}`,
        readMemoryHistory,
      );
      if (
        mounted.current &&
        current === historyEpoch.current &&
        ownerEpoch === epoch.current
      )
        setHistory(rows);
    } catch {
      if (
        mounted.current &&
        current === historyEpoch.current &&
        ownerEpoch === epoch.current
      )
        setError("Conversation history couldn’t load. Try again.");
    }
  }
  async function openConversation(row: MemoryHistory) {
    const current = ++historyEpoch.current,
      ownerEpoch = epoch.current;
    setError("");
    setConversation(null);
    setConversationLoading(true);
    try {
      const result = await request(
        accountKey,
        `/history/${encodeURIComponent(row.id)}`,
        readMemoryConversation,
      );
      if (
        mounted.current &&
        current === historyEpoch.current &&
        ownerEpoch === epoch.current
      )
        setConversation(result);
    } catch {
      if (
        mounted.current &&
        current === historyEpoch.current &&
        ownerEpoch === epoch.current
      )
        setError("That conversation couldn’t open. Try again.");
    } finally {
      if (
        mounted.current &&
        current === historyEpoch.current &&
        ownerEpoch === epoch.current
      )
        setConversationLoading(false);
    }
  }
  async function change(
    operation: () => Promise<unknown>,
    message: string,
    privacyChange = false,
  ) {
    if (!ready || !privacy || busy || mustReload) return;
    setBusy(true);
    setError("");
    setNotice("");
    const current = epoch.current;
    let sessionChange:
      | ReturnType<typeof notifyAccountSessionChanging>
      | undefined;
    try {
      sessionChange = privacyChange
        ? notifyAccountSessionChanging()
        : undefined;
      await operation();
      if (!mounted.current) return;
      if (privacyChange || current === epoch.current) {
        setNotice(message);
        setForget(null);
        setConversation(null);
        setHistory(null);
        setHistoryOpen(false);
        setFormOpen(false);
        setEditing(null);
        setLabel("");
        setContent("");
        setSensitiveAgreement(false);
        if (!privacyChange) await load();
      }
    } catch (cause) {
      if (mounted.current && (privacyChange || current === epoch.current)) {
        setMustReload(true);
        setError(
          cause instanceof MemoryClientError
            ? cause.message
            : "The change couldn’t be checked. Reload your memory before trying again.",
        );
      }
    } finally {
      if (sessionChange) notifyAccountSessionChanged(sessionChange);
      if (mounted.current) setBusy(false);
    }
  }
  function savePrivacy(
    offTheRecord = privacy?.off_the_record ?? false,
    useSavedHistory = false,
  ) {
    if (!privacy) return;
    const enabled = useSavedHistory
      ? privacy.history_enabled
      : retention !== "";
    void change(
      () =>
        request(accountKey, "/privacy", readMemoryPrivacy, "PUT", {
          history_enabled: enabled,
          retention_days: useSavedHistory
            ? privacy.retention_days
            : retention === "30"
              ? 30
              : retention === "90"
                ? 90
                : null,
          off_the_record: offTheRecord,
          generation: privacy.generation,
          disclosure_accepted:
            enabled && !privacy.history_enabled ? disclosure : false,
        }),
      offTheRecord
        ? "Off the record is on. New conversations won’t be saved or learned from."
        : "Memory settings saved.",
      true,
    );
  }
  function saveFact(event: FormEvent) {
    event.preventDefault();
    void change(
      () =>
        request(
          accountKey,
          editing ? `/facts/${encodeURIComponent(editing.id)}` : "/facts",
          noContent,
          editing ? "PATCH" : "POST",
          {
            label: label.trim(),
            content: content.trim(),
            sensitive_agreement: sensitiveAgreement,
          },
        ),
      editing
        ? "Saved fact corrected."
        : "Saved. You can edit or forget this at any time.",
      !!editing,
    );
  }
  const controlsDisabled = busy || loading || mustReload || !privacy || !ready;
  return (
    <section
      className={styles.panel}
      aria-labelledby={`${id}-title`}
      aria-busy={loading || busy}
    >
      <div className={styles.heading}>
        <h2 id={`${id}-title`}>Memory &amp; privacy</h2>
        <span className={styles.privateBadge}>You’re in control</span>
      </div>
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {error && (
        <div role="alert" className={styles.error}>
          <p>{error}</p>
          <button
            type="button"
            disabled={!ready || busy}
            onClick={() => void load()}
          >
            Retry
          </button>
        </div>
      )}
      {!ready || (!privacy && loading) ? (
        <p role="status">Opening your private memory…</p>
      ) : privacy ? (
        <>
          <div className={styles.group}>
            <div className={styles.heading}>
              <div>
                <h3>What NavoX knows about me</h3>
                <p className={styles.muted}>
                  Saved facts, with their source. Change anything that no longer
                  fits.
                </p>
              </div>
              <button
                type="button"
                disabled={controlsDisabled}
                onClick={() => {
                  setEditing(null);
                  setLabel("");
                  setContent("");
                  setSensitiveAgreement(false);
                  setFormOpen(true);
                }}
              >
                Add something
              </button>
            </div>
            {!facts.length && (
              <p className={styles.muted}>
                No saved facts yet. Add something you want NavoX to remember.
              </p>
            )}
            <ul className={styles.facts}>
              {facts.map((fact) => (
                <li
                  key={fact.id}
                  data-memory-match={
                    !!query.trim() &&
                    `${fact.label} ${fact.content}`
                      .toLocaleLowerCase()
                      .includes(query.trim().toLocaleLowerCase())
                  }
                >
                  <div>
                    <strong>{fact.label}</strong>
                    <p className={styles.prewrap}>{fact.content}</p>
                    <small>
                      {memorySourceLabel(fact.source_kind)} ·{" "}
                      {localDate(fact.created_at, timezone)}
                      {fact.sensitive ? " · Saved with your agreement" : ""}
                    </small>
                  </div>
                  <div className={styles.actions}>
                    <button
                      type="button"
                      disabled={controlsDisabled}
                      onClick={() => {
                        setEditing(fact);
                        setLabel(fact.label);
                        setContent(fact.content);
                        setSensitiveAgreement(false);
                        setFormOpen(true);
                      }}
                    >
                      Edit<span className={styles.srOnly}> {fact.label}</span>
                    </button>
                    <button
                      type="button"
                      disabled={controlsDisabled}
                      onClick={() => setForget(fact)}
                    >
                      Delete<span className={styles.srOnly}> {fact.label}</span>
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            <p className={styles.muted}>
              Say “forget what I said about…” in Vox to find it, then review the
              removal here.
            </p>
            {formOpen && (
              <form className={styles.factForm} onSubmit={saveFact}>
                <h3>
                  {editing ? "Edit saved fact" : "Add something to remember"}
                </h3>
                {editing?.source_conversation_id && (
                  <p className={styles.sensitiveNote}>
                    Saving this correction also forgets its original
                    conversation, summary and other facts learned from it. Your
                    corrected fact stays.
                  </p>
                )}
                <label>
                  Label
                  <input
                    maxLength={80}
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    required
                    disabled={controlsDisabled}
                  />
                </label>
                <label>
                  What should NavoX remember?
                  <textarea
                    maxLength={500}
                    rows={3}
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    required
                    disabled={controlsDisabled}
                  />
                </label>
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    checked={sensitiveAgreement}
                    disabled={controlsDisabled}
                    onChange={(e) => setSensitiveAgreement(e.target.checked)}
                  />
                  <span>
                    This includes a health, family or mental-health detail, and
                    I agree to save this specific fact.
                  </span>
                </label>
                <div className={styles.actions}>
                  <button
                    type="submit"
                    disabled={
                      controlsDisabled || !label.trim() || !content.trim()
                    }
                  >
                    {editing?.source_conversation_id
                      ? "Save correction and forget source"
                      : "Save fact"}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setFormOpen(false);
                      setEditing(null);
                      setSensitiveAgreement(false);
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
            <details
              open={legacyOpen}
              onToggle={(e) => setLegacyOpen(e.currentTarget.open)}
            >
              <summary>Writing preferences and project context</summary>
              {legacyOpen && <PersonalMemoryPanel />}
            </details>
          </div>
          <div className={styles.group}>
            <div className={styles.heading}>
              <div>
                <h3>Off the record</h3>
                <p className={styles.muted}>
                  New conversations aren’t saved or used to learn facts while
                  this is on.
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={privacy.off_the_record}
                disabled={controlsDisabled}
                className={styles.switch}
                onClick={() => savePrivacy(!privacy.off_the_record, true)}
              >
                {privacy.off_the_record ? "On" : "Off"}
              </button>
            </div>
          </div>
          <div className={styles.group}>
            <h3>Conversation history</h3>
            <p className={styles.muted}>
              Keep the text of calls and chats, never raw audio. Expiry removes
              the conversation and its summaries; saved facts stay visible until
              you delete them.
            </p>
            {!privacy.history_enabled && (
              <p className={styles.muted}>
                History is off. Choose how long to keep it before enabling it.
              </p>
            )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                savePrivacy();
              }}
              className={styles.retention}
            >
              <label>
                Auto-delete conversations after
                <select
                  aria-label="Auto-delete conversations after"
                  value={retention}
                  disabled={controlsDisabled}
                  onChange={(e) => {
                    setRetention(e.target.value);
                    setDisclosure(false);
                  }}
                >
                  <option value="">History off — choose a duration</option>
                  <option value="30">30 days</option>
                  <option value="90">90 days</option>
                  <option value="never">Never</option>
                </select>
              </label>
              {!privacy.history_enabled && retention !== "" && (
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    checked={disclosure}
                    disabled={controlsDisabled}
                    onChange={(e) => setDisclosure(e.target.checked)}
                  />
                  <span>
                    I understand NavoX will save call and chat text, summaries
                    and learned facts. I can view, correct and delete them here.
                  </span>
                </label>
              )}
              <button
                type="submit"
                disabled={
                  controlsDisabled ||
                  (!privacy.history_enabled && retention !== "" && !disclosure)
                }
              >
                Save history choice
              </button>
            </form>
            <p className={styles.muted}>
              Changing memory controls ends your current Vox call so the new
              choice applies immediately.
            </p>
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={() => void searchHistory()}
            >
              View and search history
            </button>
            {historyOpen && (
              <div className={styles.history}>
                <p className={styles.muted}>
                  Search checks your 100 most recent conversations.
                </p>
                <form
                  className={styles.search}
                  onSubmit={(e) => void searchHistory(e)}
                >
                  <label>
                    Search conversations
                    <input
                      type="search"
                      value={query}
                      maxLength={200}
                      onChange={(e) => setQuery(e.target.value)}
                      disabled={controlsDisabled}
                    />
                  </label>
                  <button type="submit" disabled={controlsDisabled}>
                    Search history
                  </button>
                </form>
                {history === null ? (
                  <p role="status">
                    {error
                      ? "History is unavailable. Use Retry above."
                      : "Loading conversations…"}
                  </p>
                ) : !history.length ? (
                  <p>No conversations found.</p>
                ) : (
                  <ul className={styles.historyList}>
                    {history.map((row) => (
                      <li key={row.id}>
                        <button
                          type="button"
                          onClick={() => void openConversation(row)}
                          disabled={controlsDisabled}
                        >
                          <strong>
                            {row.title || "Untitled conversation"}
                          </strong>
                          <small>
                            {memorySourceLabel(row.source_kind)} ·{" "}
                            {localDate(row.created_at, timezone)} ·{" "}
                            {row.message_count} messages
                          </small>
                        </button>
                        <button
                          type="button"
                          className={styles.deleteLink}
                          disabled={controlsDisabled}
                          onClick={() => setForget(row)}
                        >
                          Forget
                          <span className={styles.srOnly}> {row.title}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {conversationLoading && (
                  <p role="status">Opening the conversation…</p>
                )}
                {conversation && (
                  <section
                    className={styles.conversation}
                    aria-label="Saved conversation"
                  >
                    <div className={styles.heading}>
                      <h3>{conversation.title || "Untitled conversation"}</h3>
                      <button
                        type="button"
                        onClick={() => setConversation(null)}
                      >
                        Close conversation
                      </button>
                    </div>
                    {conversation.summary && (
                      <div className={styles.summary}>
                        <strong>Conversation summary</strong>
                        <p className={styles.prewrap}>{conversation.summary}</p>
                      </div>
                    )}
                    <ol>
                      {conversation.turns.map((turn, index) => (
                        // biome-ignore lint/suspicious/noArrayIndexKey: a loaded conversation is an immutable ordered snapshot without turn IDs.
                        <li key={`${turn.created_at}:${index}`}>
                          <strong>
                            {turn.role === "user" ? "You" : "NavoX"}
                          </strong>
                          <p className={styles.prewrap}>{turn.content}</p>
                        </li>
                      ))}
                    </ol>
                  </section>
                )}
              </div>
            )}
          </div>
          <p className={styles.sensitiveNote}>
            Health, family and mental-health details need your separate
            agreement before they are saved as facts. NavoX never shares your
            memory with your school, professors or advertisers.
          </p>
          {forget && (
            <div
              role="alertdialog"
              aria-modal="false"
              aria-labelledby={`${id}-forget`}
              className={styles.confirm}
            >
              <h3 id={`${id}-forget`} tabIndex={-1}>
                Forget “{"label" in forget ? forget.label : forget.title}”?
              </h3>
              <p>
                {"label" in forget
                  ? forget.source_conversation_id
                    ? "This deletes the fact, its source conversation and summary, and every other fact learned from that conversation. You can’t undo this."
                    : "This deletes the saved fact. You can’t undo this."
                  : "This deletes the conversation, its summary and every fact learned from it. You can’t undo this."}
              </p>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.danger}
                  disabled={controlsDisabled}
                  onClick={() =>
                    void change(
                      () =>
                        request(
                          accountKey,
                          `/${"label" in forget ? "facts" : "history"}/${encodeURIComponent(forget.id)}`,
                          noContent,
                          "DELETE",
                        ),
                      "Forgotten.",
                      true,
                    )
                  }
                >
                  Yes, forget it
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setForget(null)}
                >
                  Keep it
                </button>
              </div>
            </div>
          )}
          <div className={styles.dangerZone}>
            <h3>Delete everything</h3>
            <p>
              Remove all saved conversations, summaries, learned facts and
              writing preferences. History will turn off. Your classes, tasks
              and connected apps stay in place.
            </p>
            {!deleteAll ? (
              <button
                type="button"
                className={styles.danger}
                disabled={controlsDisabled}
                onClick={() => {
                  setDeleteAll(true);
                  setDeletePhrase("");
                }}
              >
                Delete all memory
              </button>
            ) : (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void change(
                    () =>
                      request(accountKey, "/all", noContent, "DELETE", {
                        generation: privacy.generation,
                        confirmation: "DELETE EVERYTHING",
                      }),
                    "All memory deleted. Conversation history is off.",
                    true,
                  );
                }}
              >
                <label>
                  Type DELETE EVERYTHING to confirm
                  <input
                    aria-label="Type DELETE EVERYTHING to confirm"
                    autoComplete="off"
                    value={deletePhrase}
                    onChange={(e) => setDeletePhrase(e.target.value)}
                    disabled={controlsDisabled}
                  />
                </label>
                <p>
                  This permanently deletes your memory and ends your current Vox
                  call.
                </p>
                <div className={styles.actions}>
                  <button
                    type="submit"
                    className={styles.danger}
                    disabled={
                      controlsDisabled || deletePhrase !== "DELETE EVERYTHING"
                    }
                  >
                    Permanently delete everything
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setDeleteAll(false)}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}
