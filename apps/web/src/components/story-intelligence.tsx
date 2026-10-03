"use client";

import type {
  NewsAvailability,
  NewsChanges,
  NewsCoverage,
  NewsStory,
  NewsStorySummary,
  NewsTimeline,
} from "@navox/contracts";
import { useEffect, useState } from "react";
import {
  newsRequest,
  newsTime,
  newsUpdateLabels,
  safeNewsUrl,
} from "../lib/news";
import { NavoXStatus } from "./navox-ui";
import styles from "./news-workspace.module.css";

const sectionLabels: Record<
  NewsStorySummary["sections"][number]["heading"],
  string
> = {
  what_happened: "What happened",
  why_it_matters: "Why it matters",
  what_is_unclear: "What is still unclear",
  latest_development: "Latest development",
};

interface DisclosureState<T> {
  data: T | null;
  loading: boolean;
  error: string;
}
function useDisclosureData<T>(path: string) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<DisclosureState<T>>({
    data: null,
    loading: false,
    error: "",
  });

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setState({ data: null, loading: true, error: "" });
    void newsRequest<T>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted)
          setState({ data, loading: false, error: "" });
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setState({
            data: null,
            loading: false,
            error:
              reason instanceof Error
                ? reason.message
                : "This view is unavailable.",
          });
      });
    return () => controller.abort();
  }, [open, path]);

  return { open, setOpen, ...state };
}

function SourceLink({ url, label }: { url: string; label: string }) {
  const safe = safeNewsUrl(url);
  return safe ? (
    <a
      href={safe}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
    >
      {label} ↗
    </a>
  ) : (
    <span>{label}</span>
  );
}

export function StorySummaryView({ summary }: { summary: NewsStorySummary }) {
  if (summary.status !== "READY")
    return (
      <section
        className={styles.summaryPanel}
        aria-labelledby="news-summary-heading"
      >
        <h2 id="news-summary-heading">NavoX summary</h2>
        <p>
          {summary.status === "PENDING"
            ? "NavoX is preparing the summary. Check back shortly."
            : summary.status === "SOURCES_CHANGED"
              ? "This story has changed. Refresh to check for an updated summary."
              : "A detailed summary isn’t available for this story yet. You can read the full report at the original source."}
        </p>
      </section>
    );

  return (
    <section
      className={styles.summaryPanel}
      aria-labelledby="news-summary-heading"
    >
      <h2 id="news-summary-heading">NavoX summary</h2>
      {summary.as_of && (
        <p className={styles.detailMeta}>
          Sources checked {newsTime(summary.as_of)}
        </p>
      )}
      {summary.headline &&
        summary.headline_attribution &&
        summary.headline_source_url && (
          <p className={styles.summaryHeadline}>
            <strong>{summary.headline}</strong>{" "}
            <SourceLink
              url={summary.headline_source_url}
              label={summary.headline_attribution}
            />
            <NavoXStatus status={summary.headline_status} />
          </p>
        )}
      {summary.sections.map((section) => (
        <section key={section.heading} className={styles.summarySection}>
          <h3>{sectionLabels[section.heading]}</h3>
          <ul>
            {section.facts.map((fact) => (
              <li key={fact.claim_id}>
                <p>
                  {fact.attributed_to && (
                    <strong>{fact.attributed_to}: </strong>
                  )}
                  {fact.text}
                </p>
                <div className={styles.sourceRow}>
                  <NavoXStatus status={fact.status} />
                  <SourceLink url={fact.source_url} label={fact.source_name} />
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </section>
  );
}

type StoryIntelligenceProps = {
  story: NewsStory;
  availability: NewsAvailability | null;
};

export function StoryIntelligence(props: StoryIntelligenceProps) {
  const { story, availability } = props;
  const scope = `${story.id}:${story.version}:${story.retrieved_at}:${availability?.timeline}:${availability?.source_comparison}`;
  return <StoryIntelligenceScope key={scope} {...props} />;
}

function StoryIntelligenceScope({
  story,
  availability,
}: StoryIntelligenceProps) {
  const [summary, setSummary] = useState<NewsStorySummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setSummary(null);
    setSummaryLoading(true);
    void newsRequest<NewsStorySummary>(`/stories/${story.id}/summary`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setSummary(value);
          setSummaryLoading(false);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setSummary(null);
          setSummaryLoading(false);
        }
      });
    return () => controller.abort();
  }, [story.id]);

  const timeline = useDisclosureData<NewsTimeline>(
    `/stories/${story.id}/timeline?limit=50`,
  );
  const coverage = useDisclosureData<NewsCoverage>(
    `/stories/${story.id}/coverage?limit=50`,
  );
  const [sinceVersion, setSinceVersion] = useState(0);
  const changes = useDisclosureData<NewsChanges>(
    `/stories/${story.id}/changes?since_version=${sinceVersion}&limit=50`,
  );

  return (
    <>
      {summary ? (
        <StorySummaryView summary={summary} />
      ) : (
        <section
          className={styles.summaryPanel}
          aria-labelledby="news-summary-heading"
        >
          <h2 id="news-summary-heading">NavoX summary</h2>
          <p>
            {summaryLoading
              ? "Opening the summary…"
              : "We couldn’t load the summary. Refresh this story, or read the original reporting below."}
          </p>
        </section>
      )}
      <section className={styles.detailSection} aria-label="Explore this story">
        <h2>Explore this story</h2>
        <p>
          Open a view when you need more detail. Source wording and uncertainty
          stay visible.
        </p>

        {availability?.timeline && (
          <details
            open={timeline.open}
            onToggle={(event) => timeline.setOpen(event.currentTarget.open)}
          >
            <summary>Timeline</summary>
            {timeline.loading && (
              <p>Building the timeline from current reports…</p>
            )}
            {timeline.error && <p role="alert">{timeline.error}</p>}
            {timeline.data && (
              <>
                <p className={styles.detailMeta}>{timeline.data.explanation}</p>
                {timeline.data.limited && (
                  <p>
                    Showing up to 50 available reports, not a complete event
                    history.
                  </p>
                )}
                <ol className={styles.researchList}>
                  {timeline.data.entries.map((entry) => (
                    <li key={entry.item_id}>
                      <time
                        dateTime={entry.event_started_at ?? entry.published_at}
                      >
                        {newsTime(entry.event_started_at ?? entry.published_at)}
                      </time>
                      <p>{entry.reported_headline}</p>
                      <small>
                        {entry.time_basis === "event_time"
                          ? "Source-reported event time"
                          : "Publication time"}
                      </small>
                      <div>
                        <SourceLink
                          url={entry.source_url}
                          label={entry.source_name}
                        />
                      </div>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </details>
        )}

        {availability?.source_comparison && (
          <details
            open={coverage.open}
            onToggle={(event) => coverage.setOpen(event.currentTarget.open)}
          >
            <summary>Compare sources</summary>
            {coverage.loading && <p>Comparing the current source reports…</p>}
            {coverage.error && <p role="alert">{coverage.error}</p>}
            {coverage.data && (
              <>
                <p className={styles.detailMeta}>{coverage.data.explanation}</p>
                {coverage.data.limited && (
                  <p>Showing up to 50 available source reports.</p>
                )}
                <div className={styles.coverageGrid}>
                  {coverage.data.sources.map((source) => (
                    <article key={source.item_id}>
                      <h3>{source.headline}</h3>
                      {source.description && <p>{source.description}</p>}
                      <p className={styles.detailMeta}>
                        Published {newsTime(source.published_at)}
                      </p>
                      <SourceLink
                        url={source.source_url}
                        label={source.source_name}
                      />
                      {source.claims.length > 0 && (
                        <ul>
                          {source.claims.map((claim) => (
                            <li key={claim.id}>
                              <p>{claim.text}</p>
                              <NavoXStatus status={claim.status} />
                            </li>
                          ))}
                        </ul>
                      )}
                    </article>
                  ))}
                </div>
              </>
            )}
          </details>
        )}

        <details
          open={changes.open}
          onToggle={(event) => changes.setOpen(event.currentTarget.open)}
        >
          <summary>What changed</summary>
          {changes.loading && <p>Checking the story’s recorded changes…</p>}
          {changes.error && <p role="alert">{changes.error}</p>}
          {changes.data && (
            <>
              {changes.data.changes.length ? (
                <ol className={styles.researchList}>
                  {changes.data.changes.map((change) => (
                    <li key={change.version}>
                      <time dateTime={change.generated_at}>
                        {newsTime(change.generated_at)}
                      </time>
                      <p>
                        {newsUpdateLabels[change.change_kind] ??
                          "Story updated"}
                      </p>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>No recorded changes after this story’s starting version.</p>
              )}
              {changes.data.next_version !== null && (
                <button
                  type="button"
                  onClick={() =>
                    setSinceVersion(changes.data?.next_version ?? 0)
                  }
                >
                  Show later changes
                </button>
              )}
              {sinceVersion > 0 && (
                <button type="button" onClick={() => setSinceVersion(0)}>
                  Back to first changes
                </button>
              )}
              {!changes.data.history_complete && (
                <p className={styles.detailMeta}>
                  The available change history is incomplete.
                </p>
              )}
            </>
          )}
        </details>
      </section>
    </>
  );
}
