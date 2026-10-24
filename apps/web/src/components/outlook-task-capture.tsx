"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { OutlookMessageDetail } from "../lib/outlook";
import { outlookTaskApi, prepareOutlookTask } from "../lib/outlook-tasks";
import { productFeatures } from "../lib/product-features";
import {
  observeStudentEmailPrivacy,
  StudentEmailController,
  StudentEmailRequestError,
} from "../lib/student-email";
import {
  StudentEmailCorrectionEditor,
  StudentEmailEvidence,
  StudentEmailTaskActions,
} from "./student-email-actions";
import styles from "./student-email-actions.module.css";

export function OutlookTaskCapture(props: {
  accountKey: string;
  connectionId: string;
  message: OutlookMessageDetail;
  timezone: string;
}) {
  return (
    <SelectedOutlookTask
      key={`${props.accountKey}:${props.connectionId}:${props.message.id}`}
      {...props}
    />
  );
}

function SelectedOutlookTask({
  accountKey,
  connectionId,
  message,
}: {
  accountKey: string;
  connectionId: string;
  message: OutlookMessageDetail;
  timezone: string;
}) {
  const [controller] = useState(
    () => new StudentEmailController(accountKey, outlookTaskApi(accountKey)),
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  const [consent, setConsent] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [error, setError] = useState("");
  const [forgetConfirmed, setForgetConfirmed] = useState(false);
  const pending = useRef<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const live = useRef(false);
  useEffect(() => {
    live.current = true;
    const clear = () => {
      request.current?.abort();
      request.current = null;
      controller.clear();
      setConsent(false);
      setForgetConfirmed(false);
      setPreparing(false);
      setUnknown(false);
      setError("");
      pending.current = null;
    };
    const stop = observeStudentEmailPrivacy(clear, {
      window,
      document,
      channel:
        typeof BroadcastChannel === "undefined"
          ? null
          : new BroadcastChannel("navox-account-session"),
    });
    return () => {
      live.current = false;
      stop();
    };
  }, [controller]);
  const disabled = preparing || state.busy || !!state.pending || state.blocked;
  async function begin() {
    if (disabled || request.current || (!consent && !unknown)) return;
    setPreparing(true);
    setConsent(false);
    setError("");
    const abort = new AbortController();
    request.current = abort;
    pending.current ??= crypto.randomUUID();
    try {
      const review = await prepareOutlookTask(
        accountKey,
        connectionId,
        message.id,
        pending.current,
        abort.signal,
      );
      if (!live.current || abort.signal.aborted) return;
      setUnknown(false);
      await controller.openExternalReview(review.review_id);
      if (live.current && !abort.signal.aborted) pending.current = null;
    } catch (cause) {
      if (!live.current || abort.signal.aborted) return;
      const uncertain =
        cause instanceof StudentEmailRequestError && cause.uncertain;
      setUnknown(uncertain);
      if (!uncertain) pending.current = null;
      setError(
        uncertain
          ? "The review result is unknown. Check this same request before starting another."
          : cause instanceof Error
            ? cause.message
            : "The selected email could not be reviewed.",
      );
    } finally {
      if (live.current && !abort.signal.aborted) {
        setPreparing(false);
        request.current = null;
      }
    }
  }
  const review = state.review,
    context = state.context;
  return (
    <section
      className={`${styles.panel} ${styles.inboxTheme}`}
      aria-label="Create a task from this Outlook email"
      aria-busy={disabled}
    >
      <div className={styles.card}>
        <h3>Create a task from this email</h3>
        <p>
          Review <strong>{message.subject}</strong>, then choose the task title,
          course, deadline and effort. Reading this email does not create a
          task.
        </p>
        {!review && (
          <>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={consent}
                disabled={disabled || unknown}
                onChange={(event) => setConsent(event.target.checked)}
              />
              Read this selected Outlook message to prepare a task review.
            </label>
            <button
              type="button"
              disabled={disabled || (!consent && !unknown)}
              onClick={() => void begin()}
            >
              {unknown
                ? "Check this exact review request"
                : "Review email as a task"}
            </button>
          </>
        )}
        <p className={styles.hint}>
          Only this message is read. Evidence and corrections expire after 20
          minutes. Attachments are not read.
        </p>
        {(error || state.error) && <p role="alert">{error || state.error}</p>}
        {state.notice && <p role="status">{state.notice}</p>}
        {state.pending && (
          <button
            type="button"
            disabled={state.busy || state.blocked}
            onClick={() => void controller.checkPending()}
          >
            Check this exact task request
          </button>
        )}
      </div>
      {review && context && (
        <div key={`${review.review_id}:${review.revision}:${state.epoch}`}>
          <StudentEmailEvidence review={review} timezone={context.timezone} />
          {!review.task_id && !state.receipt && (
            <StudentEmailCorrectionEditor
              review={review}
              courses={context.courses}
              timezone={context.timezone}
              disabled={disabled || unknown}
              onSave={controller.correct}
              onNotImportant={() => void controller.notImportant()}
            />
          )}
          <StudentEmailTaskActions
            review={review}
            timezone={context.timezone}
            disabled={disabled || unknown}
            planningEnabled={productFeatures.studentPlanning}
            accepted={!!state.receipt?.available}
            taskUnavailable={!!state.receipt && !state.receipt.available}
            showReplyHandoff={false}
            onAccept={(confirmed, refresh) =>
              void controller.accept(confirmed, refresh)
            }
            onCheck={() => void controller.reopen(review.review_id)}
          />
        </div>
      )}
      {state.handles.length > 0 && (
        <details className={styles.card}>
          <summary>Saved review and task privacy</summary>
          <p>
            Erasing a review keeps a task you already accepted. Forgetting this
            email’s saved work removes its reviews and copied tasks from NavoX.
            Your original email remains in Outlook.
          </p>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={forgetConfirmed}
              disabled={disabled}
              onChange={(event) => setForgetConfirmed(event.target.checked)}
            />
            I confirm forgetting this email’s saved reviews and copied tasks.
          </label>
          {state.handles.map((handle) => (
            <div className={styles.actions} key={handle.review_id}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => void controller.reopen(handle.review_id)}
              >
                Reopen saved review
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() => void controller.erase(handle)}
              >
                Erase review only
              </button>
              <button
                type="button"
                disabled={disabled || !forgetConfirmed}
                onClick={() => void controller.forget(handle.source_id, true)}
              >
                Forget review and copied task
              </button>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
