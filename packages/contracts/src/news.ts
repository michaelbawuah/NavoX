export type NewsCategory = "world" | "us" | "business" | "technology" | "science";
export type NewsVerification =
  | "VERIFIED" | "CORROBORATED" | "ATTRIBUTED" | "DEVELOPING"
  | "UNCONFIRMED" | "DISPUTED" | "CONTRADICTED" | "RETRACTED";

export interface NewsSourceItem {
  id: string;
  source_id: string;
  source_name: string;
  headline: string;
  canonical_url: string;
  description: string | null;
  published_at: string;
  updated_at: string | null;
  event_started_at: string | null;
  retrieved_at: string;
  last_observed_at: string;
  expires_at: string;
  revision: number;
}

export interface NewsStory {
  id: string;
  headline: string;
  description: string | null;
  category: NewsCategory;
  verification_status: NewsVerification;
  lifecycle_status: string;
  source_count: number;
  published_at: string;
  event_started_at: string | null;
  last_updated_at: string;
  retrieved_at: string;
  version: number;
  sources: NewsSourceItem[];
  saved: boolean;
  followed: boolean;
  evidence_pending: boolean;
  ranking_basis?: "RECENCY" | "REVIEWED_IMPORTANCE";
}

export interface NewsClaim {
  id: string;
  text: string;
  attributed_to: string | null;
  status: NewsVerification;
  independent_supports: number;
  independent_contradictions: number;
  source_ids: string[];
  reason: string;
}

export interface NewsAvailability {
  feed: boolean;
  chat: boolean;
  deep_research: boolean;
  coverage_comparison: boolean;
  /** Bounded read-only views; not a promise of complete Deep Research. */
  timeline?: boolean;
  source_comparison?: boolean;
}

export interface NewsSourceOption {
  key: string;
  id: string | null;
  name: string;
  domain: string;
  category: NewsCategory;
  status: string;
  health: string;
  last_success_at: string | null;
}

export interface NewsPreferences {
  categories: NewsCategory[];
  topics: string[];
  entities: string[];
  language: string;
  region: string;
  reading_history_enabled: boolean;
}

export interface NewsStoryUpdate {
  version: number;
  change_kind: string;
  generated_at: string;
}

export type NewsFreshness = "REALTIME" | "FRESH" | "RECENT" | "HISTORICAL";
export type NewsDepth = "QUICK" | "STANDARD" | "DEEP";
export type NewsIntent =
  | "CURRENT_NEWS"
  | "TRENDING"
  /** Legacy saved turns only; this intent never queries a source. */
  | "X_TRENDS"
  | "STORY_QUESTION"
  | "VERIFY_CLAIM"
  | "TIMELINE"
  | "BACKGROUND"
  | "COVERAGE_COMPARISON"
  | "WHATS_CHANGED"
  | "DEEP_RESEARCH";

export interface NewsAnswerFact {
  item_id: string;
  text: string;
  source_name: string;
  source_url: string;
  source_id: string;
  status: NewsVerification;
  published_at?: string | null;
  event_started_at?: string | null;
}

export interface NewsAnswer {
  id: string;
  sequence: number;
  question: string;
  status: "PROCESSING" | "READY" | "UNAVAILABLE" | "SOURCES_CHANGED";
  message: string;
  facts: NewsAnswerFact[];
  as_of: string;
  actions_executed: false;
  retrieval_limited: boolean;
  source_scope: "owned_permitted_items";
  freshness: NewsFreshness;
}

export interface NewsSummaryFact {
  claim_id: string;
  text: string;
  status: NewsVerification;
  attributed_to: string | null;
  source_name: string;
  source_url: string;
}

export interface NewsSummarySection {
  heading:
    | "what_happened"
    | "why_it_matters"
    | "what_is_unclear"
    | "latest_development";
  facts: NewsSummaryFact[];
}

export interface NewsStorySummary {
  status: "READY" | "PENDING" | "UNAVAILABLE" | "SOURCES_CHANGED";
  headline: string | null;
  headline_source_url: string | null;
  headline_attribution: string | null;
  headline_status: "ATTRIBUTED";
  sections: NewsSummarySection[];
  as_of: string | null;
  actions_executed: false;
}

export interface NewsTimelineEntry {
  item_id: string;
  reported_headline: string;
  source_name: string;
  source_url: string;
  published_at: string;
  event_started_at: string | null;
  event_ended_at: string | null;
  time_basis: "event_time" | "publication_time";
  attribution_only: true;
}

export interface NewsTimeline {
  story_id: string;
  as_of: string;
  entries: NewsTimelineEntry[];
  limited: boolean;
  explanation: string;
}

export interface NewsCoverageSource {
  item_id: string;
  source_id: string;
  source_name: string;
  source_url: string;
  headline: string;
  description: string | null;
  language: string;
  published_at: string;
  claims: NewsClaim[];
}

export interface NewsCoverage {
  story_id: string;
  as_of: string;
  sources: NewsCoverageSource[];
  limited: boolean;
  explanation: string;
}

export interface NewsChangeEntry {
  version: number;
  change_kind: string;
  generated_at: string;
}

export interface NewsChanges {
  story_id: string;
  since_version: number;
  current_version: number;
  changes: NewsChangeEntry[];
  next_version: number | null;
  history_complete: boolean;
}
