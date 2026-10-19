"use client";

import katex from "katex";
import { useId } from "react";
import rough from "roughjs";
import type { Drawable, Options } from "roughjs/bin/core";
import { type PlacedAction, wrapBoardText } from "../lib/whiteboard-tutor";
import styles from "./whiteboard-scene.module.css";

type Scene = { width: number; height: number; items: PlacedAction[] };
export type WhiteboardSceneProps = {
  scene: Scene;
  stepIndex: number;
  /** When present, this is the accumulated set of revealed action IDs. */
  visibleActionIds?: ReadonlySet<string>;
};

const ink = "#22374a";
const sage = "#608774";
const generator = rough.generator();

function seedFor(id: string) {
  let value = 2166136261;
  for (const character of id) {
    value = Math.imul(value ^ character.charCodeAt(0), 16777619);
  }
  return value >>> 0 || 1;
}

function roughOptions(id: string, extra: Options = {}): Options {
  return {
    seed: seedFor(id),
    stroke: ink,
    strokeWidth: 1.6,
    roughness: 0.8,
    bowing: 0.4,
    disableMultiStroke: true,
    ...extra,
  };
}

function SketchPaths({ drawing }: { drawing: Drawable }) {
  return generator
    .toPaths(drawing)
    .map((path) => (
      <path
        key={`${path.d}-${path.fill}`}
        d={path.d}
        fill={path.fill || "none"}
        stroke={path.stroke}
        strokeWidth={path.strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    ));
}

function BoardText({
  text,
  x,
  y,
  width,
  height,
  centered = false,
  fontSize = 23,
  maxCharacters,
}: {
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  centered?: boolean;
  fontSize?: number;
  maxCharacters?: number;
}) {
  const lines = wrapBoardText(
    text,
    maxCharacters ?? Math.max(4, Math.floor(width / (fontSize * 0.57))),
  );
  const lineHeight = fontSize * 1.3;
  const firstBaseline = centered
    ? y + (height - lines.length * lineHeight) / 2 + fontSize
    : y + fontSize;
  return (
    <text
      x={centered ? x + width / 2 : x}
      y={firstBaseline}
      fill={ink}
      fontSize={fontSize}
      textAnchor={centered ? "middle" : "start"}
      className={styles.writing}
    >
      {lines
        .map((line, index) => ({
          text: line,
          y: firstBaseline + index * lineHeight,
        }))
        .map((line) => (
          <tspan
            key={`${line.y}-${line.text}`}
            x={centered ? x + width / 2 : x}
            y={line.y}
          >
            {line.text}
          </tspan>
        ))}
    </text>
  );
}

function MathNotation({ item }: { item: PlacedAction }) {
  if (item.action.type !== "math") return null;
  let generated: string;
  try {
    generated = katex.renderToString(item.action.latex, {
      throwOnError: true,
      trust: false,
      strict: "error",
      output: "htmlAndMathml",
      displayMode: true,
      maxExpand: 100,
      maxSize: 12,
      macros: Object.create(null) as Record<string, string>,
    });
  } catch {
    return (
      <BoardText
        text={`Equation: ${item.action.latex}`}
        x={item.x}
        y={item.y}
        width={item.width}
        height={item.height}
        fontSize={21}
      />
    );
  }
  return (
    <foreignObject
      x={item.x}
      y={item.y}
      width={item.width}
      height={item.height}
    >
      <div className={styles.mathWrapper}>
        <div
          className={styles.math}
          // biome-ignore lint/security/noDangerouslySetInnerHtml: Only KaTeX-generated markup with trust disabled and bounded expansion enters this node.
          dangerouslySetInnerHTML={{ __html: generated }}
        />
      </div>
    </foreignObject>
  );
}

function center(item: PlacedAction) {
  return { x: item.x + item.width / 2, y: item.y + item.height / 2 };
}

function edgeToward(item: PlacedAction, destination: { x: number; y: number }) {
  const origin = center(item);
  const dx = destination.x - origin.x;
  const dy = destination.y - origin.y;
  if (dx === 0 && dy === 0) return origin;
  const scale =
    item.action.type === "shape" && item.action.shape === "ellipse"
      ? 1 /
        Math.sqrt((dx / (item.width / 2)) ** 2 + (dy / (item.height / 2)) ** 2)
      : Math.min(
          dx === 0 ? Infinity : item.width / 2 / Math.abs(dx),
          dy === 0 ? Infinity : item.height / 2 / Math.abs(dy),
        );
  return { x: origin.x + dx * scale, y: origin.y + dy * scale };
}

function Arrow({
  item,
  items,
}: {
  item: PlacedAction;
  items: Map<string, PlacedAction>;
}) {
  if (item.action.type !== "arrow") return null;
  const from = items.get(item.action.from);
  const to = items.get(item.action.to);
  if (!from || !to) return null;
  const start = edgeToward(from, center(to));
  const end = edgeToward(to, center(from));
  const angle = Math.atan2(end.y - start.y, end.x - start.x);
  const head = 11;
  const path = `M ${end.x - head * Math.cos(angle - 0.5)} ${end.y - head * Math.sin(angle - 0.5)} L ${end.x} ${end.y} L ${end.x - head * Math.cos(angle + 0.5)} ${end.y - head * Math.sin(angle + 0.5)}`;
  return (
    <>
      <SketchPaths
        drawing={generator.line(
          start.x,
          start.y,
          end.x,
          end.y,
          roughOptions(item.action.id, { stroke: sage }),
        )}
      />
      <path
        d={path}
        stroke={sage}
        strokeWidth="1.8"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {item.action.label && (
        <BoardText
          text={item.action.label}
          x={(start.x + end.x) / 2 - 120}
          y={(start.y + end.y) / 2 - 30}
          width={240}
          height={28}
          fontSize={16}
          centered
        />
      )}
    </>
  );
}

function Highlight({ item }: { item: PlacedAction }) {
  if (item.action.type !== "highlight") return null;
  const { x, y, width, height } = item;
  if (item.action.style === "wash") {
    return (
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx="10"
        fill="#dce8bf"
        fillOpacity="0.45"
        className={styles.highlight}
      />
    );
  }
  const options = roughOptions(item.action.id, {
    stroke: sage,
    strokeWidth: 2,
  });
  return (
    <SketchPaths
      drawing={
        item.action.style === "circle"
          ? generator.ellipse(
              x + width / 2,
              y + height / 2,
              width,
              height,
              options,
            )
          : generator.line(
              x,
              y + height - 4,
              x + width,
              y + height - 4,
              options,
            )
      }
    />
  );
}

function Plot({ item, clipId }: { item: PlacedAction; clipId: string }) {
  if (item.action.type !== "plot") return null;
  const { domain, range, kind, label } = item.action;
  const x = item.x + 44;
  const y = item.y + 25;
  const width = item.width - 64;
  const height = item.height - 68;
  const projectX = (value: number) =>
    x + ((value - domain[0]) / (domain[1] - domain[0])) * width;
  const projectY = (value: number) =>
    y + height - ((value - range[0]) / (range[1] - range[0])) * height;
  const evaluate = (value: number) => {
    switch (kind) {
      case "linear":
        return value;
      case "quadratic":
        return value * value;
      case "sine":
        return Math.sin(value);
    }
  };
  const points = Array.from({ length: 181 }, (_, index) => {
    const value = domain[0] + ((domain[1] - domain[0]) * index) / 180;
    return `${index === 0 ? "M" : "L"} ${projectX(value)} ${projectY(evaluate(value))}`;
  }).join(" ");
  const axisX = projectX(Math.max(domain[0], Math.min(0, domain[1])));
  const axisY = projectY(Math.max(range[0], Math.min(0, range[1])));
  return (
    <>
      <defs>
        <clipPath id={clipId}>
          <rect x={x} y={y} width={width} height={height} />
        </clipPath>
      </defs>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        fill="#f4f5ee"
        stroke="#dce3d9"
        rx="3"
      />
      {[0.25, 0.5, 0.75].map((fraction) => (
        <path
          key={fraction}
          d={`M ${x + width * fraction} ${y} V ${y + height} M ${x} ${y + height * fraction} H ${x + width}`}
          fill="none"
          stroke="#dce3d9"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      <path
        d={`M ${x} ${axisY} H ${x + width} M ${axisX} ${y} V ${y + height}`}
        fill="none"
        stroke={ink}
        strokeWidth="1.2"
        vectorEffect="non-scaling-stroke"
      />
      <path
        data-plot-kind={kind}
        d={points}
        clipPath={`url(#${clipId})`}
        fill="none"
        stroke={sage}
        strokeWidth="2.3"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <text x={x} y={y + height + 20} className={styles.axisLabel}>
        {domain[0]}
      </text>
      <text
        x={x + width}
        y={y + height + 20}
        textAnchor="end"
        className={styles.axisLabel}
      >
        {domain[1]}
      </text>
      <text x={x - 9} y={y + 6} textAnchor="end" className={styles.axisLabel}>
        {range[1]}
      </text>
      <text
        x={x - 9}
        y={y + height}
        textAnchor="end"
        className={styles.axisLabel}
      >
        {range[0]}
      </text>
      <BoardText
        text={label}
        x={item.x}
        y={item.y + item.height - 25}
        width={item.width}
        height={25}
        fontSize={17}
        centered
      />
    </>
  );
}

function description(item: PlacedAction) {
  const action = item.action;
  switch (action.type) {
    case "write":
      return action.text;
    case "math":
      return `Equation: ${action.latex}`;
    case "shape":
      return `${action.shape === "box" ? "Box" : "Ellipse"}: ${action.label}`;
    case "arrow":
      return action.label || `Arrow from ${action.from} to ${action.to}`;
    case "highlight":
      return `${action.style} emphasis on ${action.target}`;
    case "plot":
      return `${action.label}. ${action.kind} graph, x from ${action.domain[0]} to ${action.domain[1]}, y from ${action.range[0]} to ${action.range[1]}.`;
  }
}

function ActionContent({
  item,
  items,
  clipId,
}: {
  item: PlacedAction;
  items: Map<string, PlacedAction>;
  clipId: string;
}) {
  const action = item.action;
  switch (action.type) {
    case "write":
      return (
        <BoardText
          text={action.text}
          x={item.x}
          y={item.y}
          width={item.width}
          height={item.height}
        />
      );
    case "math":
      return <MathNotation item={item} />;
    case "shape": {
      const options = roughOptions(action.id, {
        fill: "#eef2e9",
        fillStyle: "solid",
      });
      return (
        <>
          <SketchPaths
            drawing={
              action.shape === "box"
                ? generator.rectangle(
                    item.x,
                    item.y,
                    item.width,
                    item.height,
                    options,
                  )
                : generator.ellipse(
                    item.x + item.width / 2,
                    item.y + item.height / 2,
                    item.width,
                    item.height,
                    options,
                  )
            }
          />
          <BoardText
            text={action.label}
            x={item.x + 16}
            y={item.y + 10}
            width={item.width - 32}
            height={item.height - 20}
            centered
            fontSize={21}
            maxCharacters={22}
          />
        </>
      );
    }
    case "arrow":
      return <Arrow item={item} items={items} />;
    case "highlight":
      return <Highlight item={item} />;
    case "plot":
      return <Plot item={item} clipId={clipId} />;
  }
}

/** A deterministic SVG scene; no model calls, DOM drawing, or arbitrary code. */
export function WhiteboardScene({
  scene,
  stepIndex,
  visibleActionIds,
}: WhiteboardSceneProps) {
  const id = useId().replaceAll(":", "");
  const titleId = `${id}-board-title`;
  const descriptionId = `${id}-board-description`;
  const candidates = scene.items.filter(
    (item) =>
      item.stepIndex <= stepIndex &&
      (!visibleActionIds || visibleActionIds.has(item.action.id)),
  );
  const visibleIds = new Set(candidates.map((item) => item.action.id));
  const visible = candidates.filter((item) => {
    const action = item.action;
    if (action.type === "arrow") {
      return visibleIds.has(action.from) && visibleIds.has(action.to);
    }
    if (action.type === "highlight") return visibleIds.has(action.target);
    return true;
  });
  const items = new Map(visible.map((item) => [item.action.id, item]));
  return (
    <svg
      role="img"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className={styles.board}
      viewBox={`0 0 ${scene.width} ${scene.height}`}
      xmlns="http://www.w3.org/2000/svg"
      data-step-index={stepIndex}
    >
      <title id={titleId}>Lesson whiteboard</title>
      <desc id={descriptionId}>
        {visible.length
          ? visible.map(description).join(". ")
          : "The board is ready. Play the lesson or advance a step to reveal it."}
      </desc>
      <rect width={scene.width} height={scene.height} fill="#fbfcf6" rx="18" />
      {visible.map((item) => (
        <g
          key={item.action.id}
          data-action-id={item.action.id}
          data-action-type={item.action.type}
          data-active-step={item.stepIndex === stepIndex ? "true" : undefined}
          className={styles.action}
        >
          <title>{description(item)}</title>
          <ActionContent
            item={item}
            items={items}
            clipId={`${id}-${item.action.id}-clip`}
          />
        </g>
      ))}
    </svg>
  );
}
