import { describe, expect, it } from "vitest";
import { whiteboardLessons } from "./whiteboard-lessons";
import {
  arrowEndpoints,
  BOARD_WIDTH,
  type Lesson,
  layoutLesson,
  MAX_BOARD_HEIGHT,
  parseCues,
  stripCues,
  validateLesson,
  wrapBoardText,
} from "./whiteboard-tutor";

const minimal = (): Lesson => ({
  version: 1,
  id: "test-lesson",
  title: "A bounded lesson",
  subject: "Practice",
  topic: "Schema",
  minutes: 2,
  description: "An authored test fixture.",
  steps: [
    {
      id: "first-step",
      title: "Start",
      say: "Here is the first item. [[first-item]]",
      actions: [
        {
          id: "first-item",
          type: "write",
          text: "Start here",
          place: "top-left",
        },
      ],
    },
  ],
});

describe("closed lesson schema", () => {
  it("returns a fresh validated copy so later raw-data mutation cannot change a lesson", () => {
    const input = minimal();
    const result = validateLesson(input);
    input.steps[0].actions.length = 0;
    expect(result.steps[0].actions).toHaveLength(1);
    expect(result).not.toBe(input);
  });

  it.each([
    ["unsupported version", (lesson: Lesson) => ({ ...lesson, version: 2 })],
    [
      "unknown root field",
      (lesson: Lesson) => ({ ...lesson, code: "eval('x')" }),
    ],
    [
      "raw coordinates",
      (lesson: Lesson) => {
        Object.assign(lesson.steps[0].actions[0], { x: 10, y: 20 });
        return lesson;
      },
    ],
    [
      "cross-type keys",
      (lesson: Lesson) => {
        Object.assign(lesson.steps[0].actions[0], { latex: "x" });
        return lesson;
      },
    ],
    [
      "HTML",
      (lesson: Lesson) => {
        lesson.title = '<img src="x" onerror="alert(1)">';
        return lesson;
      },
    ],
    [
      "control characters",
      (lesson: Lesson) => {
        lesson.title = "hidden\u0000content";
        return lesson;
      },
    ],
    [
      "oversized text",
      (lesson: Lesson) => {
        lesson.description = "x".repeat(401);
        return lesson;
      },
    ],
    ["invalid id", (lesson: Lesson) => ({ ...lesson, id: "../../script" })],
    [
      "unbounded time",
      (lesson: Lesson) => ({ ...lesson, minutes: Number.POSITIVE_INFINITY }),
    ],
    ["fractional minutes", (lesson: Lesson) => ({ ...lesson, minutes: 1.5 })],
    ["no steps", (lesson: Lesson) => ({ ...lesson, steps: [] })],
    [
      "no actions",
      (lesson: Lesson) => {
        lesson.steps[0].actions = [];
        lesson.steps[0].say = "Nothing to draw";
        return lesson;
      },
    ],
    ["custom prototype", () => new Date()],
  ])("rejects %s", (_name, change) => {
    expect(() => validateLesson(change(minimal()))).toThrow(
      "Invalid whiteboard lesson",
    );
  });

  it("rejects duplicate ids across both steps and actions", () => {
    const lesson = minimal();
    lesson.steps.push({ ...lesson.steps[0], actions: [] });
    expect(() => validateLesson(lesson)).toThrow("duplicate id");
    lesson.steps.pop();
    lesson.steps[0].actions[0].id = lesson.steps[0].id;
    expect(() => validateLesson(lesson)).toThrow("duplicate id");
  });

  it.each([
    ["missing", "No cue here"],
    ["duplicate", "One [[first-item]] and again [[first-item]]"],
    ["unknown", "Other [[not-an-action]]"],
    ["malformed", "Broken [[first-item]"],
  ])("rejects %s narration cues", (_name, say) => {
    const lesson = minimal();
    lesson.steps[0].say = say;
    expect(() => validateLesson(lesson)).toThrow();
  });

  it("requires ordered local cues, including arrows and highlights", () => {
    const lesson = minimal();
    lesson.steps[0].actions.push(
      {
        id: "second-item",
        type: "shape",
        shape: "box",
        label: "Then",
        place: "right-of:first-item",
      },
      { id: "wire", type: "arrow", from: "first-item", to: "second-item" },
      { id: "mark", type: "highlight", target: "first-item", style: "circle" },
    );
    lesson.steps[0].say = "[[first-item]][[second-item]][[wire]][[mark]]";
    expect(validateLesson(lesson).steps[0].actions).toHaveLength(4);
    lesson.steps[0].say = "[[first-item]][[wire]][[second-item]][[mark]]";
    expect(() => validateLesson(lesson)).toThrow("ordered narration cue");
  });

  it.each([
    "below:future",
    "right-of:future",
    "absolute:40,50",
    "below:first-step",
  ])("rejects unsupported or forward placement %s", (position) => {
    const lesson = minimal();
    Object.assign(lesson.steps[0].actions[0], { place: position });
    expect(() => validateLesson(lesson)).toThrow();
  });

  it("rejects forward arrow targets, self arrows, and highlights of overlays", () => {
    const lesson = minimal();
    lesson.steps[0].actions.push({
      id: "wire",
      type: "arrow",
      from: "first-item",
      to: "future",
    });
    lesson.steps[0].say += " [[wire]]";
    expect(() => validateLesson(lesson)).toThrow("earlier content");
    Object.assign(lesson.steps[0].actions[1], { to: "first-item" });
    expect(() => validateLesson(lesson)).toThrow("endpoints must differ");
    lesson.steps[0].actions[1] = {
      id: "mark",
      type: "highlight",
      target: "first-item",
      style: "wash",
    };
    lesson.steps[0].actions.push({
      id: "mark-again",
      type: "highlight",
      target: "mark",
      style: "circle",
    });
    lesson.steps[0].say = "[[first-item]][[mark]][[mark-again]]";
    expect(() => validateLesson(lesson)).toThrow("earlier content");
  });

  it.each([
    "\\href{javascript:alert(1)}{click}",
    "\\url{https://example.com}",
    "\\htmlClass{secret}{x}",
    "\\includegraphics{https://example.com/pixel}",
    "\\def\\a{\\a}\\a",
    "<img src=x>",
    "\\notARealKatexCommand{x}",
    "\\frac{1}{",
  ])("rejects unsafe or unrenderable math %s", (latex) => {
    const lesson = minimal();
    lesson.steps[0].actions = [
      { id: "first-item", type: "math", latex, place: "top-left" },
    ];
    expect(() => validateLesson(lesson)).toThrow();
  });

  it("permits ordinary inequalities without permitting HTML", () => {
    const lesson = minimal();
    lesson.steps[0].actions = [
      { id: "first-item", type: "math", latex: "x < 3", place: "top-left" },
    ];
    expect(validateLesson(lesson).steps[0].actions[0].type).toBe("math");
  });

  it.each([
    { kind: "eval", domain: [-1, 1], range: [-1, 1] },
    { kind: "linear", domain: [1, -1], range: [-1, 1] },
    { kind: "linear", domain: [1, 1], range: [-1, 1] },
    { kind: "linear", domain: [-101, 1], range: [-1, 1] },
    { kind: "linear", domain: [-1, 1], range: [0, Number.NaN] },
    { kind: "linear", domain: [-1, 1, 2], range: [-1, 1] },
  ])("rejects unsafe graph input %#", (plot) => {
    const lesson = minimal();
    lesson.steps[0].actions = [
      {
        id: "first-item",
        type: "plot",
        ...plot,
        label: "Graph",
        place: "top-left",
      } as Lesson["steps"][number]["actions"][number],
    ];
    expect(() => validateLesson(lesson)).toThrow();
  });

  it.each([
    { answer: 2 },
    { answer: 0.5 },
    { choices: ["same", " SAME "] },
    { choices: ["one"] },
    { question: "<script>" },
    { answer: 0, scoreUrl: "https://example.com" },
  ])("rejects invalid check %#", (override) => {
    const lesson = minimal();
    Object.assign(lesson.steps[0], {
      check: {
        question: "Which?",
        choices: ["A", "B"],
        answer: 0,
        explanation: "Because A.",
        ...override,
      },
    });
    expect(() => validateLesson(lesson)).toThrow();
  });
});

describe("deterministic relative board layout", () => {
  for (const lesson of whiteboardLessons) {
    it(`${lesson.id} stays inside the board and never overlaps content`, () => {
      const board = layoutLesson(lesson);
      expect(board.width).toBe(BOARD_WIDTH);
      expect(board.height).toBeLessThanOrEqual(MAX_BOARD_HEIGHT);
      const content = board.items.filter(
        (item) => !["arrow", "highlight"].includes(item.action.type),
      );
      for (const [index, item] of content.entries()) {
        expect(item.x).toBeGreaterThanOrEqual(0);
        expect(item.y).toBeGreaterThanOrEqual(0);
        expect(item.x + item.width).toBeLessThanOrEqual(board.width);
        expect(item.y + item.height).toBeLessThanOrEqual(board.height);
        for (const other of content.slice(index + 1)) {
          const overlaps =
            item.x < other.x + other.width &&
            item.x + item.width > other.x &&
            item.y < other.y + other.height &&
            item.y + item.height > other.y;
          expect(
            overlaps,
            `${item.action.id} overlaps ${other.action.id}`,
          ).toBe(false);
        }
      }
      expect(layoutLesson(lesson)).toEqual(board);
      for (let count = 1; count <= lesson.steps.length; count++) {
        const partial = layoutLesson({
          ...lesson,
          steps: lesson.steps.slice(0, count),
        });
        expect(partial.items).toEqual(
          board.items.filter((item) => item.stepIndex < count),
        );
      }
    });
  }

  it("moves a second top-left request down instead of placing content on top of earlier content", () => {
    const lesson = minimal();
    lesson.steps[0].actions.push({
      id: "second",
      type: "write",
      text: "Second",
      place: "top-left",
    });
    lesson.steps[0].say += " [[second]]";
    const [first, second] = layoutLesson(lesson).items;
    expect(second.y).toBeGreaterThan(first.y + first.height);
  });

  it("wraps an overflowing right-of placement to a fresh row", () => {
    const lesson = minimal();
    lesson.steps[0].actions = [
      { id: "first-item", type: "math", latex: "x=1", place: "top-left" },
      {
        id: "second",
        type: "write",
        text: "Second",
        place: "right-of:first-item",
      },
    ];
    lesson.steps[0].say = "[[first-item]][[second]]";
    const [first, second] = layoutLesson(lesson).items;
    expect(second.x).toBe(40);
    expect(second.y).toBeGreaterThan(first.y + first.height);
  });

  it("derives arrow endpoints at the anchors' edges and overlays highlights on their target", () => {
    const board = layoutLesson(whiteboardLessons[0]);
    const memory = board.items.find((item) => item.action.id === "ece-memory");
    const input = board.items.find((item) => item.action.id === "ece-input");
    const mark = board.items.find((item) => item.action.id === "ece-emphasis");
    const rule = board.items.find((item) => item.action.id === "ece-rule");
    if (!memory || !input || !mark || !rule)
      throw new Error("missing fixture anchors");
    const points = arrowEndpoints(input, memory);
    expect(points.x1).toBe(input.x);
    expect(points.x2).toBe(memory.x + memory.width);
    expect(mark.x).toBe(rule.x - 10);
    expect(mark.width).toBe(rule.width + 20);
  });

  it("enforces action count and finite board height before rendering", () => {
    const lesson = minimal();
    lesson.steps = Array.from({ length: 20 }, (_, step) => ({
      id: `step-${step}`,
      title: "Bounded",
      say: "",
      actions: [],
    }));
    for (const [index, step] of lesson.steps.entries()) {
      step.actions = Array.from({ length: 4 }, (_, item) => ({
        id: `item-${index}-${item}`,
        type: "plot" as const,
        kind: "linear" as const,
        domain: [-1, 1] as [number, number],
        range: [-1, 1] as [number, number],
        label: "Graph",
        place: "next-row" as const,
      }));
      step.say = step.actions.map((action) => `[[${action.id}]]`).join(" ");
    }
    expect(() => validateLesson(lesson)).toThrow("height budget");
    lesson.steps[0].actions.push({
      id: "one-too-many",
      type: "write",
      text: "Extra",
      place: "top-left",
    });
    lesson.steps[0].say += " [[one-too-many]]";
    expect(() => validateLesson(lesson)).toThrow("too many actions");
  });
});

describe("authored examples and narration", () => {
  it("keeps cue positions in clean narration and strips cues for visible captions", () => {
    const result = parseCues("Hello [[first]]world. [[second]]");
    expect(result).toEqual({
      text: "Hello world. ",
      cues: [
        { id: "first", offset: 6 },
        { id: "second", offset: 13 },
      ],
    });
    expect(stripCues("Hello [[first]] world. [[second]]")).toBe("Hello world.");
    expect(wrapBoardText("x".repeat(80))).toEqual([
      "x".repeat(36),
      "x".repeat(36),
      "x".repeat(8),
    ]);
  });

  it("covers all six closed action types with 1 to 3 checks in each authored lesson", () => {
    const types = new Set(
      whiteboardLessons.flatMap((lesson) =>
        lesson.steps.flatMap((step) =>
          step.actions.map((action) => action.type),
        ),
      ),
    );
    expect([...types].sort()).toEqual([
      "arrow",
      "highlight",
      "math",
      "plot",
      "shape",
      "write",
    ]);
    for (const lesson of whiteboardLessons) {
      const checks = lesson.steps.filter((step) => step.check);
      expect(checks.length).toBeGreaterThanOrEqual(1);
      expect(checks.length).toBeLessThanOrEqual(3);
      expect(validateLesson(lesson)).toEqual(lesson);
    }
  });

  it("keeps worked numerical answers consistent with independent arithmetic", () => {
    const check = (lessonId: string, stepId: string) => {
      const result = whiteboardLessons
        .find((lesson) => lesson.id === lessonId)
        ?.steps.find((step) => step.id === stepId)?.check;
      if (!result) throw new Error("Missing practice check");
      return result.choices[result.answer];
    };
    const u = [2, 1];
    const v = [3, 4];
    const dot = u.reduce((sum, value, index) => sum + value * v[index], 0);
    expect(check("linear-algebra-dot-product", "dot-vectors")).toBe(
      String(dot),
    );
    const population = [4, 6, 8];
    const mean =
      population.reduce((sum, value) => sum + value, 0) / population.length;
    const squaredDeviations = population.reduce(
      (sum, value) => sum + (value - mean) ** 2,
      0,
    );
    expect(check("statistics-mean-variance", "stats-center")).toBe(
      String(population.reduce((sum, value) => sum + value - mean, 0)),
    );
    expect(check("statistics-mean-variance", "stats-spread")).toBe(
      `${squaredDeviations}/${population.length}`,
    );
    expect(check("physics-net-force", "force-calculate")).toBe("It halves");
    expect(12 / (2 * 3)).toBe(12 / 3 / 2);
    // d/dx [x exp(x) - exp(x)] = x exp(x), evaluated away from zero.
    const x = 1.3;
    const h = 0.00001;
    const primitive = (input: number) =>
      input * Math.exp(input) - Math.exp(input);
    expect((primitive(x + h) - primitive(x - h)) / (2 * h)).toBeCloseTo(
      x * Math.exp(x),
      7,
    );
    expect(check("calculus-parts", "parts-pick")).toBe("u = x");
    expect(check("ece-flip-flop", "ece-edge")).toBe(
      "At the next rising edge, if D remains stable",
    );
  });
});
