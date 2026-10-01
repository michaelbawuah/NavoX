"use client";

import type {
  AnswerCitation,
  AskResponse,
  AskSessionSummary,
  AskSessionView,
  AskTurnView,
} from "@navox/contracts";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  AskRequestError,
  askNotice,
  citationLabel,
  clearAskHistory,
  coverageNotes,
  deleteAskSession,
  followupReferent,
  isAnswered,
  loadAskSession,
  loadAskSessions,
  runAsk,
  SubmitLedger,
} from "../lib/knowledge-ask";
import {
  RequestGate,
  resourceTypeLabel,
  safeSourceUrl,
  searchTime,
} from "../lib/search";
import styles from "./knowledge-ask.module.css";
import { NavoXEmptyState, NavoXErrorState, NavoXSkeleton } from "./navox-ui";

export function AskCitations({ citations }: { citations: AnswerCitation[] }) {
  return (
    <ol className={styles.citations}>
      {citations.map((citation) => {
        const url = safeSourceUrl(citation.canonical_url);
        return (
          <li
            key={`${citation.resource_id}-${citation.excerpt_index ?? citation.fact_id}`}
          >
            <p className={styles.quote}>
              {citation.excerpt_text ?? citation.fact_value}
            </p>
            <p className={styles.meta}>
              <span>{citationLabel(citation)}</span>
              <span aria-hidden="true">·</span>
              <span>{citation.title ?? "Untitled record"}</span>
              <span aria-hidden="true">·</span>
              <span>{resourceTypeLabel(citation.source_type)}</span>
              {citation.source_updated_at && (
                <>
                  <span aria-hidden="true">·</span>
                  <span>Updated {searchTime(citation.source_updated_at)}</span>
                </>
              )}
            </p>
            {url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                referrerPolicy="no-referrer"
              >
                Open at source
              </a>
            ) : (
              <span className={styles.muted}>No source link available</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function TurnAnswer({ turn }: { turn: AskTurnView }) {
  const notice = askNotice({
    answer_state: turn.answer_state,
  } as AskResponse);
  return (
    <div className={styles.turn}>
      <p className={styles.question}>
        <span className={styles.sequence}>Turn {turn.sequence}</span>
        {turn.question}
      </p>
      {notice && <p className={styles.notice}>{notice}</p>}
      {turn.citations.map((citation) => (
        <blockquote
          key={`${citation.resource_id}-${citation.excerpt_index ?? citation.fact_id}`}
        >
          <cite>
            {citationLabel(citation)} · {citation.title ?? "Untitled record"}
          </cite>
          <p>{citation.excerpt_text ?? citation.fact_value}</p>
        </blockquote>
      ))}
    </div>
  );
}

export function KnowledgeAsk({
  initialQuestion = "",
}: {
  initialQuestion?: string;
}) {
  const [question, setQuestion] = useState(initialQuestion);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [referent, setReferent] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [sessions, setSessions] = useState<AskSessionSummary[]>([]);
  const [openSession, setOpenSession] = useState<AskSessionView | null>(null);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const gate = useRef(new RequestGate());
  const sessionGate = useRef(new RequestGate());
  // One paid identifier per (question, session, referent) until it resolves, so a
  // timeout retry reuses the reservation instead of buying a second answer.
  const pending = useRef(new SubmitLedger());

  const refreshSessions = useCallback(async () => {
    const token = sessionGate.current.next();
    try {
      const rows = await loadAskSessions();
      if (!sessionGate.current.isCurrent(token)) return;
      setSessions(rows);
      setLoadingSessions(false);
    } catch (failure) {
      if (!sessionGate.current.isCurrent(token)) return;
      setLoadingSessions(false);
      if (failure instanceof AskRequestError && failure.status === 401) {
        setError(failure.message);
      }
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
    return () => {
      gate.current.invalidate();
      sessionGate.current.invalidate();
    };
  }, [refreshSessions]);

  const submit = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      const value = question.trim();
      if (!value) return;
      const token = gate.current.next();
      setBusy(true);
      setError(null);
      setAnswer(null);
      const key = JSON.stringify({ question: value, sessionId, referent });
      const requestId = pending.current.requestIdFor(key);
      try {
        const result = await runAsk({
          question: value,
          request_id: requestId,
          session_id: sessionId,
          referent,
        });
        if (!gate.current.isCurrent(token)) return;
        pending.current.resolve();
        setAnswer(result);
        setSessionId(result.session_id);
        setReferent(null);
        await refreshSessions();
        if (!gate.current.isCurrent(token)) return;
        if (openSession) {
          const refreshed = await loadAskSession(result.session_id);
          if (!gate.current.isCurrent(token)) return;
          setOpenSession(refreshed);
        }
      } catch (failure) {
        if (!gate.current.isCurrent(token)) return;
        setAnswer(null);
        if (failure instanceof AskRequestError) {
          setError(failure.message);
        } else {
          setError("We couldn’t send that question. Please try again.");
        }
      } finally {
        if (gate.current.isCurrent(token)) setBusy(false);
      }
    },
    [openSession, question, referent, refreshSessions, sessionId],
  );

  const startFollowup = useCallback(async (turn: AskTurnView) => {
    gate.current.invalidate();
    pending.current.forget();
    setSessionId(turn.session_id);
    setReferent(null);
    setBusy(false);
    setQuestion("");
    setAnswer(null);
    const token = sessionGate.current.next();
    try {
      const view = await loadAskSession(turn.session_id);
      if (!sessionGate.current.isCurrent(token)) return;
      setOpenSession(view);
    } catch {
      if (sessionGate.current.isCurrent(token)) {
        setError("We couldn’t open that session. Please try again.");
      }
    }
    input.current?.focus();
  }, []);

  const open = useCallback(async (id: string) => {
    gate.current.invalidate();
    pending.current.forget();
    setAnswer(null);
    setReferent(null);
    setBusy(false);
    const token = sessionGate.current.next();
    try {
      const view = await loadAskSession(id);
      if (!sessionGate.current.isCurrent(token)) return;
      setOpenSession(view);
      setSessionId(view.id);
    } catch {
      if (sessionGate.current.isCurrent(token)) {
        setError("We couldn’t open that session. Please try again.");
      }
    }
  }, []);

  const remove = useCallback(
    async (id: string) => {
      const token = gate.current.next();
      sessionGate.current.invalidate();
      setBusy(false);
      pending.current.forget();
      if (sessionId === id) {
        setSessionId(null);
        setAnswer(null);
        setOpenSession(null);
        setReferent(null);
      }
      try {
        await deleteAskSession(id);
        if (!gate.current.isCurrent(token)) return;
        await refreshSessions();
      } catch {
        if (!gate.current.isCurrent(token)) return;
        setError("We couldn’t delete that session. Please try again.");
      }
    },
    [refreshSessions, sessionId],
  );

  const clearAll = useCallback(async () => {
    const token = gate.current.next();
    setBusy(false);
    setLoadingSessions(false);
    sessionGate.current.invalidate();
    pending.current.forget();
    // Erase local content first so a late response cannot restore it.
    setSessions([]);
    setOpenSession(null);
    setAnswer(null);
    setSessionId(null);
    setReferent(null);
    try {
      await clearAskHistory();
    } catch {
      if (!gate.current.isCurrent(token)) return;
      setError("We couldn’t clear your answer history. Please try again.");
    }
  }, []);

  const notice = answer ? askNotice(answer) : null;
  const notes = answer ? coverageNotes(answer) : [];
  const answered = answer ? isAnswered(answer) : false;

  return (
    <section className={styles.ask} aria-label="Grounded answers">
      <form className={styles.form} onSubmit={submit} aria-busy={busy}>
        <label className={styles.field} htmlFor="ask-question">
          <span>Ask about your connected sources</span>
          <input
            ref={input}
            id="ask-question"
            name="question"
            value={question}
            maxLength={2000}
            placeholder="What did finance decide about the quarterly budget?"
            onChange={(event) => {
              pending.current.forget();
              setQuestion(event.target.value);
            }}
            required
            autoComplete="off"
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>
      {referent && (
        <p className={styles.referent}>
          Following up on {referent}
          <button
            type="button"
            className={styles.quiet}
            onClick={() => setReferent(null)}
          >
            Clear follow-up
          </button>
        </p>
      )}
      {error && <NavoXErrorState message={error} />}
      {busy && <NavoXSkeleton label="Reading your sources…" />}
      {answer && (
        <article className={styles.answer} aria-live="polite">
          {notice && <p className={styles.notice}>{notice}</p>}
          {answered && <AskCitations citations={answer.citations} />}
          {answer.results.length > 0 && (
            <div className={styles.results}>
              <h3>
                {answered
                  ? "Sources in this answer’s search"
                  : "What search found instead"}
              </h3>
              <ul>
                {answer.results.map((resource, index) => {
                  const position = Number(
                    resource.provenance.display_number ?? index + 1,
                  );
                  const url = safeSourceUrl(resource.canonical_url);
                  return (
                    <li key={`${resource.source_type}-${resource.resource_id}`}>
                      <span>
                        #{position} · {resource.title ?? "Untitled record"}
                      </span>
                      <button
                        type="button"
                        className={styles.quiet}
                        onClick={() => {
                          gate.current.invalidate();
                          setBusy(false);
                          pending.current.forget();
                          setSessionId(answer.session_id);
                          setReferent(followupReferent(position));
                          setQuestion("");
                          input.current?.focus();
                        }}
                      >
                        Ask about source #{position}
                      </button>
                      {url && (
                        <a
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          referrerPolicy="no-referrer"
                        >
                          Open
                        </a>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {notes.length > 0 && (
            <ul className={styles.notes}>
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <div className={styles.controls}>
            {answer.suggested_followups.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className={styles.quiet}
                onClick={() => {
                  setQuestion(suggestion);
                  input.current?.focus();
                }}
              >
                {suggestion}
              </button>
            ))}
          </div>
        </article>
      )}
      <section className={styles.sessions} aria-label="Answer sessions">
        <header>
          <h2>Your sessions</h2>
          {sessions.length > 0 && (
            <button type="button" className={styles.quiet} onClick={clearAll}>
              Clear history
            </button>
          )}
        </header>
        {loadingSessions ? (
          <NavoXSkeleton label="Loading sessions…" />
        ) : sessions.length === 0 ? (
          <NavoXEmptyState title="No sessions yet">
            <p>
              Ask a question and it will be kept here, separate from your index.
            </p>
          </NavoXEmptyState>
        ) : (
          <ul className={styles.sessionList}>
            {sessions.map((session) => (
              <li key={session.id}>
                <button type="button" onClick={() => void open(session.id)}>
                  {session.title ?? "Untitled session"}
                </button>
                <span className={styles.muted}>{session.turn_count} turns</span>
                <button
                  type="button"
                  className={styles.quiet}
                  onClick={() => void remove(session.id)}
                  aria-label={`Delete session ${session.title ?? "untitled"}`}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
        {openSession && (
          <div className={styles.openSession}>
            {openSession.turns.map((turn, index) => (
              <article key={turn.id}>
                <TurnAnswer turn={turn} />
                {index === openSession.turns.length - 1 && (
                  <button
                    type="button"
                    className={styles.quiet}
                    onClick={() => void startFollowup(turn)}
                  >
                    Continue this session
                  </button>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
