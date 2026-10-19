import type {
  BoardAction,
  BoardLesson as Lesson,
  BoardLessonStep as LessonStep,
  BoardPlace as Place,
} from "@navox/contracts";
import { renderToString } from "katex";

export type {
  BoardAction,
  BoardLesson,
  BoardLesson as Lesson,
  BoardLessonStep,
  BoardLessonStep as LessonStep,
  BoardPlace,
  BoardPlace as Place,
} from "@navox/contracts";

export type PlacedAction = {
  action: BoardAction;
  stepIndex: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

export const BOARD_WIDTH = 1120;
export const MAX_BOARD_HEIGHT = 12000;
const MARGIN = 40;
const GAP = 32;
const ID = /^[a-z][a-z0-9-]{0,39}$/;
const UNSAFE_MATH =
  /\\(?:href|url|html\w*|includegraphics|def|gdef|edef|xdef|let|futurelet|newcommand|renewcommand|providecommand|require|class|style|cssId|input|include|catcode)\b/u;

function fail(message: string): never {
  throw new Error(`Invalid whiteboard lesson: ${message}`);
}

function hasControlCharacters(value: string): boolean {
  return [...value].some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return (
      (code < 32 && code !== 9 && code !== 10 && code !== 13) ||
      (code >= 127 && code <= 159)
    );
  });
}

function object(
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    (Object.getPrototypeOf(value) !== Object.prototype &&
      Object.getPrototypeOf(value) !== null)
  ) {
    fail("expected a plain object");
  }
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => !keys.includes(key))) {
    fail("unknown field");
  }
  return record;
}

function text(value: unknown, name: string, max: number): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > max ||
    hasControlCharacters(value) ||
    /[<>]/u.test(value)
  ) {
    fail(`${name} must be bounded plain text`);
  }
  return value;
}

function id(value: unknown): string {
  if (typeof value !== "string" || !ID.test(value)) fail("invalid id");
  return value;
}

function choice<T extends string>(value: unknown, values: readonly T[]): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    fail("unsupported action value");
  }
  return value as T;
}

/** Offsets refer to the narration after cue removal, ready for a future timed voice adapter. */
export function parseCues(say: string): {
  text: string;
  cues: { id: string; offset: number }[];
} {
  const cues: { id: string; offset: number }[] = [];
  let result = "";
  let cursor = 0;
  const pattern = /\[\[([a-z][a-z0-9-]{0,39})\]\]/gu;
  for (const match of say.matchAll(pattern)) {
    result += say.slice(cursor, match.index);
    cues.push({ id: match[1], offset: result.length });
    cursor = match.index + match[0].length;
  }
  result += say.slice(cursor);
  if (result.includes("[[") || result.includes("]]")) fail("malformed cue");
  return { text: result, cues };
}

export function stripCues(say: string): string {
  return parseCues(say).text.replace(/\s+/gu, " ").trim();
}

function priorReference(value: unknown, previous: Set<string>): string {
  const reference = id(value);
  if (!previous.has(reference)) fail("references must name earlier content");
  return reference;
}

function place(value: unknown, previous: Set<string>): Place {
  if (value === "top-left" || value === "next-row") return value;
  if (typeof value !== "string") fail("invalid placement");
  const match = /^(below|right-of):([a-z][a-z0-9-]{0,39})$/u.exec(value);
  if (!match) fail("placement must be a supported relative position");
  priorReference(match[2], previous);
  return value as Place;
}

function interval(value: unknown): [number, number] {
  if (
    !Array.isArray(value) ||
    value.length !== 2 ||
    value.some((item) => typeof item !== "number" || !Number.isFinite(item)) ||
    value[0] < -100 ||
    value[1] > 100 ||
    value[1] - value[0] < 0.1
  ) {
    fail("plot intervals must increase within -100 to 100");
  }
  return [value[0], value[1]];
}

function action(value: unknown, previous: Set<string>): BoardAction {
  const common = object(value, [
    "id",
    "type",
    "text",
    "place",
    "latex",
    "shape",
    "label",
    "from",
    "to",
    "target",
    "style",
    "kind",
    "domain",
    "range",
  ]);
  const actionId = id(common.id);
  switch (common.type) {
    case "write": {
      const item = object(value, ["id", "type", "text", "place"]);
      return {
        id: actionId,
        type: "write",
        text: text(item.text, "writing", 240),
        place: place(item.place, previous),
      };
    }
    case "math": {
      const item = object(value, ["id", "type", "latex", "place"]);
      if (
        typeof item.latex !== "string" ||
        !item.latex.trim() ||
        item.latex.length > 300 ||
        hasControlCharacters(item.latex) ||
        UNSAFE_MATH.test(item.latex) ||
        /<\/?[a-z][^>]*>/iu.test(item.latex)
      ) {
        fail("unsafe or oversized math");
      }
      try {
        renderToString(item.latex, {
          throwOnError: true,
          trust: false,
          strict: "error",
          maxExpand: 100,
          maxSize: 12,
          displayMode: true,
        });
      } catch {
        fail("math does not render");
      }
      return {
        id: actionId,
        type: "math",
        latex: item.latex,
        place: place(item.place, previous),
      };
    }
    case "shape": {
      const item = object(value, ["id", "type", "shape", "label", "place"]);
      return {
        id: actionId,
        type: "shape",
        shape: choice(item.shape, ["box", "ellipse"]),
        label: text(item.label, "shape label", 100),
        place: place(item.place, previous),
      };
    }
    case "arrow": {
      const item = object(value, ["id", "type", "from", "to", "label"]);
      const from = priorReference(item.from, previous);
      const to = priorReference(item.to, previous);
      if (from === to) fail("arrow endpoints must differ");
      return {
        id: actionId,
        type: "arrow",
        from,
        to,
        ...(item.label === undefined
          ? {}
          : { label: text(item.label, "arrow label", 60) }),
      };
    }
    case "highlight": {
      const item = object(value, ["id", "type", "target", "style"]);
      return {
        id: actionId,
        type: "highlight",
        target: priorReference(item.target, previous),
        style: choice(item.style, ["circle", "underline", "wash"]),
      };
    }
    case "plot": {
      const item = object(value, [
        "id",
        "type",
        "kind",
        "domain",
        "range",
        "label",
        "place",
      ]);
      return {
        id: actionId,
        type: "plot",
        kind: choice(item.kind, ["linear", "quadratic", "sine"]),
        domain: interval(item.domain),
        range: interval(item.range),
        label: text(item.label, "plot label", 80),
        place: place(item.place, previous),
      };
    }
    default:
      return fail("unsupported action type");
  }
}

/** The only input boundary for authored or future generated scripts; returns fresh data. */
export function validateLesson(value: unknown): Lesson {
  const item = object(value, [
    "version",
    "id",
    "title",
    "subject",
    "topic",
    "minutes",
    "description",
    "steps",
  ]);
  if (item.version !== 1) fail("unsupported version");
  if (
    typeof item.minutes !== "number" ||
    !Number.isInteger(item.minutes) ||
    item.minutes < 1 ||
    item.minutes > 30
  ) {
    fail("minutes must be an integer from 1 to 30");
  }
  if (
    !Array.isArray(item.steps) ||
    !item.steps.length ||
    item.steps.length > 20
  ) {
    fail("expected 1 to 20 steps");
  }
  const ids = new Set<string>();
  const previous = new Set<string>();
  let totalActions = 0;
  const uniqueId = (candidate: unknown) => {
    const result = id(candidate);
    if (ids.has(result)) fail("duplicate id");
    ids.add(result);
    return result;
  };
  const steps = item.steps.map((value): LessonStep => {
    const step = object(value, ["id", "title", "say", "actions", "check"]);
    const stepId = uniqueId(step.id);
    const say = text(step.say, "narration", 1500);
    if (!Array.isArray(step.actions) || step.actions.length > 12) {
      fail("each step permits 0 to 12 actions");
    }
    const actions = step.actions.map((value) => {
      const result = action(value, previous);
      uniqueId(result.id);
      if (result.type !== "arrow" && result.type !== "highlight") {
        previous.add(result.id);
      }
      totalActions += 1;
      if (totalActions > 80) fail("too many actions");
      return result;
    });
    const cues = parseCues(say).cues;
    if (
      cues.length !== actions.length ||
      cues.some((cue, index) => cue.id !== actions[index].id)
    ) {
      fail(
        "each action needs exactly one ordered narration cue in its own step",
      );
    }
    let check: LessonStep["check"];
    if (step.check !== undefined) {
      const question = object(step.check, [
        "question",
        "choices",
        "answer",
        "explanation",
      ]);
      if (
        !Array.isArray(question.choices) ||
        question.choices.length < 2 ||
        question.choices.length > 4
      ) {
        fail("checks need 2 to 4 choices");
      }
      const choices = question.choices.map((value) =>
        text(value, "choice", 160),
      );
      if (
        new Set(choices.map((value) => value.trim().toLowerCase())).size !==
        choices.length
      ) {
        fail("check choices must differ");
      }
      if (
        typeof question.answer !== "number" ||
        !Number.isInteger(question.answer) ||
        question.answer < 0 ||
        question.answer >= choices.length
      ) {
        fail("check answer must be a choice index");
      }
      check = {
        question: text(question.question, "question", 300),
        choices,
        answer: question.answer,
        explanation: text(question.explanation, "explanation", 500),
      };
    }
    return {
      id: stepId,
      title: text(step.title, "step title", 100),
      say,
      actions,
      ...(check ? { check } : {}),
    };
  });
  if (!totalActions) fail("lesson needs board content");
  const result: Lesson = {
    version: 1,
    id: id(item.id),
    title: text(item.title, "title", 100),
    subject: text(item.subject, "subject", 60),
    topic: text(item.topic, "topic", 100),
    minutes: item.minutes,
    description: text(item.description, "description", 400),
    steps,
  };
  // Also enforce the finite board budget at the script boundary.
  placeValidatedLesson(result);
  return result;
}

export function wrapBoardText(value: string, maxCharacters = 36): string[] {
  const lines: string[] = [];
  for (const paragraph of value.split("\n")) {
    let line = "";
    for (const word of paragraph.split(/\s+/u).filter(Boolean)) {
      // Bound even a long unbroken label; SVG text cannot wrap by itself.
      const parts = word.match(new RegExp(`.{1,${maxCharacters}}`, "gu")) ?? [];
      for (const part of parts) {
        if (line && line.length + part.length + 1 > maxCharacters) {
          lines.push(line);
          line = "";
        }
        line += `${line ? " " : ""}${part}`;
      }
    }
    lines.push(line);
  }
  return lines;
}

function dimensions(action: BoardAction): { width: number; height: number } {
  switch (action.type) {
    case "write":
      return {
        width: 480,
        height: Math.max(72, wrapBoardText(action.text).length * 30 + 28),
      };
    case "shape":
      return {
        width: 320,
        height: Math.max(112, wrapBoardText(action.label, 22).length * 28 + 48),
      };
    case "math":
      return { width: 800, height: 128 };
    case "plot":
      return { width: 480, height: 280 };
    default:
      return { width: 0, height: 0 };
  }
}

function overlaps(a: PlacedAction, b: PlacedAction): boolean {
  return (
    a.x < b.x + b.width + GAP &&
    a.x + a.width + GAP > b.x &&
    a.y < b.y + b.height + GAP &&
    a.y + a.height + GAP > b.y
  );
}

/** SVG arrow coordinates are derived from the two placed anchors, never from lesson input. */
export function arrowEndpoints(
  from: PlacedAction,
  to: PlacedAction,
): {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
} {
  const center = (item: PlacedAction) => ({
    x: item.x + item.width / 2,
    y: item.y + item.height / 2,
  });
  const a = center(from);
  const b = center(to);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const edge = (item: PlacedAction, direction: number) => {
    const origin = center(item);
    if (dx === 0 && dy === 0) return origin;
    const scale =
      item.action.type === "shape" && item.action.shape === "ellipse"
        ? 1 /
          Math.sqrt(
            (dx / (item.width / 2)) ** 2 + (dy / (item.height / 2)) ** 2,
          )
        : Math.min(
            dx === 0 ? Number.POSITIVE_INFINITY : item.width / 2 / Math.abs(dx),
            dy === 0
              ? Number.POSITIVE_INFINITY
              : item.height / 2 / Math.abs(dy),
          );
    return {
      x: origin.x + direction * dx * scale,
      y: origin.y + direction * dy * scale,
    };
  };
  const start = edge(from, 1);
  const end = edge(to, -1);
  return { x1: start.x, y1: start.y, x2: end.x, y2: end.y };
}

function placeValidatedLesson(lesson: Lesson): {
  width: number;
  height: number;
  items: PlacedAction[];
} {
  const items: PlacedAction[] = [];
  const content: PlacedAction[] = [];
  const anchors = new Map<string, PlacedAction>();
  let bottom = MARGIN;
  lesson.steps.forEach((step, stepIndex) => {
    for (const action of step.actions) {
      if (action.type === "highlight") {
        const target = anchors.get(action.target);
        if (!target) fail("missing highlight anchor");
        items.push({
          action,
          stepIndex,
          x: target.x - 10,
          y: target.y - 10,
          width: target.width + 20,
          height: target.height + 20,
        });
        continue;
      }
      if (action.type === "arrow") {
        const from = anchors.get(action.from);
        const to = anchors.get(action.to);
        if (!from || !to) fail("missing arrow anchor");
        const points = arrowEndpoints(from, to);
        items.push({
          action,
          stepIndex,
          x: Math.min(points.x1, points.x2),
          y: Math.min(points.y1, points.y2),
          width: Math.abs(points.x2 - points.x1),
          height: Math.abs(points.y2 - points.y1),
        });
        continue;
      }
      const size = dimensions(action);
      let x = MARGIN;
      let y = content.length ? bottom + GAP : MARGIN;
      if (action.place === "top-left") y = MARGIN;
      else if (action.place !== "next-row") {
        const [direction, anchorId] = action.place.split(":");
        const anchor = anchors.get(anchorId);
        if (!anchor) fail("missing placement anchor");
        x = direction === "below" ? anchor.x : anchor.x + anchor.width + GAP;
        y = direction === "below" ? anchor.y + anchor.height + GAP : anchor.y;
      }
      if (x + size.width > BOARD_WIDTH - MARGIN) {
        x = MARGIN;
        y = bottom + GAP;
      }
      const placed: PlacedAction = { action, stepIndex, x, y, ...size };
      let conflict = content.find((existing) => overlaps(placed, existing));
      while (conflict) {
        placed.y = conflict.y + conflict.height + GAP;
        conflict = content.find((existing) => overlaps(placed, existing));
      }
      if (placed.y + placed.height + MARGIN > MAX_BOARD_HEIGHT) {
        fail("board exceeds the height budget");
      }
      bottom = Math.max(bottom, placed.y + placed.height);
      content.push(placed);
      anchors.set(action.id, placed);
      items.push(placed);
    }
  });
  return { width: BOARD_WIDTH, height: Math.max(400, bottom + MARGIN), items };
}

/** Validates again so callers cannot accidentally bypass the schema by casting raw JSON. */
export function layoutLesson(lesson: Lesson): {
  width: number;
  height: number;
  items: PlacedAction[];
} {
  return placeValidatedLesson(validateLesson(lesson));
}
