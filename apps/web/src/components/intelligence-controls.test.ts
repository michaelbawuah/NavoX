import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  IntelligenceControls,
  IntelligenceFeedback,
  SyncReadout,
  WorkspaceContext,
  WorkspaceReadout,
} from "./intelligence-controls";

const connection = {
  id: "connection-1",
  status: "active",
  external_email: "person@example.test",
  granted_scopes: ["openid"],
};
const readScopes = [
  "https://www.googleapis.com/auth/gmail.readonly",
  "https://www.googleapis.com/auth/calendar.events.readonly",
];

describe("connected understanding controls", () => {
  it("requires explicit read consent before offering source sync", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceControls, {
        connections: [connection],
        paused: false,
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toContain("Enable Gmail &amp; Calendar understanding");
    expect(markup).not.toContain("Sync Gmail");
    expect(markup).not.toContain("Sync Calendar");
    expect(markup).toContain("Email sending stays a separate");
  });

  it("keeps sync disabled when a previously authorized connection is unhealthy", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceControls, {
        connections: [
          {
            ...connection,
            status: "reauthorization_required",
            granted_scopes: readScopes,
          },
        ],
        paused: false,
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Sync Gmail<\/button>/);
    expect(markup).toMatch(
      /<button[^>]*disabled=""[^>]*>Sync Calendar<\/button>/,
    );
    expect(markup).toContain("Reconnect read access");
  });

  it("honors agent pause for both sources", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceControls, {
        connections: [{ ...connection, granted_scopes: readScopes }],
        paused: true,
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Sync Gmail<\/button>/);
    expect(markup).toMatch(
      /<button[^>]*disabled=""[^>]*>Sync Calendar<\/button>/,
    );
  });

  it("escapes external account text and makes no provider request on server render", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    try {
      const markup = renderToStaticMarkup(
        createElement(IntelligenceControls, {
          connections: [
            { ...connection, external_email: '<img src=x onerror="alert(1)">' },
          ],
          paused: false,
          onRefresh: vi.fn(),
        }),
      );
      expect(markup).toContain("&lt;img");
      expect(markup).not.toContain("<img");
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("offers bounded preference feedback without an approval action", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceFeedback, {
        commitmentId: "commitment-1",
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toContain("Your feedback never changes permissions");
    expect(markup).toContain("Incorrect");
    expect(markup).toContain("Too frequent");
    expect(markup).not.toContain("Approve");
  });

  it("keeps weather hidden until stored opt-in preferences are loaded", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceContext, {
        timezone: "America/New_York",
        onTimezoneChange: vi.fn(),
      }),
    );
    expect(markup).toContain("Loading time");
    expect(markup).not.toContain("Weather unavailable");
    expect(markup).not.toContain("°");
  });
});

describe("sync progress readout", () => {
  const result = {
    workflow_id: "intelligence:connection:gmail:request",
    status: "failed" as const,
    commitment_count: null,
    error: { code: "google_scope_missing" },
  };
  const props = {
    source: "gmail" as const,
    progress: result,
    pollingPaused: false,
    onCheck: vi.fn(),
    onReconnect: vi.fn(),
    reconnectDisabled: false,
  };

  it("offers explicit reconnect for a failed Google read with saved permissions", () => {
    const markup = renderToStaticMarkup(createElement(SyncReadout, props));
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("Reconnect read access");
    expect(markup).toContain("Sync failed");
    expect(markup).not.toContain("Sync complete");
  });

  it("shows an AI timeout without offering a Google reconnect", () => {
    const markup = renderToStaticMarkup(
      createElement(SyncReadout, {
        ...props,
        progress: {
          ...result,
          error: { code: "provider_request_failed", provider_code: "timeout" },
        },
      }),
    );
    expect(markup).toContain("AI request timed out");
    expect(markup).not.toContain("Reconnect read access");
    expect(markup).not.toContain("Sync complete");
  });

  it("lets the owner recheck an unknown job without claiming it finished", () => {
    const markup = renderToStaticMarkup(
      createElement(SyncReadout, {
        ...props,
        progress: { ...result, status: "unavailable", error: null },
        pollingPaused: true,
      }),
    );
    expect(markup).toContain("Check sync status");
    expect(markup).toContain("may still be running");
    expect(markup).not.toContain("Sync failed");
    expect(markup).not.toContain("Sync complete");
  });
});

describe("compact Today context", () => {
  const now = new Date("2026-09-23T02:05:07.000Z");
  const preferences = {
    timezone: "America/New_York",
    clock_format: "12h" as const,
    temperature_unit: "fahrenheit" as const,
    weather_visible: true,
    weather_city: "Ithaca, New York",
  };
  const weather = {
    status: "ready",
    temperature: 52.4,
    unit: "fahrenheit" as const,
    description: "Cloudy",
    city: "Ithaca, New York, United States",
    observed_at: "2026-09-23T02:00:00Z",
  };

  it("uses the selected timezone for the day, date, and 12-hour clock", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "America/New_York",
        preferences,
        weather,
      }),
    );
    expect(markup).toContain("Today");
    expect(markup).toContain("Tuesday");
    expect(markup).toContain("September 22");
    expect(markup).toContain("10:05:07 PM");
    expect(markup).toContain("EDT");
    expect(markup).not.toContain("Wednesday");
  });

  it("updates the date across timezones and honors the 24-hour preference", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "UTC",
        preferences: { ...preferences, timezone: "UTC", clock_format: "24h" },
        weather: null,
      }),
    );
    expect(markup).toContain("Wednesday");
    expect(markup).toContain("September 23");
    expect(markup).toContain("02:05:07");
    expect(markup).not.toContain("10:05:07 PM");
  });

  it("does not invent a time or expose weather before preferences load", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now: null,
        timezone: "America/New_York",
        preferences: null,
        weather,
      }),
    );
    expect(markup).not.toContain("Invalid Date");
    expect(markup).not.toContain("NaN");
    expect(markup).not.toContain("52°F");
    expect(markup).not.toContain("Ithaca");
    expect(markup).not.toContain("Weather unavailable");
  });

  it("hides stale weather when the user disables weather", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "America/New_York",
        preferences: { ...preferences, weather_visible: false },
        weather,
      }),
    );
    expect(markup).not.toContain("Ithaca");
    expect(markup).not.toContain("52°F");
    expect(markup).not.toContain("Weather by Open-Meteo");
    expect(markup).not.toContain("Loading weather");
  });

  it("distinguishes loading weather from an unavailable result", () => {
    const loading = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "America/New_York",
        preferences,
        weather: null,
      }),
    );
    const unavailable = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "America/New_York",
        preferences,
        weather: {
          ...weather,
          status: "unavailable",
          temperature: null,
          description: "Weather lookup could not complete.",
          observed_at: null,
        },
      }),
    );
    expect(loading).toContain("Loading weather");
    expect(loading).not.toContain("Weather unavailable");
    expect(unavailable).toContain("Weather unavailable");
    expect(unavailable).not.toContain("Loading weather");
    expect(unavailable).not.toContain("52°F");
  });

  it("retains Fahrenheit weather, observation time, and provider attribution", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "America/New_York",
        preferences,
        weather,
      }),
    );
    expect(markup).toContain("52°F");
    expect(markup).toContain("Cloudy");
    expect(markup).toContain("Ithaca, New York, United States");
    expect(markup).toContain("As of");
    expect(markup).toContain("10:00 PM");
    expect(markup).toContain('href="https://open-meteo.com/"');
    expect(markup).toContain("Weather by Open-Meteo");
  });

  it("uses the returned Celsius unit and rounds the current temperature", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "America/New_York",
        preferences: { ...preferences, temperature_unit: "celsius" },
        weather: { ...weather, temperature: 11.4, unit: "celsius" },
      }),
    );
    expect(markup).toContain("11°C");
    expect(markup).not.toContain("°F");
  });

  it("escapes external city content and keeps a ticking clock out of live regions", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceReadout, {
        now,
        timezone: "America/New_York",
        preferences,
        weather: { ...weather, city: '<img src=x onerror="alert(1)">' },
      }),
    );
    expect(markup).toContain("&lt;img");
    expect(markup).not.toContain("<img");
    expect(markup).not.toMatch(/aria-live="(?:polite|assertive)"/);
    expect(markup).not.toContain('role="status"');
  });
});
