import { describe, expect, it } from "vitest";
import { parseWeather, weatherAnswer, weatherSelector } from "./weather";

const NOW = new Date("2026-09-30T12:00:00Z");
const READING = {
  status: "ready",
  temperature: 18,
  unit: "celsius",
  description: "Cloudy",
  city: "Ithaca, New York, United States",
  observed_at: "2026-09-30T11:45:00Z",
};

describe("current workspace weather", () => {
  it("requires a grounded current request and never guesses a city", () => {
    const intent = {
      kind: "weather.read",
      question: "What's the weather in Ithaca today?",
      entity: { kind: "TOPIC", value: "Ithaca", confidence: 0.9 },
      time: { kind: "RELATIVE", expression: "today", confidence: 0.9 },
    } as Parameters<typeof weatherSelector>[0];
    expect(weatherSelector(intent)).toBe("Ithaca");
    expect(
      weatherSelector({
        ...intent,
        entity: { ...intent.entity, value: "Boston" },
      }),
    ).toBeUndefined();
    expect(
      weatherSelector({
        ...intent,
        time: { ...intent.time, expression: "tomorrow" },
      }),
    ).toBeUndefined();
  });

  it("rejects stale or malformed current readings", () => {
    expect(parseWeather(READING, NOW)).toMatchObject({
      status: "ready",
      city: READING.city,
    });
    expect(() =>
      parseWeather({ ...READING, observed_at: "2026-09-30T10:00:00Z" }, NOW),
    ).toThrow();
    expect(() =>
      parseWeather({ ...READING, temperature: Number.POSITIVE_INFINITY }, NOW),
    ).toThrow();
    expect(() => parseWeather({ ...READING, city: "" }, NOW)).toThrow();
  });

  it("distinguishes disabled, unavailable and configured-city mismatch", () => {
    expect(
      weatherAnswer(
        parseWeather({ status: "disabled", unit: "celsius" }, NOW),
        null,
      ).state,
    ).toBe("CLARIFY");
    expect(
      weatherAnswer(
        parseWeather({ status: "unavailable", unit: "celsius" }, NOW),
        null,
      ).state,
    ).toBe("UNAVAILABLE");
    expect(weatherAnswer(parseWeather(READING, NOW), "Boston").state).toBe(
      "CLARIFY",
    );
    expect(weatherAnswer(parseWeather(READING, NOW), "New York").state).toBe(
      "CLARIFY",
    );
    expect(weatherAnswer(parseWeather(READING, NOW), "Ithaca").state).toBe(
      "READY",
    );
  });
});
