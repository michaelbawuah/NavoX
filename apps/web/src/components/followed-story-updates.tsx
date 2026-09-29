"use client";

import { useEffect, useId, useRef, useState } from "react";
import { newsRequest, newsTime } from "../lib/news";
import {
  acknowledgeableVersion,
  type FollowedUpdate,
  type FollowingPage,
} from "../lib/news-following";
import styles from "./followed-story-updates.module.css";
import { NavoXCard, NavoXStatus } from "./navox-ui";

export function FollowedUpdateList({
  page,
  busy,
  onSeen,
}: {
  page: FollowingPage;
  busy: boolean;
  onSeen: (entry: FollowedUpdate) => void;
}) {
  return (
    <div>
      {page.entries.length === 0 && (
        <p>
          {page.next_story_id
            ? "No new evidence changes on this page. There are more followed stories to check."
            : "No new recorded evidence changes in these followed stories."}
        </p>
      )}
      {page.entries.map((entry) => (
        <NavoXCard key={entry.story.id}>
          <h3>
            <a href={`/news/stories/${encodeURIComponent(entry.story.id)}`}>
              {entry.story.headline}
            </a>
          </h3>
          <NavoXStatus status={entry.story.verification_status} />
          {entry.baseline_required ? (
            <p>Choose a starting point for this previously followed story.</p>
          ) : (
            <>
              <p>
                {entry.events.length} recorded evidence{" "}
                {entry.events.length === 1 ? "change" : "changes"} since your
                last acknowledgement.
              </p>
              {!!entry.events.length && (
                <p>
                  Latest recorded change:{" "}
                  {newsTime(entry.events[entry.events.length - 1].generated_at)}
                  . Open the story to review the current evidence.
                </p>
              )}
              {!entry.history_complete && (
                <p>
                  This is a partial history. More changes may exist; missing
                  history is not treated as read.
                </p>
              )}
            </>
          )}
          {acknowledgeableVersion(entry) !== null && (
            <button type="button" disabled={busy} onClick={() => onSeen(entry)}>
              {entry.baseline_required
                ? "Start tracking from this version"
                : "Mark shown updates seen"}
            </button>
          )}
        </NavoXCard>
      ))}
    </div>
  );
}

export function FollowedStoryUpdates({
  refreshing = false,
}: {
  refreshing?: boolean;
} = {}) {
  if (refreshing)
    return (
      <p role="status">
        Refreshing news. Check followed stories again afterward.
      </p>
    );
  return <FollowedStoryUpdatesContent />;
}

function FollowedStoryUpdatesContent() {
  const headingId = useId();
  const [page, setPage] = useState<FollowingPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function check(after: string | null = null, seen?: FollowedUpdate) {
    const observed = seen ? acknowledgeableVersion(seen) : null;
    if (seen && observed === null) return;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setNotice("");
    setPage(null);
    try {
      if (seen)
        await newsRequest(
          `/following/${encodeURIComponent(seen.story.id)}/seen`,
          {
            method: "POST",
            signal: request.signal,
            body: JSON.stringify({ observed_version: observed }),
          },
        );
      const query = after ? `&after_story_id=${encodeURIComponent(after)}` : "";
      const result = await newsRequest<FollowingPage>(
        `/following/updates?limit=5${query}`,
        {
          signal: request.signal,
        },
      );
      if (!request.signal.aborted) {
        setPage(result);
        setNotice(
          seen ? "Your selected update marker is saved." : "Updates checked.",
        );
      }
    } catch (reason) {
      if (!request.signal.aborted)
        setNotice(
          reason instanceof Error
            ? reason.message
            : "Updates are unavailable. Try again.",
        );
    } finally {
      if (!request.signal.aborted) setBusy(false);
    }
  }

  return (
    <section
      className={styles.panel}
      aria-labelledby={headingId}
      aria-busy={busy}
    >
      <h2 id={headingId}>Changes in stories you follow</h2>
      <p>
        Follow a story to keep up with recorded changes to its evidence. Opening
        this list does not mark anything as read.
      </p>
      <button type="button" disabled={busy} onClick={() => void check()}>
        {busy ? "Checking updates…" : "Check followed stories"}
      </button>
      <p role="status" aria-live="polite">
        {notice}
      </p>
      {page && (
        <>
          <FollowedUpdateList
            page={page}
            busy={busy}
            onSeen={(entry) => void check(null, entry)}
          />
          {page.next_story_id && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void check(page.next_story_id)}
            >
              Check next followed stories
            </button>
          )}
          <p className={styles.note}>
            Checked {newsTime(page.as_of)}. Changes are not a new verification
            or a completeness guarantee.
          </p>
        </>
      )}
    </section>
  );
}
