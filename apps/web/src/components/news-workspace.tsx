"use client";

import type {
  NewsAvailability,
  NewsCategory,
  NewsClaim,
  NewsPreferences,
  NewsSourceOption,
  NewsStory,
} from "@navox/contracts";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  loadNewsFeed,
  type NewsFeed,
  NewsRequestError,
  newsRequest,
  newsTime,
  newsTrendingCaption,
} from "../lib/news";
import { parseFollowedLabels } from "../lib/news-preferences";
import { FollowedStoryUpdates } from "./followed-story-updates";
import {
  NavoXCard,
  NavoXEmptyState,
  NavoXErrorState,
  NavoXNavigation,
  NavoXPageHeader,
  NavoXSkeleton,
  NavoXSourceList,
  NavoXStatus,
} from "./navox-ui";
import { NewsChat } from "./news-chat";
import styles from "./news-workspace.module.css";
import { RelatedStories } from "./related-stories";
import { StoryIntelligence } from "./story-intelligence";

const categories: [NewsCategory, string][] = [
  ["world", "World"],
  ["us", "U.S."],
  ["business", "Business"],
  ["technology", "Technology"],
  ["science", "Science"],
];
type Feed = NewsFeed;

function NewsFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className={styles.workspace}>
      <a className={styles.skipLink} href="#news-main">
        Skip to news
      </a>
      <header className={styles.topbar}>
        <a href="/" className={styles.brand} aria-label="NavoX home">
          NavoX<span aria-hidden="true">✦</span>
        </a>
        <NavoXNavigation current="News" />
      </header>
      <main id="news-main" className={styles.main}>
        {children}
      </main>
      <footer className={styles.footer}>
        NavoX · A clearer view of what’s happening.
      </footer>
    </div>
  );
}

export function NewsStoryCard({
  story,
  busy = false,
  onSave,
}: {
  story: NewsStory;
  busy?: boolean;
  onSave: () => void;
}) {
  return (
    <NavoXCard>
      <div className={styles.cardTop}>
        <span>
          {categories.find(([value]) => value === story.category)?.[1]}
        </span>
        <time dateTime={story.published_at}>
          {newsTime(story.published_at)}
        </time>
      </div>
      <h2>
        <a href={`/news/stories/${story.id}`}>{story.headline}</a>
      </h2>
      {story.description && (
        <p className={styles.excerpt}>{story.description}</p>
      )}
      <div className={styles.cardBottom}>
        <div className={styles.sourceRow}>
          <NavoXStatus status={story.verification_status} />
          <span>
            {story.sources[0]?.source_name}
            {story.source_count > 1 ? ` + ${story.source_count - 1} more` : ""}
          </span>
        </div>
        <button
          type="button"
          aria-pressed={story.saved}
          disabled={busy}
          onClick={onSave}
          aria-label={`${story.saved ? "Unsave" : "Save"} ${story.headline}`}
        >
          {story.saved ? "Saved ✓" : "Save"}
        </button>
      </div>
    </NavoXCard>
  );
}

export function NewsFeedTabs({
  feed,
  onSelect,
  reviewedImportance = false,
}: {
  feed: Feed;
  onSelect: (feed: Feed) => void;
  reviewedImportance?: boolean;
}) {
  return (
    <>
      <nav aria-label="News sections" className={styles.tabs}>
        {(
          [
            ["top", reviewedImportance ? "Top stories" : "Latest"],
            ["for-you", "For you"],
            ["trending", "Trending"],
            ...categories,
            ["saved", "Saved"],
          ] as [Feed, string][]
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={feed === value}
            onClick={() => onSelect(value)}
          >
            {label}
          </button>
        ))}
      </nav>
      {feed === "trending" && (
        <p className={styles.feedCaption}>{newsTrendingCaption}</p>
      )}
    </>
  );
}

export function NewsWorkspace() {
  const [selection, setSelection] = useState<{ feed: Feed }>({ feed: "top" });
  const { feed } = selection;
  const [availability, setAvailability] = useState<NewsAvailability | null>(
    null,
  );
  const [stories, setStories] = useState<NewsStory[]>([]);
  const [sources, setSources] = useState<NewsSourceOption[]>([]);
  const [preferences, setPreferences] = useState<NewsPreferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [signedOut, setSignedOut] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    setLoading(true);
    setError("");
    setStories([]);
    async function load() {
      try {
        const available = await newsRequest<NewsAvailability>("/availability", {
          signal,
        });
        if (signal.aborted) return;
        setAvailability(available);
        setSignedOut(false);
        if (!available.feed) return;
        const [items, options, prefs] = await Promise.all([
          loadNewsFeed(selection.feed, signal),
          newsRequest<NewsSourceOption[]>("/sources", { signal }),
          newsRequest<NewsPreferences>("/preferences", { signal }),
        ]);
        if (!signal.aborted) {
          setStories(items);
          setSources(options);
          setPreferences(prefs);
        }
      } catch (reason) {
        if (signal.aborted) return;
        setSignedOut(
          reason instanceof NewsRequestError && reason.status === 401,
        );
        setError(
          reason instanceof Error
            ? reason.message
            : "We couldn’t open your news.",
        );
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [selection]);

  useEffect(() => {
    if (!availability?.feed) return;
    const timer = window.setInterval(
      () => setSelection((value) => ({ ...value })),
      60_000,
    );
    return () => window.clearInterval(timer);
  }, [availability?.feed]);

  async function save(story: NewsStory) {
    setBusy(story.id);
    setNotice("");
    try {
      await newsRequest(`/stories/${story.id}/save`, {
        method: "POST",
        body: JSON.stringify({ enabled: !story.saved }),
      });
      setSelection((value) => ({ ...value }));
      setNotice(story.saved ? "Story removed from Saved." : "Story saved.");
    } catch (reason) {
      setNotice(
        reason instanceof Error
          ? reason.message
          : "We couldn’t save this story.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function sourceCommand(
    source: NewsSourceOption,
    action: "activate" | "disable" | "refresh",
  ) {
    setBusy(source.key);
    setNotice("");
    try {
      const result = await newsRequest<{ status: string }>(
        `/sources/${action === "activate" ? source.key : source.id}/${action}`,
        {
          method: "POST",
          body: JSON.stringify({ request_id: crypto.randomUUID() }),
        },
      );
      setSelection((value) => ({ ...value }));
      setNotice(
        result.status === "FAILED"
          ? "This source couldn’t refresh. Try again later."
          : action === "activate"
            ? "Source added. Reports will appear after its next refresh."
            : action === "disable"
              ? "Source removed."
              : "Source refreshed.",
      );
    } catch (reason) {
      setNotice(
        reason instanceof Error
          ? reason.message
          : "This source is unavailable.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function savePreferences(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("preferences");
    setNotice("");
    const data = new FormData(event.currentTarget);
    try {
      const result = await newsRequest<NewsPreferences>("/preferences", {
        method: "PATCH",
        body: JSON.stringify({
          categories: data.getAll("category"),
          topics: parseFollowedLabels(data.get("topics")),
          entities: parseFollowedLabels(data.get("entities")),
        }),
      });
      setPreferences(result);
      setSelection((value) => ({ ...value }));
      setNotice("Your interests are saved.");
    } catch (reason) {
      setNotice(
        reason instanceof Error
          ? reason.message
          : "We couldn’t save your interests.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <NewsFrame>
      <NavoXPageHeader
        title="Catch up on the world."
        description="The latest headlines, with original reporting a click away."
      />
      {availability?.chat && !signedOut && <NewsChat />}
      {loading && <NavoXSkeleton />}
      {!loading && signedOut ? (
        <NavoXEmptyState title="Your news starts here.">
          <p>Sign in to follow stories and save what matters to you.</p>
          <a href="/#access-panel">Sign in to NavoX →</a>
        </NavoXEmptyState>
      ) : !loading && error ? (
        <NavoXErrorState
          message={error}
          onRetry={() => setSelection((value) => ({ ...value }))}
        />
      ) : !loading && !availability?.feed ? (
        <NavoXEmptyState title="News is getting ready.">
          <p>Connect a news source in Settings to start seeing headlines.</p>
          <a href="/">Back to Today →</a>
        </NavoXEmptyState>
      ) : null}
      {availability?.feed && !signedOut && !error && (
        <>
          <NewsFeedTabs
            feed={feed}
            reviewedImportance={
              stories.length > 0 &&
              stories.every(
                (story) => story.ranking_basis === "REVIEWED_IMPORTANCE",
              )
            }
            onSelect={(value) => setSelection({ feed: value })}
          />
          {feed === "top" && stories.length > 0 && (
            <p className={styles.notice}>
              {stories.every(
                (story) => story.ranking_basis === "REVIEWED_IMPORTANCE",
              )
                ? "Top stories, selected with supporting reporting."
                : "Latest headlines from your news sources."}
            </p>
          )}
          <p role="status" className={styles.notice}>
            {notice}
          </p>
          {!loading &&
            !error &&
            (stories.length ? (
              <div className={styles.feed}>
                {stories.map((story) => (
                  <NewsStoryCard
                    key={story.id}
                    story={story}
                    busy={busy === story.id}
                    onSave={() => void save(story)}
                  />
                ))}
              </div>
            ) : (
              <NavoXEmptyState
                title={
                  feed === "saved"
                    ? "Keep a story for later."
                    : "No stories here yet."
                }
              >
                <p>
                  {feed === "saved"
                    ? "Save any story and you’ll find it here."
                    : "Add a source below, or check another category as new reports arrive."}
                </p>
              </NavoXEmptyState>
            ))}
          <section className={styles.settings} aria-label="Your news settings">
            <details>
              <summary>Stories you follow</summary>
              {!loading && (
                <FollowedStoryUpdates refreshing={loading || busy !== null} />
              )}
            </details>
            <details>
              <summary>Your interests</summary>
              {preferences && (
                <form onSubmit={savePreferences}>
                  <fieldset>
                    <legend>Choose your categories</legend>
                    {categories.map(([value, label]) => (
                      <label key={value}>
                        <input
                          type="checkbox"
                          name="category"
                          value={value}
                          defaultChecked={preferences.categories.includes(
                            value,
                          )}
                        />
                        {label}
                      </label>
                    ))}
                  </fieldset>
                  <label htmlFor="news-topics">Topics you follow</label>
                  <input
                    id="news-topics"
                    name="topics"
                    defaultValue={preferences.topics.join(", ")}
                    maxLength={1000}
                    placeholder="Space, renewable energy, local transport"
                  />
                  <p>
                    Separate topics with commas. These choices shape For you.
                  </p>
                  <label htmlFor="news-entities">
                    People, organizations and places you follow
                  </label>
                  <input
                    id="news-entities"
                    name="entities"
                    defaultValue={preferences.entities.join(", ")}
                    maxLength={1000}
                    placeholder="NASA, Nvidia, Ghana, Ithaca"
                  />
                  <p>
                    Add only the entities you choose. NavoX does not infer a
                    sensitive political profile.
                  </p>
                  <button disabled={busy === "preferences"} type="submit">
                    Save interests
                  </button>
                </form>
              )}
            </details>
            <details>
              <summary>Your sources</summary>
              <p>Add sources to start receiving their reports.</p>
              {sources.length === 0 && <p>No sources are available yet.</p>}
              <ul>
                {sources.map((source) => (
                  <li key={source.key}>
                    <div>
                      <strong>{source.name}</strong>
                      <small>
                        {source.domain}
                        {source.last_success_at
                          ? ` · Last checked ${newsTime(source.last_success_at)}`
                          : ""}
                      </small>
                    </div>
                    {source.status === "active" ? (
                      <div className={styles.actions}>
                        <button
                          disabled={busy === source.key}
                          onClick={() => void sourceCommand(source, "refresh")}
                          type="button"
                        >
                          Refresh
                        </button>
                        <button
                          disabled={busy === source.key}
                          onClick={() => void sourceCommand(source, "disable")}
                          type="button"
                        >
                          Remove
                        </button>
                      </div>
                    ) : (
                      <button
                        disabled={busy === source.key}
                        onClick={() => void sourceCommand(source, "activate")}
                        type="button"
                      >
                        Add source
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          </section>
        </>
      )}
    </NewsFrame>
  );
}

export function NewsStoryWorkspace({ storyId }: { storyId: string }) {
  const [story, setStory] = useState<NewsStory | null>(null);
  const [claims, setClaims] = useState<NewsClaim[]>([]);
  const [availability, setAvailability] = useState<NewsAvailability | null>(
    null,
  );
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [request, setRequest] = useState({ storyId });
  const refresh = useCallback(() => setRequest((value) => ({ ...value })), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setStory(null);
    setClaims([]);
    setAvailability(null);
    void Promise.all([
      newsRequest<NewsStory>(`/stories/${request.storyId}`, {
        signal: controller.signal,
      }),
      newsRequest<NewsClaim[]>(`/stories/${request.storyId}/claims`, {
        signal: controller.signal,
      }),
      newsRequest<NewsAvailability>("/availability", {
        signal: controller.signal,
      }).catch(() => null),
    ])
      .then(([item, evidence, available]) => {
        if (!controller.signal.aborted) {
          setStory(item);
          setClaims(evidence);
          setAvailability(available);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "This story is unavailable.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [request]);
  useEffect(() => {
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function toggle(
    operation: "save" | "follow" | "dismiss",
    enabled: boolean,
  ) {
    setBusy(true);
    setNotice("");
    try {
      await newsRequest(`/stories/${storyId}/${operation}`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      setNotice(
        operation === "dismiss"
          ? "Story hidden from your feed."
          : "Your choice is saved.",
      );
      refresh();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <NewsFrame>
      <a href="/news" className={styles.back}>
        ← All news
      </a>
      {loading ? (
        <NavoXSkeleton label="Opening the story…" />
      ) : error ? (
        <NavoXErrorState message={error} onRetry={refresh} />
      ) : (
        story && (
          <>
            <NavoXPageHeader
              title={story.headline}
              description={`${story.sources[0]?.source_name ?? "Source report"} · Published ${newsTime(story.published_at)}`}
            />
            <NavoXStatus status={story.verification_status} explain />
            <p className={styles.storyDescription}>{story.description}</p>
            <div className={styles.actions}>
              <button
                type="button"
                disabled={busy}
                aria-pressed={story.saved}
                onClick={() => void toggle("save", !story.saved)}
              >
                {story.saved ? "Saved ✓" : "Save story"}
              </button>
              <button
                type="button"
                disabled={busy}
                aria-pressed={story.followed}
                onClick={() => void toggle("follow", !story.followed)}
              >
                {story.followed ? "Following ✓" : "Follow story"}
              </button>
            </div>
            <p role="status" className={styles.notice}>
              {notice}
            </p>
            <StoryIntelligence story={story} availability={availability} />
            <NewsChat storyId={storyId} />
            <section className={styles.detailSection}>
              <h2>What we know</h2>
              {claims.length ? (
                claims.map((claim) => (
                  <NavoXCard key={claim.id}>
                    <p>
                      {claim.attributed_to && (
                        <strong>{claim.attributed_to}: </strong>
                      )}
                      {claim.text}
                    </p>
                    <NavoXStatus status={claim.status} explain />
                  </NavoXCard>
                ))
              ) : (
                <p>
                  The report is available from its original source. NavoX has
                  not yet confirmed its material claims.
                </p>
              )}
            </section>
            <section className={styles.detailSection}>
              <h2>Original reporting</h2>
              <NavoXSourceList sources={story.sources} />
            </section>
            <RelatedStories storyId={storyId} />
            <details className={styles.detailSection}>
              <summary>More about this story</summary>
              <p>Last checked {newsTime(story.retrieved_at)}.</p>
              <p>
                {story.event_started_at
                  ? `Event began ${newsTime(story.event_started_at)}.`
                  : "The event time has not been established. Publication time is shown separately."}
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => void toggle("dismiss", true)}
              >
                Hide from my feed
              </button>
            </details>
          </>
        )
      )}
    </NewsFrame>
  );
}
