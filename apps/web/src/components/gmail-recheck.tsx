"use client";

import { useEffect, useRef, useState } from "react";
import {
  type RecheckItem,
  type RecheckPage,
  recheckReasons,
  recheckRequest,
  removalSelection,
  runRecheckBatch,
} from "../lib/gmail-recheck";
import styles from "./intelligence-controls.module.css";
import { SourceEvidence } from "./source-evidence";

export function RecheckRow({
  item,
  selected,
  disabled,
  selectionFull,
  onSelect,
  onKeep,
  onRetry,
}: {
  item: RecheckItem;
  selected: boolean;
  disabled: boolean;
  selectionFull: boolean;
  onSelect: () => void;
  onKeep: () => void;
  onRetry: () => void;
}) {
  const canRemove =
    item.outcome === "remove_suggested" && item.preview_id !== null;
  const canKeep =
    item.preview_id &&
    ["remove_suggested", "retained", "needs_review", "failed"].includes(
      item.outcome,
    );
  return (
    <li className={styles.recheckItem}>
      <div className={styles.recheckTitle}>
        {canRemove && (
          <input
            type="checkbox"
            checked={selected}
            disabled={disabled || (!selected && selectionFull)}
            onChange={onSelect}
            aria-label={`Select for removal: ${item.title}`}
          />
        )}
        <strong>{item.title}</strong>
      </div>
      <p className={styles.copy}>
        {item.outcome === "removed"
          ? "Removed from Today."
          : item.outcome === "kept"
            ? "Kept by you."
            : (recheckReasons[item.reason] ?? "This item needs your review.")}
      </p>
      <div className={styles.actions}>
        {canKeep && (
          <button type="button" disabled={disabled} onClick={onKeep}>
            Keep this item
          </button>
        )}
        {item.outcome === "failed" && (
          <button type="button" disabled={disabled} onClick={onRetry}>
            Retry this item
          </button>
        )}
      </div>
      {!disabled &&
        item.outcome !== "removed" &&
        item.evidence_ids?.map((id) => (
          <SourceEvidence key={id} evidenceId={id} paused={false} />
        ))}
    </li>
  );
}

export function GmailRecheck({
  connectionId,
  disabled,
  onRefresh,
}: {
  connectionId: string;
  disabled: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<RecheckItem[]>([]);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [selected, setSelected] = useState(new Set<string>());
  const [busy, setBusy] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const running = useRef(false);
  const stop = useRef(false);
  const blocked = useRef(disabled);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    blocked.current = disabled;
  }, [disabled]);
  useEffect(
    () => () => {
      stop.current = true;
      controller.current?.abort();
    },
    [],
  );

  async function work(task: (signal: AbortSignal) => Promise<void>) {
    if (running.current || blocked.current) return;
    running.current = true;
    stop.current = false;
    setBusy(true);
    setError("");
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    try {
      await task(current.signal);
    } catch (cause) {
      if (!current.signal.aborted)
        setError(
          cause instanceof Error ? cause.message : "Recheck could not finish.",
        );
    } finally {
      running.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }

  function updateItem(item: RecheckItem) {
    setItems((previous) =>
      previous.map((old) =>
        old.commitment_id === item.commitment_id ? item : old,
      ),
    );
    setSelected((previous) => {
      const next = new Set(previous);
      next.delete(item.commitment_id);
      return next;
    });
  }

  async function load(signal: AbortSignal, append = false) {
    const query = new URLSearchParams({ connection_id: connectionId });
    if (append && nextAfter) query.set("after", nextAfter);
    const page = await recheckRequest<RecheckPage>(`?${query}`, signal);
    setItems((previous) =>
      append
        ? [
            ...previous,
            ...page.items.filter(
              (item) =>
                !previous.some(
                  (old) => old.commitment_id === item.commitment_id,
                ),
            ),
          ]
        : page.items,
    );
    setNextAfter(page.next_after);
    setSelected(new Set());
    setOpen(true);
  }

  async function preview(
    item: RecheckItem,
    signal: AbortSignal,
    retry = false,
  ) {
    return recheckRequest<RecheckItem>("/preview", signal, {
      connection_id: connectionId,
      commitment_id: item.commitment_id,
      retry,
    });
  }

  async function batch(signal: AbortSignal) {
    let checked = 0;
    setBatchRunning(true);
    try {
      await runRecheckBatch({
        items,
        preview: (item) => preview(item, signal),
        onStart: (_item, position, total) =>
          setMessage(`Checking ${position} of ${total}…`),
        onResult: (item) => {
          updateItem(item);
          checked += item.outcome === "checking" ? 0 : 1;
        },
        shouldStop: () => stop.current || blocked.current || signal.aborted,
      });
    } finally {
      if (!signal.aborted) setBatchRunning(false);
    }
    if (!signal.aborted)
      setMessage(
        `${checked} items checked. Review the results below. Nothing has been removed.`,
      );
  }

  async function apply(
    action: "remove" | "keep",
    chosen: { commitment_id: string; preview_id: string | null }[],
    signal: AbortSignal,
  ) {
    if (!chosen.length) return;
    const result = await recheckRequest<{
      applied: string[];
      skipped: string[];
    }>("/apply", signal, {
      connection_id: connectionId,
      action,
      items: chosen,
    });
    setItems((previous) =>
      previous.map((item) =>
        result.applied.includes(item.commitment_id)
          ? {
              ...item,
              outcome: action === "remove" ? "removed" : "kept",
              reason: "owner_selected",
            }
          : result.skipped.includes(item.commitment_id)
            ? {
                ...item,
                outcome: "unchecked",
                preview_id: null,
                reason: "not_checked",
              }
            : item,
      ),
    );
    setSelected(new Set());
    setMessage(
      `${result.applied.length} items ${action === "remove" ? "removed from Today" : "kept"}.${result.skipped.length ? ` ${result.skipped.length} changed or expired; recheck them first.` : ""}`,
    );
    try {
      await onRefresh();
    } catch {
      setError(
        "Your choices were saved. Use Refresh Today to reload the workspace.",
      );
    }
  }

  const chosen = removalSelection(items, selected);
  const suggestions = items.filter(
    (item) => item.outcome === "remove_suggested" && item.preview_id,
  );
  const checking = items.some((item) => item.outcome === "checking");
  return (
    <div className={styles.recheck}>
      <button
        className={styles.secondary}
        type="button"
        disabled={busy || disabled}
        aria-expanded={open}
        onClick={() =>
          open ? setOpen(false) : void work((signal) => load(signal))
        }
      >
        {open ? "Hide older Gmail items" : "Review older Gmail items"}
      </button>
      {open && (
        <>
          <p className={styles.copy}>
            Recheck older cards against their emails. Review suggested removals
            before clearing them from Today. Your emails stay in Gmail.
          </p>
          <p className={styles.copy}>
            Each batch checks up to 10 items using your AI provider, with up to
            30 AI requests. Older cards may include choices you previously made.
          </p>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={
                busy ||
                disabled ||
                checking ||
                !items.some((item) => item.outcome === "unchecked")
              }
              onClick={() => void work(batch)}
            >
              Recheck up to 10 items
            </button>
            <button
              type="button"
              disabled={busy || disabled}
              onClick={() => void work((signal) => load(signal))}
            >
              Refresh results
            </button>
            {batchRunning && (
              <button
                type="button"
                onClick={() => {
                  stop.current = true;
                  setMessage("Stopping after the current check finishes…");
                }}
              >
                Stop after current check
              </button>
            )}
          </div>
          {checking && (
            <p className={styles.copy}>
              A check is already running. Refresh results to see its saved
              outcome.
            </p>
          )}
          {suggestions.length > 0 && (
            <div className={styles.actions}>
              <button
                type="button"
                disabled={busy || disabled}
                onClick={() =>
                  setSelected(
                    new Set(
                      suggestions
                        .slice(0, 25)
                        .map((item) => item.commitment_id),
                    ),
                  )
                }
              >
                Select suggested removals
              </button>
              <button
                type="button"
                disabled={busy || disabled || !chosen.length}
                onClick={() =>
                  void work((signal) => apply("remove", chosen, signal))
                }
              >
                Remove selected ({chosen.length})
              </button>
            </div>
          )}
          {!items.length && !nextAfter && (
            <p className={styles.copy}>No older Gmail items need rechecking.</p>
          )}
          <ul className={styles.recheckList}>
            {items.map((item) => (
              <RecheckRow
                key={item.commitment_id}
                item={item}
                selected={selected.has(item.commitment_id)}
                disabled={busy || disabled}
                selectionFull={selected.size >= 25}
                onSelect={() =>
                  setSelected((previous) => {
                    const next = new Set(previous);
                    if (next.has(item.commitment_id))
                      next.delete(item.commitment_id);
                    else if (next.size < 25) next.add(item.commitment_id);
                    return next;
                  })
                }
                onKeep={() =>
                  void work((signal) =>
                    apply(
                      "keep",
                      [
                        {
                          commitment_id: item.commitment_id,
                          preview_id: item.preview_id,
                        },
                      ],
                      signal,
                    ),
                  )
                }
                onRetry={() =>
                  void work(async (signal) =>
                    updateItem(await preview(item, signal, true)),
                  )
                }
              />
            ))}
          </ul>
          {nextAfter && (
            <button
              className={styles.secondary}
              type="button"
              disabled={busy || disabled}
              onClick={() => void work((signal) => load(signal, true))}
            >
              Load more older items
            </button>
          )}
        </>
      )}
      {message && (
        <p className={styles.message} role="status">
          {message}
        </p>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
