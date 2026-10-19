import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ studentWhiteboard: false }));
vi.mock("../../lib/product-features", () => ({ productFeatures: flags }));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NOT_FOUND");
  },
  redirect: (path: string) => {
    throw new Error(`REDIRECT:${path}`);
  },
}));

import WhiteboardPage from "./page";

afterEach(() => {
  flags.studentWhiteboard = false;
});

describe("optional whiteboard route", () => {
  it("does not expose lessons with the deployment flag off", () => {
    expect(() => WhiteboardPage()).toThrow("NOT_FOUND");
  });

  it("redirects old links to the canonical Board route when enabled", () => {
    flags.studentWhiteboard = true;
    expect(() => WhiteboardPage()).toThrow("REDIRECT:/board");
  });
});
