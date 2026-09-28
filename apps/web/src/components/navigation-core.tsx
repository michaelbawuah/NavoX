"use client";

import { useEffect, useRef } from "react";
import styles from "./navox-landing.module.css";

/** Local, decorative geometry. No assets, network requests, or user data. */
export function NavigationCore({ paused }: { paused: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let width = 0;
    let height = 0;
    let visible = true;
    let time = 0;
    let last = 0;
    let pointerX = 0;
    let pointerY = 0;

    function draw() {
      if (!context || !canvas || !width || !height) return;
      context.clearRect(0, 0, width, height);
      const size = Math.min(width * 0.35, height * 0.36);
      const cx = width * 0.5;
      const cy = height * 0.47;
      const angle = time * 0.11 + 0.45 + pointerX * 0.15;
      const tilt = -0.45 + pointerY * 0.12;
      const project = (x: number, y: number, z: number) => {
        const rx = x * Math.cos(angle) + z * Math.sin(angle);
        const rz = z * Math.cos(angle) - x * Math.sin(angle);
        const ry = y * Math.cos(tilt) - rz * Math.sin(tilt);
        const depth = y * Math.sin(tilt) + rz * Math.cos(tilt);
        const scale = 4.8 / (4.8 - depth);
        return {
          x: cx + rx * size * scale,
          y: cy + ry * size * scale,
          z: depth,
        };
      };

      const halo = context.createRadialGradient(
        cx,
        cy,
        size * 0.2,
        cx,
        cy,
        size * 1.75,
      );
      halo.addColorStop(0, "rgba(67,132,253,.14)");
      halo.addColorStop(0.55, "rgba(58,112,235,.07)");
      halo.addColorStop(1, "rgba(30,60,130,0)");
      context.fillStyle = halo;
      context.fillRect(0, 0, width, height);

      // A bent toroidal ribbon, depth-sorted into a reflective woven sculpture.
      const lines: {
        points: { x: number; y: number; z: number }[];
        depth: number;
        band: number;
      }[] = [];
      for (let band = 0; band < 100; band++) {
        const v = (band / 100) * Math.PI * 2;
        const points = [];
        let depth = 0;
        for (let step = 0; step <= 100; step++) {
          const u = (step / 100) * Math.PI * 2;
          const radius = 0.96 + 0.27 * Math.cos(v + u * 2);
          const p = project(
            radius * Math.cos(u),
            radius * Math.sin(u),
            0.27 * Math.sin(v + u * 2) + 0.23 * Math.sin(u * 2 + time * 0.18),
          );
          points.push(p);
          depth += p.z;
        }
        lines.push({ points, depth: depth / points.length, band });
      }
      lines.sort((a, b) => a.depth - b.depth);
      for (const line of lines) {
        const light =
          0.45 +
          0.55 * Math.abs(Math.cos((line.band / 100) * Math.PI * 2 + 0.4));
        context.strokeStyle = `rgba(${Math.round(110 + light * 135)},${Math.round(155 + light * 95)},255,${0.2 + light * 0.48})`;
        context.lineWidth = light > 0.95 ? 1.3 : 0.65;
        context.beginPath();
        for (let i = 0; i < line.points.length; i++) {
          const p = line.points[i];
          if (i === 0) context.moveTo(p.x, p.y);
          else context.lineTo(p.x, p.y);
        }
        context.stroke();
      }

      // A luminous optical core, with a small reflected highlight.
      const orb = context.createRadialGradient(
        cx - size * 0.15,
        cy - size * 0.17,
        0,
        cx,
        cy,
        size * 0.42,
      );
      orb.addColorStop(0, "#f0ffff");
      orb.addColorStop(0.13, "#bcecff");
      orb.addColorStop(0.38, "#6495ca");
      orb.addColorStop(0.66, "#203f72");
      orb.addColorStop(0.88, "#08132a");
      orb.addColorStop(1, "#7299bf");
      context.beginPath();
      context.arc(cx, cy, size * 0.4, 0, Math.PI * 2);
      context.fillStyle = orb;
      context.fill();
      context.strokeStyle = "rgba(204,241,255,.65)";
      context.lineWidth = 0.7;
      context.stroke();

      for (let i = 0; i < 75; i++) {
        const t = i * 2.39996;
        const distance = 1.35 + (i % 11) * 0.047;
        const p = project(
          Math.cos(t) * distance,
          Math.sin(t) * distance * 0.69,
          Math.sin(t + time * 0.1) * 0.5,
        );
        context.fillStyle = `rgba(171,213,255,${0.15 + ((i % 5) / 5) * 0.6})`;
        context.fillRect(p.x, p.y, i % 9 === 0 ? 2 : 1, i % 9 === 0 ? 2 : 1);
      }
    }

    function tick(now: number) {
      frame = 0;
      if (!visible || document.hidden || paused || motion.matches) return;
      if (now - last >= 32) {
        time += Math.min((now - (last || now)) / 1000, 0.05);
        last = now;
        draw();
      }
      frame = requestAnimationFrame(tick);
    }
    function sync() {
      cancelAnimationFrame(frame);
      frame = 0;
      last = 0;
      draw();
      if (visible && !document.hidden && !paused && !motion.matches)
        frame = requestAnimationFrame(tick);
    }
    function resize() {
      if (!canvas || !context) return;
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      const ratio = Math.min(window.devicePixelRatio || 1, 1.75);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw();
    }
    function pointer(event: PointerEvent) {
      if (!canvas || paused || motion.matches || event.pointerType === "touch")
        return;
      const rect = canvas.getBoundingClientRect();
      pointerX = Math.max(
        -1,
        Math.min(1, (event.clientX - rect.left) / rect.width - 0.5),
      );
      pointerY = Math.max(
        -1,
        Math.min(1, (event.clientY - rect.top) / rect.height - 0.5),
      );
    }
    const resizeObserver = new ResizeObserver(resize);
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      sync();
    });
    resizeObserver.observe(canvas);
    observer.observe(canvas);
    window.addEventListener("pointermove", pointer, { passive: true });
    document.addEventListener("visibilitychange", sync);
    motion.addEventListener("change", sync);
    resize();
    sync();
    return () => {
      cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      observer.disconnect();
      window.removeEventListener("pointermove", pointer);
      document.removeEventListener("visibilitychange", sync);
      motion.removeEventListener("change", sync);
    };
  }, [paused]);

  return (
    <canvas
      ref={canvasRef}
      tabIndex={-1}
      aria-hidden="true"
      className={styles.coreCanvas}
    />
  );
}
