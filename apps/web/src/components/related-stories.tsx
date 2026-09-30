"use client";

import type { NewsStory } from "@navox/contracts";
import { useEffect, useState } from "react";
import { newsRequest, newsTime } from "../lib/news";
import { NavoXStatus } from "./navox-ui";
import styles from "./related-stories.module.css";

export function RelatedStoryList({ stories }: { stories: NewsStory[] }) {
  if (!stories.length) return null;
  return (
    <section className={styles.related} aria-labelledby="news-related-heading">
      <h2 id="news-related-heading">Related stories</h2>
      <p className={styles.note}>
        Other stories here that share a recorded claim with this one.
      </p>
      <ul>
        {stories.map((story) => (
          <li key={story.id}>
            <a href={`/news/stories/${story.id}`}>{story.headline}</a>
            <span className={styles.meta}>
              {story.sources[0]?.source_name
                ? `${story.sources[0].source_name} · `
                : ""}
              {newsTime(story.published_at)}
            </span>
            <NavoXStatus status={story.verification_status} />
          </li>
        ))}
      </ul>
    </section>
  );
}

export function RelatedStories({ storyId }: { storyId: string }) {
  const [stories, setStories] = useState<NewsStory[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    setStories([]);
    void newsRequest<NewsStory[]>(`/stories/${storyId}/related`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setStories(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setStories([]);
      });
    return () => controller.abort();
  }, [storyId]);

  return <RelatedStoryList stories={stories} />;
}
