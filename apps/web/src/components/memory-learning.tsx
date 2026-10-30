"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  conversationMemoryRequest,
  MemoryClientError,
} from "../lib/conversation-memory";
import {
  type MemoryLearning,
  type PrivateMemorySuggestion,
  readMemoryLearning,
  readSavedSuggestion,
} from "../lib/memory-learning";
import styles from "./memory-privacy.module.css";
import { useVoxCall } from "./vox-call-provider";

function learningStatus(value: MemoryLearning) {
  switch (value.status_reason) {
    case "disabled":
      return "Learning from saved conversations is unavailable here. You can still add memories yourself.";
    case "history_required":
      return "Choose retained History and accept its disclosure before learning from saved conversations can run.";
    case "off_record":
      return "Learning is paused while Off the record is on.";
    case "unconfigured":
      return "No qualified learning route is available here. Existing suggestions can still be reviewed.";
    case "ready":
      return value.enabled && value.configured
        ? "Private suggestions can be reviewed here. Learning during retained Vox calls follows your memory choice; background learning requires separate qualified configuration. Private suggestions always need your separate approval."
        : "Learning availability could not be confirmed. Check again before relying on it.";
  }
}
export function MemoryLearningPanel({
  accountKey,
  generation,
  disabled,
  onSaved,
  onUncertain,
  onBusyChange,
  onPrivacyChanged,
}: {
  accountKey: string;
  generation: number;
  disabled: boolean;
  onSaved: () => void;
  onUncertain: () => void;
  onBusyChange: (busy: boolean) => void;
  onPrivacyChanged: () => void;
}) {
  const call = useVoxCall();
  const id = useId(),
    scope = `${accountKey}:${generation}`;
  const [stored, setStored] = useState<{
    scope: string;
    value: MemoryLearning;
  } | null>(null);
  const [agreements, setAgreements] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState("");
  const [locked, setLocked] = useState(false);
  const [notice, setNotice] = useState("");
  const [now, setNow] = useState(Date.now);
  const epoch = useRef(0),
    latest = useRef(scope);
  const flow = useRef({ busy: false, uncertain: false, disabled });
  latest.current = scope;
  const view = stored?.scope === scope ? stored.value : null;
  useEffect(() => {
    ++epoch.current;
    flow.current.busy = false;
    flow.current.uncertain = false;
    setStored((previous) => (previous?.scope === scope ? previous : null));
    setAgreements({});
    setBusy("");
    setLocked(false);
    setNotice("");
    return () => {
      ++epoch.current;
    };
  }, [scope]);
  useEffect(() => {
    const wasDisabled = flow.current.disabled;
    flow.current.disabled = disabled;
    if (!disabled && wasDisabled && flow.current.uncertain) {
      flow.current.uncertain = false;
      setLocked(false);
      setStored(null);
      setAgreements({});
      setNotice(
        "Saved memory reloaded. Check for fresh suggestions before another change.",
      );
    }
  }, [disabled]);
  useEffect(() => {
    if (!view) return;
    const next = Math.min(
      ...view.pending
        .map((item) => Date.parse(item.expires_at))
        .filter((at) => at > now),
    );
    if (!Number.isFinite(next)) return;
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.max(0, next - Date.now() + 1),
    );
    return () => clearTimeout(timer);
  }, [view, now]);
  async function run(
    kind: "check" | "save" | "dismiss",
    suggestion?: PrivateMemorySuggestion,
  ) {
    if (
      disabled ||
      flow.current.busy ||
      locked ||
      (kind !== "check" &&
        (!view ||
          !suggestion ||
          Date.parse(suggestion.expires_at) <= Date.now())) ||
      (kind === "save" && !agreements[suggestion?.id ?? ""]) ||
      (kind === "save" && !view?.enabled)
    )
      return;
    const current = epoch.current,
      fresh = () => current === epoch.current && latest.current === scope;
    flow.current.busy = true;
    setBusy(kind === "check" ? "check" : `${kind}:${suggestion?.id}`);
    onBusyChange(true);
    setNotice("");
    try {
      if (kind === "check") {
        setStored(null);
        setAgreements({});
        const value = await conversationMemoryRequest(
          accountKey,
          "/learning",
          readMemoryLearning,
        );
        if (!fresh()) return;
        if (value.generation !== generation) {
          setNotice(
            "Your memory settings changed. Loading the current choice before another review.",
          );
          onPrivacyChanged();
        } else {
          setStored({ scope, value });
          setNow(Date.now());
        }
      } else if (suggestion) {
        if (kind === "save" && call?.state.callId) {
          await call.controller.hangUp();
          if (!fresh()) return;
        }
        await conversationMemoryRequest(
          accountKey,
          `/learning/${encodeURIComponent(suggestion.id)}/${kind === "save" ? "confirm" : "dismiss"}`,
          (value) => {
            if (kind === "save") return readSavedSuggestion(value, suggestion);
            if (value !== null)
              throw new Error("Dismissal could not be checked");
            return null;
          },
          "POST",
          kind === "save"
            ? {
                generation,
                confirmation: "SAVE MEMORY",
                sensitive_agreement: true,
              }
            : { generation },
        );
        if (!fresh()) return;
        setAgreements({});
        if (kind === "save") {
          setStored(null);
          setNotice("Memory saved. Its source remains readable in History.");
          onSaved();
        } else {
          setStored((previous) =>
            previous?.scope === scope
              ? {
                  ...previous,
                  value: {
                    ...previous.value,
                    pending: previous.value.pending.filter(
                      (item) => item.id !== suggestion.id,
                    ),
                  },
                }
              : previous,
          );
          setNotice(
            "Suggestion dismissed. Its saved conversation was not deleted.",
          );
        }
      }
    } catch (cause) {
      if (!fresh()) return;
      setStored(null);
      setAgreements({});
      if (
        kind !== "check" &&
        cause instanceof MemoryClientError &&
        cause.uncertain
      ) {
        flow.current.uncertain = true;
        setLocked(true);
        setNotice(
          "The result could not be confirmed. Reload your memory before making another change.",
        );
        onUncertain();
      } else
        setNotice(
          kind === "check"
            ? "Learning and suggestions could not be checked. Try again."
            : "That suggestion changed or could not be applied. Check for fresh suggestions before trying again.",
        );
    } finally {
      if (fresh()) {
        flow.current.busy = false;
        setBusy("");
        onBusyChange(false);
      }
    }
  }
  const blocked = disabled || !!busy || locked;
  const pending =
    view?.pending.filter((item) => Date.parse(item.expires_at) > now) ?? [];
  return (
    <section
      className={styles.group}
      aria-labelledby={`${id}-title`}
      aria-busy={!!busy}
    >
      <h3 id={`${id}-title`}>Private memory suggestions</h3>
      <p>
        Review private memory suggestions from retained History. Checking only
        loads existing suggestions; it does not save a fact or change your
        memory choice.
      </p>
      <button
        type="button"
        disabled={blocked}
        onClick={() => void run("check")}
      >
        {busy === "check"
          ? "Checking suggestions…"
          : "Check learning and suggestions"}
      </button>
      {view && (
        <>
          <p role="status">{learningStatus(view)}</p>
          {!pending.length && (
            <p>No current private suggestions in this checked list.</p>
          )}
          {pending.length > 0 && (
            <>
              <p className={styles.sensitiveNote}>
                These suggestions may contain private details. Saving keeps the
                fact and its whole source conversation out of automatic voice
                recall; History remains readable. Saving ends the current Vox
                call and clears active chat context. Saved History remains
                available.
              </p>
              {pending.map((suggestion) => (
                <article
                  key={suggestion.id}
                  className={styles.group}
                  aria-label={`Private suggestion: ${suggestion.label}`}
                >
                  <h4>{suggestion.label}</h4>
                  <p className={styles.prewrap}>{suggestion.content}</p>
                  <p className={styles.muted}>
                    From a retained{" "}
                    {suggestion.source_kind === "vox"
                      ? "Vox conversation"
                      : "chat conversation"}
                    . You can correct or forget the saved fact afterward.
                  </p>
                  <label className={styles.check}>
                    <input
                      type="checkbox"
                      checked={!!agreements[suggestion.id]}
                      disabled={blocked || !view.enabled}
                      onChange={(event) =>
                        setAgreements((previous) => ({
                          ...previous,
                          [suggestion.id]: event.target.checked,
                        }))
                      }
                    />
                    <span>
                      I separately agree to save the health, family or other
                      private details in this exact statement.
                    </span>
                  </label>
                  <div className={styles.actions}>
                    <button
                      type="button"
                      disabled={
                        blocked || !view.enabled || !agreements[suggestion.id]
                      }
                      onClick={() => void run("save", suggestion)}
                    >
                      {busy === `save:${suggestion.id}`
                        ? "Saving suggestion…"
                        : "Save this suggestion"}
                    </button>
                    <button
                      type="button"
                      disabled={blocked}
                      onClick={() => void run("dismiss", suggestion)}
                    >
                      Dismiss suggestion
                    </button>
                  </div>
                </article>
              ))}
            </>
          )}
          {view.has_more && (
            <p>
              More suggestions may be available. Review this checked list, then
              check again for another bounded list.
            </p>
          )}
        </>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
