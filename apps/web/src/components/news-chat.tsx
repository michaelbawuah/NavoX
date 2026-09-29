"use client";

import type {
  NewsAnswer,
  NewsAvailability,
  NewsFreshness,
} from "@navox/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { newsRequest, newsTime, safeNewsUrl } from "../lib/news";
import { NavoXAskBox, NavoXStatus } from "./navox-ui";
import styles from "./news-chat.module.css";

const freshnessLabels: Record<NewsFreshness, string> = {
  REALTIME: "Checked for very recent reports",
  FRESH: "Fresh sources",
  RECENT: "Recent sources",
  HISTORICAL: "Historical context",
};

export function NewsAnswerCard({
  answer,
  canReference,
  onReference,
}: {
  answer: NewsAnswer;
  canReference: boolean;
  onReference: (position: number) => void;
}) {
  return (
    <article className={styles.answer}>
      <h3>{answer.question}</h3>
      <small>
        Requested {newsTime(answer.as_of)}
        {answer.status === "READY" && ` · ${freshnessLabels[answer.freshness]}`}
      </small>
      <p>{answer.message}</p>
      {answer.retrieval_limited && answer.status === "READY" && (
        <p className={styles.scopeNote}>
          This answer uses a bounded set of currently connected sources.
        </p>
      )}
      {answer.status === "READY" && answer.facts.length > 0 && (
        <ol className={styles.facts} aria-label="Source-backed answer facts">
          {answer.facts.map((fact, index) => {
            const url = safeNewsUrl(fact.source_url);
            const position = index + 1;
            return (
              <li
                className={styles.fact}
                key={`${fact.item_id}-${fact.status}-${fact.text}`}
              >
                <blockquote>{fact.text}</blockquote>
                <div className={styles.factMeta}>
                  <NavoXStatus status={fact.status} />
                  {url ? (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      referrerPolicy="no-referrer"
                    >
                      {fact.source_name} ↗
                    </a>
                  ) : (
                    <span>{fact.source_name}</span>
                  )}
                </div>
                {canReference && answer.status === "READY" && (
                  <button
                    type="button"
                    className={styles.referenceButton}
                    onClick={() => onReference(position)}
                    aria-label={`Ask a follow-up about item ${position}`}
                  >
                    Ask about #{position}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </article>
  );
}

export function referenceableTurnId(answers: NewsAnswer[]): string | undefined {
  const latest = answers[answers.length - 1];
  return latest?.status === "READY" ? latest.id : undefined;
}

export function NewsChat({ storyId }: { storyId?: string }) {
  return <NewsChatSession key={storyId ?? "global"} storyId={storyId} />;
}

function NewsChatSession({ storyId }: { storyId?: string }) {
  const [available, setAvailable] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<NewsAnswer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  const questionInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void newsRequest<NewsAvailability>("/availability", {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setAvailable(value.chat);
      })
      .catch(() => {});
    return () => {
      controller.abort();
      pending.current?.abort();
    };
  }, []);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    const controller = new AbortController();
    pending.current = controller;
    const signal = controller.signal;
    setBusy(true);
    setError("");
    try {
      let id = conversationId;
      if (!id) {
        const created = await newsRequest<{ id: string }>("/conversations", {
          signal,
          method: "POST",
          body: JSON.stringify({ story_id: storyId ?? null }),
        });
        id = created.id;
        if (!signal.aborted) setConversationId(id);
      }
      const message = await newsRequest<{ id: string }>(
        `/conversations/${id}/messages`,
        {
          signal,
          method: "POST",
          body: JSON.stringify({
            request_id: crypto.randomUUID(),
            question: text,
            intent: storyId ? "STORY_QUESTION" : "CURRENT_NEWS",
            freshness: "FRESH",
            depth: "STANDARD",
          }),
        },
      );
      if (!signal.aborted) setQuestion("");
      for (let attempt = 0; attempt < 60 && !signal.aborted; attempt += 1) {
        const turns = await newsRequest<NewsAnswer[]>(`/conversations/${id}`, {
          signal,
        });
        if (signal.aborted) return;
        setAnswers(turns);
        if (
          turns.some(
            (turn) => turn.id === message.id && turn.status !== "PROCESSING",
          )
        )
          return;
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
      if (!signal.aborted)
        setError(
          "This answer is taking longer than expected. Please check back shortly.",
        );
    } catch (reason) {
      if (!signal.aborted)
        setError(
          reason instanceof Error
            ? reason.message
            : "News conversations are temporarily unavailable.",
        );
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }

  const referenceableAnswerId = referenceableTurnId(answers);

  function reference(position: number) {
    setQuestion(`Tell me more about #${position}`);
    window.requestAnimationFrame(() => questionInput.current?.focus());
  }

  if (!available) return null;
  return (
    <section className={styles.chat} aria-label="Ask NavoX about the news">
      <h2>Ask NavoX</h2>
      <p>
        {storyId
          ? "Go deeper into this story."
          : "Ask about the news. Follow a question wherever it leads."}
      </p>
      {answers.map((answer) => (
        <NewsAnswerCard
          key={answer.id}
          answer={answer}
          canReference={!busy && answer.id === referenceableAnswerId}
          onReference={reference}
        />
      ))}
      {error && <p role="alert">{error}</p>}
      <NavoXAskBox
        id="news-question"
        label={storyId ? "Ask about this story" : "Ask a news question"}
        value={question}
        placeholder="What changed, and what is still unclear?"
        busy={busy}
        maxLength={2000}
        buttonLabel="Ask NavoX"
        inputRef={questionInput}
        onChange={setQuestion}
        onSubmit={ask}
        hint="Answers are grounded in the currently available NavoX news sources."
      />
      <p role="status" className={styles.progress}>
        {busy ? "Checking the sources…" : ""}
      </p>
    </section>
  );
}
