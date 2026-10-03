import type {
  AssistantBlock,
  AssistantResponseState,
  CapabilityDecision,
  PlannedIntent,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { isRecord, parseCapabilityDecision } from "./validate";

const CURRENT_EXPRESSIONS = new Set([
  "today",
  "now",
  "right now",
  "currently",
  "this morning",
  "this afternoon",
  "this evening",
]);

export type WeatherReading =
  | { status: "disabled"; unit: "celsius" | "fahrenheit" }
  | { status: "unavailable"; unit: "celsius" | "fahrenheit" }
  | {
      status: "ready";
      unit: "celsius" | "fahrenheit";
      city: string;
      temperature: number;
      description: string;
      observed_at: string;
    };

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "Weather returned an unreadable current observation.",
  );
}

/** `undefined` is ungrounded or a non-current request; `null` uses the saved city. */
export function weatherSelector(
  intent: PlannedIntent,
): string | null | undefined {
  if (intent.kind !== "weather.read") return undefined;
  if (
    intent.time.expression !== null &&
    !CURRENT_EXPRESSIONS.has(
      intent.time.expression.trim().toLocaleLowerCase("en-US"),
    )
  )
    return undefined;
  if (intent.entity.kind === "NONE") return null;
  const value = intent.entity.value?.trim() ?? "";
  if (
    value.length < 2 ||
    value.length > 128 ||
    !intent.question
      .toLocaleLowerCase("en-US")
      .includes(value.toLocaleLowerCase("en-US"))
  )
    return undefined;
  return value;
}

export function parseWeather(payload: unknown, now: Date): WeatherReading {
  if (!isRecord(payload)) unreadable();
  const unit = payload.unit;
  if (unit !== "celsius" && unit !== "fahrenheit") unreadable();
  if (payload.status === "disabled" || payload.status === "unavailable") {
    if (payload.temperature !== null && payload.temperature !== undefined)
      unreadable();
    return { status: payload.status, unit };
  }
  if (payload.status !== "ready") unreadable();
  const city = payload.city;
  const description = payload.description;
  const observed = payload.observed_at;
  const temperature = payload.temperature;
  if (
    typeof city !== "string" ||
    !city.trim() ||
    city.length > 256 ||
    typeof description !== "string" ||
    !description.trim() ||
    description.length > 128 ||
    typeof observed !== "string" ||
    observed.length > 64 ||
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(observed) ||
    typeof temperature !== "number" ||
    !Number.isFinite(temperature)
  )
    unreadable();
  const time = Date.parse(observed);
  if (
    !Number.isFinite(time) ||
    time > now.getTime() + 60_000 ||
    time < now.getTime() - 45 * 60_000
  )
    unreadable();
  const minimum = unit === "celsius" ? -100 : -148;
  const maximum = unit === "celsius" ? 60 : 140;
  if (temperature < minimum || temperature > maximum) unreadable();
  return {
    status: "ready",
    unit,
    city,
    description,
    temperature,
    observed_at: observed,
  };
}

function decision(
  state: AssistantResponseState,
  reason: string,
): CapabilityDecision {
  return parseCapabilityDecision({
    kind: state === "READY" ? "DELEGATE" : "CLARIFY",
    capability_id: "weather.read",
    target: "workspace.weather",
    reason,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: state,
  });
}

export function weatherAnswer(
  reading: WeatherReading,
  requestedCity: string | null,
): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  if (reading.status === "disabled") {
    return {
      state: "CLARIFY",
      decision: decision("CLARIFY", "weather.city_not_enabled"),
      blocks: [
        {
          kind: "ANSWER",
          text: "Choose a weather city in your workspace preferences before I can give a live reading.",
        },
      ],
    };
  }
  if (reading.status === "unavailable") {
    return {
      state: "UNAVAILABLE",
      decision: parseCapabilityDecision({
        kind: "UNAVAILABLE",
        capability_id: "weather.read",
        target: "workspace.weather",
        reason: "weather.source_unavailable",
        requires_approval: false,
        action_state: "NONE",
        action_id: null,
        response_state: "UNAVAILABLE",
      }),
      blocks: [
        {
          kind: "NOTICE",
          state: "UNAVAILABLE",
          text: "A current weather reading is unavailable right now.",
        },
      ],
    };
  }
  if (
    requestedCity !== null &&
    !(
      reading.city.toLocaleLowerCase("en-US") ===
        requestedCity.toLocaleLowerCase("en-US") ||
      reading.city
        .toLocaleLowerCase("en-US")
        .startsWith(`${requestedCity.toLocaleLowerCase("en-US")},`)
    )
  ) {
    return {
      state: "CLARIFY",
      decision: decision("CLARIFY", "weather.city_mismatch"),
      blocks: [
        {
          kind: "ANSWER",
          text: `Your configured weather location is ${reading.city}. Which city did you mean?`,
        },
      ],
    };
  }
  const symbol = reading.unit === "celsius" ? "°C" : "°F";
  return {
    state: "READY",
    decision: decision("READY", "weather.current"),
    blocks: [
      {
        kind: "ANSWER",
        text: `Current weather in ${reading.city}: ${reading.temperature}${symbol}, ${reading.description.toLocaleLowerCase("en-US")}.`,
      },
      {
        kind: "DETAILS",
        lines: [
          `Observed at ${reading.observed_at}.`,
          "Location and units come from your workspace weather preferences.",
        ],
      },
    ],
  };
}
