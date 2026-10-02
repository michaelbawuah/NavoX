"use client";

import { draftSpeechFor } from "@navox/assistant-runtime/voice";
import type {
  AssistantEmailAction,
  AssistantEmailDraft,
  AssistantTurnView,
} from "@navox/contracts";
import { useEffect, useState } from "react";
import {
  approveAssistantEmailDraft,
  createAssistantEmailDraft,
  loadAssistantEmailAction,
  loadAssistantEmailDraft,
  prepareAssistantEmailDraft,
  reviseAssistantEmailDraft,
  synthesizeAssistantEmailDraftSpeech,
} from "../lib/assistant-client";
import styles from "./navox-assistant.module.css";

function errorText(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The email action could not finish. Please retry.";
}

export interface DraftSpeechRequest {
  /** Stable identity of the exact saved draft version being read. */
  key: string;
  synthesize: (signal: AbortSignal) => Promise<Uint8Array<ArrayBuffer>>;
  onEnd: () => void;
  onError: (reason: string) => void;
}

/** Identity of one saved draft version, so only its own control shows "Stop". */
export function draftSpeechKey(
  draft: AssistantEmailDraft,
  version: number,
): string {
  return `${draft.id}:${version}`;
}

/**
 * The draft control's speaking state follows the voice controller: a global
 * Stop, Mute, barge-in or finished read leaves nothing to claim as speaking.
 */
export function activeDraftSpeechKey(
  selected: string | null,
  voiceSpeaking: boolean,
): string | null {
  return voiceSpeaking ? selected : null;
}

/**
 * The explicit Read draft aloud control. It never starts by itself, never
 * approves a send, and offers an honest note instead of audio when the current
 * saved version is longer than the spoken bound.
 */
export function DraftSpeechControl({
  edited,
  speakable,
  busy,
  speaking,
  onStart,
  onStop,
}: {
  edited: boolean;
  speakable: boolean;
  busy: boolean;
  speaking: boolean;
  onStart: () => void;
  onStop: () => void;
}) {
  if (edited) return null;
  return (
    <>
      <button
        type="button"
        disabled={busy || (speaking ? false : !speakable)}
        aria-label="Read this saved draft aloud"
        onClick={() => (speaking ? onStop() : onStart())}
      >
        {speaking ? "Stop reading" : "Read draft aloud"}
      </button>
      {!speakable && (
        <p className={styles.itemMeta}>
          This draft is longer than the spoken limit, so only the full text
          below is available. Reading it never sends it.
        </p>
      )}
    </>
  );
}

export function AssistantEmailActions({
  sessionId,
  turn,
  speakDraft,
  stopDraftSpeech,
  speakingKey,
}: {
  sessionId: string;
  turn: AssistantTurnView;
  /** Starts explicit playback of the reviewed draft version, when available. */
  speakDraft?: (request: DraftSpeechRequest) => void;
  /** Cancels any draft playback on edit, stop or unmount. */
  stopDraftSpeech?: () => void;
  /** The draft version the voice controller is currently speaking, if any. */
  speakingKey?: string | null;
}) {
  const [instructions, setInstructions] = useState(
    "Reply to this email clearly and concisely.",
  );
  const [draft, setDraft] = useState<AssistantEmailDraft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [action, setAction] = useState<AssistantEmailAction | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recovering, setRecovering] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const pointerKey = `navox.assistant.email-draft.v1:${sessionId}:${turn.id}`;
  useEffect(() => {
    let active = true;
    let saved: string | null = null;
    try {
      saved = window.sessionStorage.getItem(pointerKey);
    } catch {
      /* Storage may be disabled. */
    }
    if (!saved) {
      setRecovering(false);
      return;
    }
    void (async () => {
      try {
        const restored = await loadAssistantEmailDraft(
          sessionId,
          turn.id,
          saved,
        );
        if (!active) return;
        setDraft(restored);
        setSubject(restored.versions.at(-1)?.subject ?? "");
        setBody(restored.versions.at(-1)?.body ?? "");
        if (restored.action_id)
          setAction(
            await loadAssistantEmailAction(sessionId, turn.id, restored.id),
          );
      } catch (failure) {
        if (active) setError(errorText(failure));
      } finally {
        if (active) setRecovering(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [pointerKey, sessionId, turn.id]);

  const current = draft?.versions.at(-1);
  const edited = current
    ? subject !== current.subject || body !== current.body
    : false;

  const speakingDraftKey =
    draft && current ? draftSpeechKey(draft, current.version) : null;
  const speakingDraft =
    speakingDraftKey !== null && speakingKey === speakingDraftKey;

  // Unsaved edits change the exact content; never keep speaking the saved
  // version the operator has moved past.
  useEffect(() => {
    if (!edited || !speakingDraft) return;
    stopDraftSpeech?.();
  }, [edited, speakingDraft, stopDraftSpeech]);

  useEffect(
    () => () => {
      stopDraftSpeech?.();
    },
    [stopDraftSpeech],
  );

  const emailItems =
    turn.presentation?.blocks.filter(
      (block) => block.kind === "ITEM" && block.item.type === "EMAIL",
    ) ?? [];
  if (
    turn.state !== "READY" ||
    turn.decision?.capability_id !== "email.search" ||
    turn.decision.reason !== "email.search.found_one" ||
    emailItems.length !== 1
  )
    return null;

  async function run(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  const canApprove =
    action?.status === "awaiting_approval" &&
    action.approval?.status === "pending";
  const draftSpeech = current
    ? draftSpeechFor({ subject: current.subject, body: current.body })
    : null;
  const canReadDraft =
    current !== undefined &&
    !edited &&
    draftSpeech?.speakable === true &&
    speakDraft !== undefined;

  return (
    <section className={styles.emailAction} aria-label="Email reply action">
      {!draft && (
        <>
          <label htmlFor={`instructions-${turn.id}`}>Reply instructions</label>
          <textarea
            id={`instructions-${turn.id}`}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            maxLength={4000}
            disabled={busy || recovering}
          />
          <button
            type="button"
            disabled={busy || recovering || !instructions.trim()}
            onClick={() =>
              void run(async () => {
                const created = await createAssistantEmailDraft(
                  sessionId,
                  turn.id,
                  instructions,
                );
                try {
                  window.sessionStorage.setItem(pointerKey, created.id);
                } catch {
                  /* Storage may be disabled. */
                }
                setDraft(created);
                setSubject(created.versions.at(-1)?.subject ?? "");
                setBody(created.versions.at(-1)?.body ?? "");
              })
            }
          >
            Draft reply
          </button>
        </>
      )}
      {draft && current && (
        <>
          <p>To: {current.to[0]} · Saved draft</p>
          <label htmlFor={`subject-${draft.id}`}>Subject</label>
          <input
            id={`subject-${draft.id}`}
            value={subject}
            onChange={(event) => {
              setSubject(event.target.value);
              setAction(null);
              setConfirmed(false);
            }}
            maxLength={256}
            disabled={busy}
          />
          <label htmlFor={`body-${draft.id}`}>Message</label>
          <textarea
            id={`body-${draft.id}`}
            value={body}
            onChange={(event) => {
              setBody(event.target.value);
              setAction(null);
              setConfirmed(false);
            }}
            maxLength={50000}
            disabled={busy}
          />
          {edited && (
            <button
              type="button"
              disabled={busy || !subject.trim() || !body.trim()}
              onClick={() =>
                void run(async () => {
                  const revised = await reviseAssistantEmailDraft(
                    sessionId,
                    turn.id,
                    draft,
                    subject,
                    body,
                  );
                  setDraft(revised);
                  setSubject(revised.versions.at(-1)?.subject ?? "");
                  setBody(revised.versions.at(-1)?.body ?? "");
                  setAction(null);
                  setConfirmed(false);
                })
              }
            >
              Save changes
            </button>
          )}
          {!edited && (
            <DraftSpeechControl
              edited={edited}
              speakable={draftSpeech?.speakable === true && canReadDraft}
              busy={busy}
              speaking={speakingDraft}
              onStart={() => {
                if (!current || !draftSpeech?.speakable || !speakDraft) return;
                const version = current.version;
                setError(null);
                speakDraft({
                  key: draftSpeechKey(draft, version),
                  synthesize: (signal) =>
                    synthesizeAssistantEmailDraftSpeech(
                      sessionId,
                      turn.id,
                      draft.id,
                      version,
                      signal,
                    ),
                  onEnd: () => {},
                  onError: (reason) => setError(reason),
                });
              }}
              onStop={() => stopDraftSpeech?.()}
            />
          )}
          {!action && !edited && (
            <button
              type="button"
              disabled={busy || recovering}
              onClick={() =>
                void run(async () => {
                  setAction(
                    draft.action_id
                      ? await loadAssistantEmailAction(
                          sessionId,
                          turn.id,
                          draft.id,
                        )
                      : await prepareAssistantEmailDraft(
                          sessionId,
                          turn.id,
                          draft,
                        ),
                  );
                  setConfirmed(false);
                })
              }
            >
              {draft.action_id
                ? "Resume exact send review"
                : "Review exact send"}
            </button>
          )}
        </>
      )}
      {action && !edited && (
        <div className={styles.emailReview}>
          <p>Send status: {action.status}</p>
          <p>From: {action.payload.sender}</p>
          <p>To: {action.payload.to}</p>
          <p>Subject: {action.payload.subject}</p>
          <p className={styles.emailBody}>{action.payload.body_text}</p>
          {canApprove && (
            <>
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                  disabled={busy}
                />{" "}
                I approve sending this exact email now.
              </label>
              <button
                type="button"
                disabled={busy || !confirmed}
                onClick={() =>
                  void run(async () => {
                    setConfirmed(false);
                    setAction(
                      await approveAssistantEmailDraft(
                        sessionId,
                        turn.id,
                        draft?.id ?? "",
                        action,
                      ),
                    );
                  })
                }
              >
                Approve and send
              </button>
            </>
          )}
          {action.status !== "completed" && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setAction(
                    await loadAssistantEmailAction(
                      sessionId,
                      turn.id,
                      draft?.id ?? "",
                    ),
                  );
                })
              }
            >
              Refresh send status
            </button>
          )}
          {action.status === "completed" && action.result.message_id && (
            <p>Sent and confirmed by Gmail.</p>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </section>
  );
}
