import { readFileSync } from "node:fs";
import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  AccessStage,
  LandingAtmosphere,
  MemoryCore,
  MotionControl,
} from "./landing-art";

describe("landing artwork", () => {
  it("keeps decorative graphics out of the accessibility and keyboard trees", () => {
    const markup = renderToStaticMarkup(
      createElement(
        Fragment,
        null,
        createElement(LandingAtmosphere),
        createElement(MemoryCore),
      ),
    );
    const graphics = markup.match(/<svg\b[^>]*>/g) ?? [];

    expect(graphics.length).toBeGreaterThan(0);
    for (const graphic of graphics) {
      expect(graphic).toContain('aria-hidden="true"');
      expect(graphic).toContain('focusable="false"');
    }
    expect(markup).not.toMatch(/<(?:a|button|input|iframe)\b/);
    expect(markup).not.toContain("tabindex");
  });

  it("renders deterministically without external asset or provider requests", () => {
    const artwork = createElement(
      Fragment,
      null,
      createElement(LandingAtmosphere),
      createElement(MemoryCore),
    );
    const markup = renderToStaticMarkup(artwork);

    expect(renderToStaticMarkup(artwork)).toBe(markup);
    expect(markup).not.toMatch(/(?:src|href)=|https?:\/\//);
  });

  it("gives each crystal and orbital gradient a unique reference", () => {
    const markup = renderToStaticMarkup(
      createElement(
        Fragment,
        null,
        createElement(LandingAtmosphere),
        createElement(MemoryCore),
        createElement(MemoryCore),
      ),
    );
    const ids = [...markup.matchAll(/\bid="([^"]+)"/g)].map(
      (match) => match[1],
    );
    const references = [...markup.matchAll(/url\(#([^)]+)\)/g)].map(
      (match) => match[1],
    );

    expect(ids.length).toBeGreaterThan(0);
    expect(new Set(ids).size).toBe(ids.length);
    for (const reference of references) expect(ids).toContain(reference);
  });

  it("keeps the form outside the decorative hidden region", () => {
    const markup = renderToStaticMarkup(
      createElement(
        AccessStage,
        null,
        createElement("form", { id: "access-form" }),
      ),
    );

    expect(markup).toMatch(/<\/div><form id="access-form"><\/form><\/div>$/);
  });
});

describe("visual motion controls", () => {
  it.each([false, true])("exposes paused=%s as a toggle state", (paused) => {
    const onToggle = vi.fn();
    const button = MotionControl({ paused, onToggle });
    const markup = renderToStaticMarkup(button);

    expect(markup).toContain(`aria-pressed="${paused}"`);
    expect(markup).toContain('type="button"');
    expect(markup).toContain("Pause visual motion");
    button.props.onClick();
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("includes non-interactive artwork, pause, and reduced-motion safeguards", () => {
    const css = readFileSync(
      new URL("./landing-art.module.css", import.meta.url),
      "utf8",
    );

    expect(css).toContain("pointer-events: none");
    expect(css).toContain('data-effects-paused="true"');
    expect(css).toContain("animation-play-state: paused");
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.motion\s*\{\s*animation: none;/,
    );
  });
});
