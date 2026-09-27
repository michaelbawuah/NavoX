"use client";

import { type FormEvent, useState } from "react";
import {
  type AssistanceAnswer,
  type AssistanceDomain,
  requestAssistance,
} from "../lib/operational-assistance";
import styles from "./today-workspace.module.css";

const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
type Item = { id: string; title: string; type: string };

export function OperationalAssistance({
  items,
  paused,
}: {
  items: Item[];
  paused: boolean;
}) {
  const [domain, setDomain] = useState<AssistanceDomain>("assistant");
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState(
    "What is the saved status of these tasks?",
  );
  const [session, setSession] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AssistanceAnswer | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const visible = [...new Map(items.map((item) => [item.id, item])).values()];
  const validSelection = selected.filter((id) =>
    visible.some((item) => item.id === id),
  );

  function chooseDomain(value: AssistanceDomain) {
    setDomain(value);
    setAnswer(null);
    setQuestion(
      {
        assistant: "What is the saved status of these tasks?",
        planning: "Prepare the selected task for my review.",
        meeting_preparation: "Prepare my next upcoming selected meeting.",
        ranking: "Rank these tasks by earliest due date; unknown dates last.",
      }[value],
    );
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      paused ||
      busy ||
      validSelection.length === 0 ||
      validSelection.length > 12
    )
      return;
    setBusy(true);
    setAnswer(null);
    setMessage("");
    try {
      setAnswer(
        await requestAssistance(
          api,
          {
            domain,
            itemIds: validSelection,
            instructions: question,
            sessionId: session,
          },
          setSession,
        ),
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Suggestions could not be loaded.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.controlCard}>
      <h2>Explore your tasks</h2>
      <p>
        Choose saved tasks to ask about, prepare for, or compare. Suggestions
        are for your review.
      </p>
      <form className={styles.approvalForm} onSubmit={ask}>
        <fieldset
          className={styles.assistanceSelection}
          disabled={busy || paused}
        >
          <legend>Choose up to 12 tasks</legend>
          {visible.slice(0, 40).map((item) => (
            <label key={item.id}>
              <input
                type="checkbox"
                checked={validSelection.includes(item.id)}
                disabled={
                  !validSelection.includes(item.id) &&
                  validSelection.length >= 12
                }
                onChange={(event) => {
                  setAnswer(null);
                  setSelected(
                    event.target.checked
                      ? [...validSelection, item.id]
                      : validSelection.filter((id) => id !== item.id),
                  );
                }}
              />
              {item.title}
            </label>
          ))}
          {visible.length === 0 && <p>No saved tasks are available yet.</p>}
        </fieldset>
        <label>
          Help with
          <select
            value={domain}
            disabled={busy || paused}
            onChange={(event) =>
              chooseDomain(event.target.value as AssistanceDomain)
            }
          >
            <option value="assistant">A question about saved tasks</option>
            <option value="planning">Preparing next steps</option>
            <option value="meeting_preparation">Meeting preparation</option>
            <option value="ranking">Comparing priorities</option>
          </select>
        </label>
        <label>
          Your request
          <textarea
            required
            maxLength={2000}
            value={question}
            disabled={busy || paused}
            onChange={(event) => {
              setQuestion(event.target.value);
              setAnswer(null);
            }}
          />
        </label>
        <button
          type="submit"
          disabled={
            busy || paused || validSelection.length === 0 || !question.trim()
          }
        >
          {busy ? "Preparing suggestions…" : "Get suggestions"}
        </button>
      </form>
      {message && <p role="status">{message}</p>}
      {answer && !paused && selected.length === validSelection.length && (
        <div aria-live="polite">
          <ul>
            {[...new Set(answer.details)].map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
