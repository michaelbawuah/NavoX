import { describe, expect, it } from "vitest";
import {
  meetingAlternatives,
  meetingCheck,
  meetingTimeLabel,
} from "./inbox-availability";
import type { AvailabilitySnapshot } from "./student-calendar-api";

const now = Date.parse("2026-10-24T12:00:00Z");
const proposed = "2026-10-26T18:00:00Z";
function snapshot(): AvailabilitySnapshot {
  return {
    input: {
      start_at: "2026-10-26T17:50:00Z",
      end_at: "2026-11-02T04:59:00Z",
      user_id: "11111111-1111-4111-8111-111111111111",
      workspace_id: "22222222-2222-4222-8222-222222222222",
      timezone: "America/New_York",
      now: new Date(now).toISOString(),
      preferences: {
        wake_time: "07:00",
        sleep_time: "23:00",
        focus_windows: [],
        never_schedule: [],
        max_study_minutes: 90,
        break_minutes: 15,
      },
      routine_confirmed: true,
      required_source_ids: ["student", "google-calendar"],
      coverage: [],
      busy: [],
      study_blocks: [],
      uncertainties: [],
      duration_minutes: 30,
      include_short_slots: true,
    },
    result: {
      timezone: "America/New_York",
      checked_at: new Date(now).toISOString(),
      scope: "provided_owned_sources_only",
      status: "complete",
      confirmed_openings: [
        {
          start_at: "2026-10-26T17:50:00Z",
          end_at: "2026-10-26T20:00:00Z",
          minutes: 130,
          reasons: [],
          unchecked_source_ids: [],
          unknown_travel_record_ids: [],
          displaced_study_block_ids: [],
        },
      ],
      provisional_openings: [],
      busy: [],
      warnings: [],
      changes_applied: false,
    },
    checked_at: new Date(now).toISOString(),
    expires_at: new Date(now + 60000).toISOString(),
    revision: {
      student_revision: "a".repeat(64),
      planning_revision: 1,
      preferences_revision: 1,
      availability_generation: 1,
      availability_revision: 1,
    },
    source_summary: [
      { source_id: "google-calendar", state: "complete", calendar_count: 1 },
    ],
    changes_applied: false,
  };
}
describe("Inbox meeting evidence", () => {
  it("uses checked local-time intervals and includes both ten-minute buffers", () => {
    expect(meetingCheck(snapshot(), proposed, 30, now)).toBe("clear");
    expect(meetingCheck(snapshot(), "2026-10-26T17:55:00Z", 30, now)).toBe(
      "unchecked",
    );
    expect(
      meetingTimeLabel(proposed, "2026-10-26T18:30:00Z", "America/New_York"),
    ).toBe("Mon, Oct 26, 2:00 PM–2:30 PM");
  });
  it("shows an actual clash and offers only confirmed alternatives", () => {
    const current = snapshot();
    current.result.busy = [
      { start_at: "2026-10-26T17:00:00Z", end_at: "2026-10-26T19:00:00Z" },
    ];
    current.result.confirmed_openings[0].start_at = "2026-10-26T19:10:00Z";
    current.result.confirmed_openings[0].minutes = 50;
    expect(meetingCheck(current, proposed, 30, now)).toBe("conflict");
    const choices = meetingAlternatives(current, 30, now);
    expect(choices).toHaveLength(1);
    expect(choices[0].start_at).toBe("2026-10-26T19:20:00.000Z");
    expect(current.changes_applied).toBe(false);
  });
  it("never turns unavailable calendars, unknown travel or an expired check into free time", () => {
    for (const reason of [
      "coverage_unknown",
      "travel_unknown",
      "routine_unconfirmed",
      "movable_study",
    ] as const) {
      const current = snapshot();
      current.result.provisional_openings = [
        { ...current.result.confirmed_openings[0], reasons: [reason] },
      ];
      current.result.confirmed_openings = [];
      current.result.status = "partial";
      expect(meetingCheck(current, proposed, 30, now)).toBe("unchecked");
      expect(meetingAlternatives(current, 30, now)).toEqual([]);
    }
    expect(meetingCheck(snapshot(), proposed, 30, now + 60000)).toBe("expired");
    expect(meetingAlternatives(snapshot(), 30, now + 60000)).toEqual([]);
  });
  it("does not silently move existing study work to make the email time fit", () => {
    const current = snapshot();
    current.result.confirmed_openings = [];
    current.input.study_blocks = [
      {
        id: "study",
        user_id: current.input.user_id,
        workspace_id: current.input.workspace_id,
        start_at: proposed,
        end_at: "2026-10-26T19:00:00Z",
        movable: true,
        calendar_backed: false,
        needs_review: false,
      },
    ];
    expect(meetingCheck(current, proposed, 30, now)).toBe("conflict");
    expect(current.input.study_blocks).toHaveLength(1);
  });
});
