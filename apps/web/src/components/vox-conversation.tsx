"use client";
import { MESSAGE_COUNTER_AT, TYPED_MESSAGE_LIMIT } from "@navox/contracts";
import { useEffect, useRef, useState } from "react";
import { productFeatures } from "../lib/product-features";
import type { VoxCallController, VoxCallState } from "../lib/vox-call";
import type { VoxConversationModel } from "../lib/vox-conversation";
import styles from "./vox-conversation.module.css";
import { VoxTextFile } from "./vox-text-file";

export function VoxConversation({
  model,
  state,
  controller,
}: {
  model: VoxConversationModel;
  state: VoxCallState;
  controller: VoxCallController;
}) {
  const input = useRef<HTMLTextAreaElement>(null),
    log = useRef<HTMLDivElement>(null);
  const draft = useRef(model.draft);
  draft.current = model.draft;
  const count = [...model.draft].length;
  const [fileSaved, setFileSaved] = useState(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Measure after the controlled draft changes.
  useEffect(() => {
    if (input.current) {
      input.current.style.height = "auto";
      input.current.style.height = `${Math.min(input.current.scrollHeight, 192)}px`;
    }
  }, [model.draft]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: New conversation entries scroll to the newest text.
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [model.turns, model.pending]);
  function send() {
    if (state.phase === "standby") controller.resume();
    void model.send();
  }
  function saveText() {
    const url = URL.createObjectURL(
      new Blob([model.draft], { type: "text/plain;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "navox-question.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const seen = new Map<string, number>();
  const entries = model.turns.map((turn) => {
    const text = JSON.stringify(turn),
      occurrence = seen.get(text) ?? 0;
    seen.set(text, occurrence + 1);
    return { ...turn, key: text + occurrence };
  });
  return (
    <section className={styles.conversation} aria-label="Vox conversation">
      <div
        ref={log}
        className={styles.messages}
        role="log"
        aria-label="Conversation"
        aria-live="polite"
      >
        {entries.map((turn) => (
          <div key={turn.key} className={styles.turn}>
            {turn.kind === "dialogue" && (
              <p className={styles.question}>
                <span className={styles.sr}>You: </span>
                {turn.question}
              </p>
            )}
            <p className={styles.answer}>
              <span className={styles.sr}>NavoX: </span>
              {turn.kind === "summary" && (
                <small>Earlier in this conversation</small>
              )}
              {turn.answer}
            </p>
          </div>
        ))}
        {model.pending && (
          <p className={styles.question}>
            <span className={styles.sr}>You: </span>
            {model.pending}
            <small>Waiting for NavoX…</small>
          </p>
        )}
        {!model.turns.length && !model.pending && (
          <div className={styles.suggestions}>
            {[
              "What's next?",
              "Plan my day",
              "When am I free?",
              "Show me on the Board",
            ].map((text) => (
              <button
                key={text}
                type="button"
                disabled={!model.available}
                onClick={() => {
                  model.setDraft(text);
                  input.current?.focus();
                }}
              >
                {text}
              </button>
            ))}
          </div>
        )}
      </div>
      <form
        className={styles.composer}
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <label htmlFor="vox-message" className={styles.sr}>
          Message NavoX
        </label>
        <textarea
          id="vox-message"
          ref={input}
          rows={2}
          placeholder="Message NavoX…"
          value={model.draft}
          disabled={!model.available || model.busy}
          onChange={(e) => model.setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className={styles.actions}>
          <button
            type="button"
            aria-label={
              state.microphoneMuted ? "Unmute microphone" : "Mute microphone"
            }
            aria-pressed={state.microphoneMuted}
            onClick={() =>
              controller.setMicrophoneMuted(!state.microphoneMuted)
            }
            disabled={!model.available}
          >
            {state.microphoneMuted ? "Mic off" : "Mic on"}
          </button>
          {count > MESSAGE_COUNTER_AT && (
            <span aria-live="polite">
              {count.toLocaleString()}/{TYPED_MESSAGE_LIMIT.toLocaleString()}
            </span>
          )}
          <button
            type="submit"
            disabled={
              !model.available ||
              !model.draft.trim() ||
              model.busy ||
              !!model.pending ||
              count > TYPED_MESSAGE_LIMIT
            }
          >
            {model.busy ? "Sending…" : "Send"}
          </button>
        </div>
        {fileSaved && (
          <p role="status">Your file was saved in class materials.</p>
        )}
        {count > TYPED_MESSAGE_LIMIT &&
          productFeatures.studentCoursework &&
          state.owner && (
            <VoxTextFile
              text={model.draft}
              accountKey={`${state.owner.workspaceId}:${state.owner.userId}`}
              onSaved={(savedText) => {
                if (draft.current === savedText) model.setDraft("");
                setFileSaved(true);
              }}
            />
          )}
        {count > TYPED_MESSAGE_LIMIT && (
          <p role="alert">
            That’s long. Save it as a file instead.{" "}
            <button type="button" onClick={saveText}>
              Save text as a file
            </button>
          </p>
        )}
        {model.notice && <p role="status">{model.notice}</p>}
      </form>
    </section>
  );
}
