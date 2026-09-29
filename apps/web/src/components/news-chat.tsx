"use client";

import type { NewsAvailability, NewsVerification } from "@navox/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { newsRequest, newsTime, safeNewsUrl } from "../lib/news";
import { NavoXStatus } from "./navox-ui";
import styles from "./news-chat.module.css";

interface NewsAnswer {
  id: string;
  question: string;
  status: "PROCESSING" | "READY" | "UNAVAILABLE" | "SOURCES_CHANGED";
  message: string;
  as_of: string;
  facts: {
    text: string;
    source_name: string;
    source_url: string;
    source_id: string;
    status: NewsVerification;
  }[];
}

export function NewsChat({ storyId }: { storyId?: string }) {
  const [available, setAvailable] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<NewsAnswer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
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
        <article key={answer.id} className={styles.answer}>
          <h3>{answer.question}</h3>
          <small>Sources checked {newsTime(answer.as_of)}</small>
          <p>{answer.message}</p>
          {answer.facts.map((fact) => {
            const url = safeNewsUrl(fact.source_url);
            return (
              <div
                className={styles.fact}
                key={`${fact.source_id}-${fact.status}-${fact.text}`}
              >
                <blockquote>{fact.text}</blockquote>
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
            );
          })}
        </article>
      ))}
      {error && <p role="alert">{error}</p>}
      <form onSubmit={ask}>
        <label htmlFor="news-question">
          {storyId ? "Ask about this story" : "Ask a news question"}
        </label>
        <div>
          <input
            id="news-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={2000}
            placeholder="What changed, and what is still unclear?"
            required
            disabled={busy}
          />
          <button type="submit" disabled={busy || !question.trim()}>
            Ask NavoX
          </button>
        </div>
      </form>
      <p role="status" className={styles.progress}>
        {busy ? "Checking the sources…" : ""}
      </p>
    </section>
  );
}
