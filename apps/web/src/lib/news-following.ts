import type { NewsStory } from "@navox/contracts";

export interface FollowedUpdate {
  story: NewsStory;
  since_version: number | null;
  through_version: number;
  events: { version: number; generated_at: string; description: string }[];
  history_complete: boolean;
  baseline_required: boolean;
}

export interface FollowingPage {
  entries: FollowedUpdate[];
  next_story_id: string | null;
  as_of: string;
  scope: "followed_stories";
  external_notifications_sent: false;
}

export function acknowledgeableVersion(entry: FollowedUpdate): number | null {
  const version = entry.through_version;
  if (
    !Number.isInteger(version) ||
    version < 1 ||
    version > entry.story.version
  )
    return null;
  if (entry.baseline_required && entry.since_version === null) return version;
  return entry.since_version !== null && version > entry.since_version
    ? version
    : null;
}
