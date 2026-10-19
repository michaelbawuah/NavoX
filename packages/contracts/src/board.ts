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
