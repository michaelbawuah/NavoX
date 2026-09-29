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
  x_trends: boolean;
  deep_research: boolean;
  coverage_comparison: boolean;
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
