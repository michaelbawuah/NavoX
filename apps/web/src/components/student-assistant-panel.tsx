"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import {
  initialStudentAssistantState,
  StudentAssistantController,
  type StudentAssistantReply,
} from "../lib/student-agent-client";
import styles from "./student-assistant-panel.module.css";

export function StudentAssistantAnswer({
  reply,
}: {
  reply: StudentAssistantReply;
}) {
  return (
    <div className={styles.answer}>
      <span className={styles.answerLabel}>
        {reply.status === "review"
          ? "Ready for your review"
          : reply.status === "clarify"
            ? "One more detail"
            : reply.status === "unavailable" || reply.status === "limit"
              ? "This question needs another step"
              : "NavoX"}
      </span>
      <p>{reply.answer}</p>
      {reply.links.length > 0 && (
        <div className={styles.links}>
          {reply.links.map((link) => (
            <div key={link.href}>
              <Link href={link.href}>
                {link.label} <span aria-hidden="true">↗</span>
              </Link>
              <span>{link.note}</span>
            </div>
          ))}
        </div>
      )}
      {reply.status === "clarify" && (
        <p className={styles.followup}>
          Write the full question below with that detail. NavoX won’t include
          the previous question.
        </p>
      )}
    </div>
  );
}
function StudentAssistantAccount({
  accountId,
  toolContextConsentAvailable,
}: {
  accountId: string;
  toolContextConsentAvailable: boolean;
}) {
  const id = useId();
  const [state, setState] = useState(initialStudentAssistantState);
  const controller = useRef<StudentAssistantController | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const current = new StudentAssistantController(
      accountId,
      toolContextConsentAvailable,
    );
    controller.current = current;
    setState(current.current());
    const unsubscribe = current.subscribe(setState);
    const hidden = () => {
      if (document.hidden)
        current.clear("Temporary text cleared while this page was hidden.");
      else void current.recheck();
    };
    const blur = () =>
      current.clear("Temporary text cleared when you left this page.");
    const focus = () => {
      void current.recheck();
    };
    const accountChanged = () => {
      current.clear("Account changed. Start a new question after signing in.");
    };
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("blur", blur);
    window.addEventListener("focus", focus);
    window.addEventListener("pageshow", focus);
    window.addEventListener("storage", focus);
    window.addEventListener("navox:account-changed", accountChanged);
    window.addEventListener("navox:signout", accountChanged);
    return () => {
      unsubscribe();
      current.dispose();
      controller.current = null;
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("blur", blur);
      window.removeEventListener("focus", focus);
      window.removeEventListener("pageshow", focus);
      window.removeEventListener("storage", focus);
      window.removeEventListener("navox:account-changed", accountChanged);
      window.removeEventListener("navox:signout", accountChanged);
    };
  }, [accountId, toolContextConsentAvailable]);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void controller.current?.ask();
  }
  const examples = [
    "What class do I have next?",
    "List my tasks",
    "Preview my daily plan",
    "Explain a flip-flop",
  ];
  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <div className={styles.header}>
        <div>
          <span className={styles.eyebrow}>Your student assistant</span>
          <h2 id={`${id}-title`}>What would help right now?</h2>
        </div>
        <span className={styles.private}>Private question</span>
      </div>
      <p className={styles.intro}>
        Find your next class, check your tasks, or work through an idea.
      </p>
      <div className={styles.examples}>
        {examples.map((example) => (
          <button
            key={example}
            type="button"
            disabled={state.busy || state.blocked}
            onClick={() => {
              controller.current?.setQuestion(example);
              field.current?.focus();
            }}
          >
            {example}
          </button>
        ))}
      </div>
      <div aria-live="polite">
        {state.reply && <StudentAssistantAnswer reply={state.reply} />}
      </div>
      <form onSubmit={submit}>
        <label className={styles.label} htmlFor={`${id}-question`}>
          Your question
        </label>
        <textarea
          ref={field}
          id={`${id}-question`}
          maxLength={500}
          rows={3}
          value={state.question}
          disabled={state.busy || state.blocked}
          placeholder="Where is my next class?"
          aria-describedby={`${id}-privacy ${id}-count`}
          onChange={(event) =>
            controller.current?.setQuestion(event.target.value)
          }
        />
        <div className={styles.below}>
          <span id={`${id}-count`}>{state.question.length}/500</span>
          <span>One question at a time</span>
        </div>
        {toolContextConsentAvailable && (
          <label className={styles.consent}>
            <input
              type="checkbox"
              checked={state.toolConsent}
              disabled={state.busy || state.blocked}
              onChange={(event) =>
                controller.current?.setToolConsent(event.target.checked)
              }
            />
            <span>
              Let the configured AI use selected class, course, task and feed
              facts for this question.
              <small>
                Files, saved memory, previous questions and private calendar
                links aren’t included. This option does not enable an
                unqualified model.
              </small>
            </span>
          </label>
        )}
        <div className={styles.actions}>
          <button
            className={styles.ask}
            type="submit"
            disabled={state.busy || state.blocked || !state.question.trim()}
          >
            {state.busy ? "Checking…" : "Ask NavoX"}
            <span aria-hidden="true"> ↗</span>
          </button>
          <button type="button" onClick={() => controller.current?.clear()}>
            {state.busy ? "Cancel and clear" : "Clear"}
          </button>
          {state.blocked && (
            <button
              type="button"
              onClick={() => void controller.current?.recheck()}
            >
              Check sign-in again
            </button>
          )}
        </div>
      </form>
      {state.error && (
        <p className={styles.error} role="alert">
          {state.error}
        </p>
      )}
      <p className={styles.status} role="status">
        {state.notice}
      </p>
      <p className={styles.privacy} id={`${id}-privacy`}>
        This panel does not save a conversation. Each question is sent without
        chat history. Temporary text clears when you leave the page or after ten
        minutes. AI explanations use the configured provider.
      </p>
    </section>
  );
}
export function StudentAssistantPanel({
  accountId,
  toolContextConsentAvailable = false,
}: {
  accountId: string;
  toolContextConsentAvailable?: boolean;
}) {
  return (
    <StudentAssistantAccount
      key={`${accountId}:${toolContextConsentAvailable}`}
      accountId={accountId}
      toolContextConsentAvailable={toolContextConsentAvailable}
    />
  );
}
