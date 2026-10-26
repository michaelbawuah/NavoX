import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createStudentAvailabilitySuggestionsApi,
  type MeetingSuggestions,
  meetingOption,
  readMeetingSuggestions,
} from "./student-availability-suggestions-api";
import type {
  AvailabilityPreviewInput,
  AvailabilitySnapshot,
} from "./student-calendar-api";

const user = "11111111-1111-4111-8111-111111111111";
const workspace = "22222222-2222-4222-8222-222222222222";
const account = `${workspace}:${user}`;
const checked = "2026-10-22T12:00:00Z",
  end = "2026-10-22T23:00:00Z",
  expiry = "2026-10-22T12:01:00Z",
  now = Date.parse(checked);
const prefs = {
  wake_time: "07:00",
  sleep_time: "23:00",
  focus_windows: [],
  never_schedule: [],
  max_study_minutes: 90,
  break_minutes: 15,
  // The complete JSON shape of the canonical backend Preferences model.
  travel_minutes: 15,
  wake_times: null,
  alarm: {
    enabled: false,
    days: [0, 1, 2, 3, 4, 5, 6],
    smart: false,
    prep_minutes: 30,
    sound: "gentle" as const,
    snooze_minutes: 10 as const,
    skip_date: null,
  },
};
const preview: AvailabilityPreviewInput = {
  start_at: checked,
  end_at: end,
  duration_minutes: 30,
  preferences_revision: 2,
  expected_timezone: "UTC",
  routine_confirmed: true,
  travel_assumption: "unknown",
  include_short_slots: false,
};
function snapshot(): AvailabilitySnapshot {
  return {
    input: {
      start_at: checked,
      end_at: end,
      user_id: user,
      workspace_id: workspace,
      timezone: "UTC",
      now: checked,
      preferences: structuredClone(prefs),
      routine_confirmed: true,
      required_source_ids: ["student", "google:opaque"],
      coverage: ["student", "google:opaque"].map((source_id) => ({
        start_at: checked,
        end_at: end,
        user_id: user,
        workspace_id: workspace,
        source_id,
        state: "complete",
        observed_at: checked,
        fresh_until: expiry,
        revision: "fresh",
      })),
      busy: [
        {
          id: "meeting:opaque",
          source_id: "google:opaque",
          user_id: user,
          workspace_id: workspace,
          start_at: "2026-10-22T15:00:00Z",
          end_at: "2026-10-22T16:00:00Z",
          kind: "meeting",
          travel_required: true,
          travel_before_minutes: 0,
          travel_after_minutes: 0,
        },
      ],
      study_blocks: [],
      uncertainties: [],
      duration_minutes: 30,
      include_short_slots: false,
    },
    result: {
      timezone: "UTC",
      checked_at: checked,
      scope: "provided_owned_sources_only",
      status: "complete",
      confirmed_openings: [
        {
          start_at: "2026-10-22T12:30:00Z",
          end_at: "2026-10-22T14:50:00Z",
          minutes: 140,
          reasons: [],
          unchecked_source_ids: [],
          unknown_travel_record_ids: [],
          displaced_study_block_ids: [],
        },
      ],
      provisional_openings: [],
      busy: [
        { start_at: "2026-10-22T14:50:00Z", end_at: "2026-10-22T16:10:00Z" },
      ],
      warnings: [],
      changes_applied: false,
    },
    checked_at: checked,
    expires_at: expiry,
    revision: {
      student_revision: "a".repeat(64),
      planning_revision: 1,
      preferences_revision: 2,
      availability_generation: 4,
      availability_revision: 8,
    },
    source_summary: [
      { source_id: "google:opaque", state: "complete", calendar_count: 2 },
    ],
    changes_applied: false,
  };
}

function response(): MeetingSuggestions {
  return {
    changes_applied: false,
    snapshot: snapshot(),
    selection: {
      status: "ok",
      timezone: "UTC",
      duration_minutes: 30,
      suggestions: [
        {
          start_at: "2026-10-22T13:00:00Z",
          end_at: "2026-10-22T13:30:00Z",
          minutes: 30,
          reasons: ["soonest"],
          displaced_study_block_ids: [],
        },
      ],
      alternatives_available: 3,
      changes_applied: false,
    },
  };
}
const studyId = "33333333-3333-4333-8333-333333333333";
function studyResponse(): MeetingSuggestions {
  const value = response();
  const study = {
    id: studyId,
    user_id: user,
    workspace_id: workspace,
    start_at: "2026-10-22T13:00:00Z",
    end_at: "2026-10-22T14:00:00Z",
    movable: true,
    calendar_backed: false,
    needs_review: false,
  };
  value.snapshot.input.study_blocks = [study];
  value.snapshot.result.status = "partial";
  const opening = value.snapshot.result.confirmed_openings[0];
  value.snapshot.result.confirmed_openings = [];
  value.snapshot.result.provisional_openings = [
    {
      ...opening,
      reasons: ["movable_study"],
      displaced_study_block_ids: [studyId],
    },
  ];
  value.snapshot.result.warnings = [
    {
      code: "movable_study",
      source_id: null,
      record_id: studyId,
      start_at: study.start_at,
      end_at: study.end_at,
    },
  ];
  value.selection.status = "partial_information";
  value.selection.suggestions[0].displaced_study_block_ids = [studyId];
  value.selection.suggestions[0].reasons = ["soonest", "moves_study_block"];
  return value;
}
afterEach(() => vi.restoreAllMocks());
describe("fresh server ranked meeting options", () => {
  it("preserves certified exact study IDs as a separate review requirement", () => {
    const answer = readMeetingSuggestions(
      studyResponse(),
      account,
      preview,
      now,
    );
    const option = answer.selection.suggestions[0];
    expect(option.displaced_study_block_ids).toEqual([studyId]);
    expect(meetingOption(option).displaced_study_block_ids).toEqual([studyId]);
    expect(option.reasons).toContain("moves_study_block");
    expect(answer.changes_applied).toBe(false);
  });

  it("permits a certified unaffected part of a study-only provisional window", () => {
    const value = studyResponse();
    value.snapshot.input.study_blocks[0].start_at = "2026-10-22T14:20:00Z";
    value.snapshot.input.study_blocks[0].end_at = "2026-10-22T14:40:00Z";
    value.selection.suggestions[0].displaced_study_block_ids = [];
    value.selection.suggestions[0].reasons = ["soonest"];
    expect(
      readMeetingSuggestions(value, account, preview, now).selection
        .suggestions,
    ).toHaveLength(1);
  });

  it.each([
    "missing",
    "foreign",
    "started",
    "fixed",
    "review",
    "coverage",
    "travel",
    "reason",
  ])(
    "rejects unverified or inconsistent displaced study metadata: %s",
    (change) => {
      const value = studyResponse();
      const option = value.selection.suggestions[0];
      if (change === "missing") {
        option.displaced_study_block_ids = [];
        option.reasons = ["soonest"];
      }
      if (change === "foreign")
        option.displaced_study_block_ids = [
          "44444444-4444-4444-8444-444444444444",
        ];
      if (change === "started")
        value.snapshot.input.study_blocks[0].start_at = checked;
      if (change === "fixed")
        value.snapshot.input.study_blocks[0].calendar_backed = true;
      if (change === "review")
        value.snapshot.input.study_blocks[0].needs_review = true;
      if (change === "coverage")
        value.snapshot.source_summary[0].state = "partial";
      if (change === "travel")
        value.snapshot.result.provisional_openings[0].reasons.push(
          "travel_unknown",
        );
      if (change === "reason") option.reasons = ["soonest"];
      expect(() =>
        readMeetingSuggestions(value, account, preview, now),
      ).toThrow();
    },
  );
  it("uses the private read-only endpoint and preserves expiry and scheduling buffers", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(response()), {
        headers: { "content-type": "application/json" },
      }),
    );
    const api = createStudentAvailabilitySuggestionsApi(account, {
      fetch: fetcher,
      baseUrl: "https://api.example.test/api/v1",
      now: () => now,
    });
    expect(fetcher).not.toHaveBeenCalled();
    const answer = await api.suggest(
      { ...preview, purpose: "coffee" },
      new AbortController().signal,
    );
    expect(answer.snapshot.expires_at).toBe(expiry);
    expect(meetingOption(answer.selection.suggestions[0])).toEqual(
      expect.objectContaining({
        reserved_start_at: "2026-10-22T12:50:00.000Z",
        reserved_end_at: "2026-10-22T13:40:00.000Z",
      }),
    );
    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/student/availability/suggestions",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        headers: {
          "X-NavoX-Account": account,
          "Content-Type": "application/json",
        },
      }),
    );
    const payload = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(payload).toEqual({ ...preview, purpose: "coffee" });
    expect(payload).not.toHaveProperty("snapshot");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([
    "source",
    "buffer",
    "duration",
    "displacement",
    "duplicate",
    "expired",
    "owner",
    "status",
    "too-many",
    "unknown-reason",
  ])("rejects %s mismatch before showing a time", (kind) => {
    const answer = response();
    const option = answer.selection.suggestions[0];
    if (kind === "source") option.start_at = "2026-10-22T17:00:00Z";
    if (kind === "buffer") {
      option.start_at = "2026-10-22T12:30:00Z";
      option.end_at = "2026-10-22T13:00:00Z";
    }
    if (kind === "duration") option.minutes = 15;
    if (kind === "displacement")
      (option.displaced_study_block_ids as string[]).push("study");
    if (kind === "duplicate")
      answer.selection.suggestions.push(structuredClone(option));
    if (kind === "expired") answer.snapshot.expires_at = checked;
    if (kind === "owner")
      answer.snapshot.input.user_id = "33333333-3333-4333-8333-333333333333";
    if (kind === "status") answer.selection.status = "partial_information";
    if (kind === "too-many")
      answer.selection.suggestions.push(
        structuredClone(option),
        structuredClone(option),
      );
    if (kind === "unknown-reason")
      (option.reasons as string[]).push("invented");
    expect(() =>
      readMeetingSuggestions(answer, account, preview, now),
    ).toThrow();
  });
  it("accepts the server's exact prebuffer after a short network delay", () => {
    const answer = response();
    answer.selection.suggestions[0].start_at = "2026-10-22T12:40:00Z";
    answer.selection.suggestions[0].end_at = "2026-10-22T13:10:00Z";
    expect(
      readMeetingSuggestions(answer, account, preview, now + 2000).selection
        .suggestions,
    ).toHaveLength(1);
  });
  it("retains an honest partial result without inventing free time", () => {
    const answer = response();
    answer.snapshot.result.status = "partial";
    answer.selection.status = "partial_information";
    answer.selection.suggestions = [];
    expect(
      readMeetingSuggestions(answer, account, preview, now).selection.status,
    ).toBe("partial_information");
  });
  it("rejects a lunch outside local lunch hours", () => {
    const answer = response();
    answer.snapshot.result.confirmed_openings[0].end_at =
      "2026-10-22T19:00:00Z";
    answer.selection.suggestions[0].start_at = "2026-10-22T18:00:00Z";
    answer.selection.suggestions[0].end_at = "2026-10-22T18:30:00Z";
    expect(() =>
      readMeetingSuggestions(
        answer,
        account,
        { ...preview, purpose: "lunch" },
        now,
      ),
    ).toThrow();
  });
  it("aborts before issuing a request when the account lifecycle cancels it", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const controller = new AbortController();
    controller.abort();
    const api = createStudentAvailabilitySuggestionsApi(account, {
      fetch: fetcher,
      now: () => now,
    });
    await expect(api.suggest(preview, controller.signal)).rejects.toMatchObject(
      { code: "cancelled" },
    );
    expect(fetcher).not.toHaveBeenCalled();
  });
});
