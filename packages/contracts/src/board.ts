/** Version 1 of the implemented Board lesson language. Layout coordinates stay renderer-local. */
export type BoardPlace =
  | "top-left"
  | "next-row"
  | `below:${string}`
  | `right-of:${string}`;

export type BoardAction =
  | { id: string; type: "write"; text: string; place: BoardPlace }
  | { id: string; type: "math"; latex: string; place: BoardPlace }
  | {
      id: string;
      type: "shape";
      shape: "box" | "ellipse";
      label: string;
      place: BoardPlace;
    }
  | { id: string; type: "arrow"; from: string; to: string; label?: string }
  | {
      id: string;
      type: "highlight";
      target: string;
      style: "circle" | "underline" | "wash";
    }
  | {
      id: string;
      type: "plot";
      kind: "linear" | "quadratic" | "sine";
      domain: [number, number];
      range: [number, number];
      label: string;
      place: BoardPlace;
    };

export type BoardLessonStep = {
  id: string;
  title: string;
  say: string;
  actions: BoardAction[];
  check?: {
    question: string;
    choices: string[];
    answer: number;
    explanation: string;
  };
};

export type BoardLesson = {
  version: 1;
  id: string;
  title: string;
  subject: string;
  topic: string;
  minutes: number;
  description: string;
  steps: BoardLessonStep[];
};

/** Private authored-lesson progress. This is not evidence of answer correctness. */
export type SavedBoardSnapshot = {
  version: 1;
  lesson_id: string;
  lesson_version: string;
  title: string;
  step_index: number;
  revealed: number;
  complete: boolean;
  answers: Record<string, number>;
  marked: string[];
  strokes: { id: number; points: { x: number; y: number }[] }[];
};

export type SavedBoardSummary = {
  id: string;
  revision: number;
  course_id: string | null;
  created_at: string;
  updated_at: string;
  title: string;
  lesson_id: string;
  lesson_version: string;
  step_index: number;
  complete: boolean;
};

export type SavedBoard = SavedBoardSummary & { snapshot: SavedBoardSnapshot };

export type SavedBoardsCollection = {
  generation: number;
  revision: number;
  boards: SavedBoardSummary[];
};

export type SavedBoardCreate = {
  request_id: string;
  generation: number;
  course_id: string | null;
  snapshot: SavedBoardSnapshot;
};

export type SavedBoardUpdate = {
  request_id: string;
  revision: number;
  snapshot: SavedBoardSnapshot;
};

export type SavedBoardErase = { request_id: string; revision: number };
