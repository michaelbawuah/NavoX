import { type ReactNode, useId } from "react";
import styles from "./landing-art.module.css";

/** Decorative vector art only: no provider data, timers, or external assets. */
function IceCrystal() {
  const id = useId();

  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 120 160">
      <defs>
        <linearGradient id={`${id}-ice`} x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="#e2ffff" />
          <stop offset="0.38" stopColor="#82d8f8" stopOpacity="0.8" />
          <stop offset="1" stopColor="#254583" stopOpacity="0.15" />
        </linearGradient>
      </defs>
      <path
        d="M63 5 101 49 108 104 60 155 15 111 23 47Z"
        fill={`url(#${id}-ice)`}
        stroke="#9edcff"
        strokeOpacity="0.7"
      />
      <path d="M63 5 56 66 23 47Z" fill="#e8ffff" fillOpacity="0.6" />
      <path d="m63 5 38 44-45 17Z" fill="#b8f7ff" fillOpacity="0.35" />
      <path d="m56 66 52 38-48 51Z" fill="#406ec4" fillOpacity="0.5" />
      <path d="m15 111 41-45 4 89Z" fill="#59c3df" fillOpacity="0.25" />
      <path d="m56 66 45-17 7 55Z" fill="#e1ffff" fillOpacity="0.15" />
      <path
        d="m63 5-7 61 52 38M23 47l33 19 4 89M15 111l41-45 45-17"
        fill="none"
        stroke="#ddffff"
        strokeOpacity="0.45"
        strokeWidth="0.7"
      />
    </svg>
  );
}

export function LandingAtmosphere() {
  return (
    <div aria-hidden="true" className={styles.atmosphere}>
      <div className={styles.aurora} />
      <div className={`${styles.shard} ${styles.shardWest} ${styles.motion}`}>
        <IceCrystal />
      </div>
      <div className={`${styles.shard} ${styles.shardEast} ${styles.motion}`}>
        <IceCrystal />
      </div>
      <div className={`${styles.shard} ${styles.shardSouth} ${styles.motion}`}>
        <IceCrystal />
      </div>
      <svg
        aria-hidden="true"
        className={styles.constellation}
        focusable="false"
        viewBox="0 0 240 470"
      >
        <g fill="none" stroke="#79bfff" strokeOpacity="0.28">
          <path d="m190 24-79 83 58 79-109 91 100 78-43 82" />
          <path d="m111 107-70 31 19 139 109-91 41 97-50 72" />
          <path d="m41 138 128 48 21-162M60 277l57 160" strokeDasharray="3 7" />
        </g>
        <g fill="#b6eaff">
          <circle cx="190" cy="24" r="2" />
          <circle cx="111" cy="107" r="3" />
          <circle cx="41" cy="138" r="2" />
          <circle cx="169" cy="186" r="3" />
          <circle cx="60" cy="277" r="3" />
          <circle cx="210" cy="283" r="2" />
          <circle cx="160" cy="355" r="3" />
          <circle cx="117" cy="437" r="2" />
        </g>
        <g fill="none" stroke="#a2e9e2" strokeOpacity="0.4">
          <circle cx="111" cy="107" r="11" />
          <circle cx="60" cy="277" r="11" />
          <circle cx="160" cy="355" r="11" />
        </g>
      </svg>
      <span className={`${styles.ember} ${styles.emberOne} ${styles.motion}`} />
      <span className={`${styles.ember} ${styles.emberTwo} ${styles.motion}`} />
      <span
        className={`${styles.ember} ${styles.emberThree} ${styles.motion}`}
      />
    </div>
  );
}

export function MemoryCore() {
  const id = useId();

  return (
    <div aria-hidden="true" className={styles.core}>
      <div className={styles.coreGlow} />
      <svg
        aria-hidden="true"
        className={styles.orbits}
        focusable="false"
        viewBox="0 0 420 230"
      >
        <defs>
          <linearGradient id={`${id}-orbit`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#8bf1eb" stopOpacity="0.1" />
            <stop offset="0.48" stopColor="#85d9ff" stopOpacity="0.7" />
            <stop offset="1" stopColor="#9b96ff" stopOpacity="0.15" />
          </linearGradient>
          <linearGradient id={`${id}-ember`} x1="0" y1="0" x2="1" y2="0">
            <stop stopColor="#ff824c" stopOpacity="0" />
            <stop offset="0.6" stopColor="#ff9e64" />
            <stop offset="1" stopColor="#ffe5c2" />
          </linearGradient>
        </defs>
        <g fill="none" stroke={`url(#${id}-orbit)`}>
          <ellipse
            cx="210"
            cy="119"
            rx="171"
            ry="51"
            transform="rotate(-18 210 119)"
          />
          <ellipse
            cx="210"
            cy="119"
            rx="145"
            ry="54"
            transform="rotate(24 210 119)"
          />
          <ellipse
            cx="210"
            cy="119"
            rx="184"
            ry="76"
            strokeDasharray="2 9"
            strokeOpacity="0.45"
          />
        </g>
        <path
          d="M234 178c70-4 116-34 118-56 1-11-9-20-25-27"
          fill="none"
          stroke={`url(#${id}-ember)`}
          strokeWidth="2"
        />
        <g fill="#a6eef4">
          <circle cx="68" cy="166" r="3" />
          <circle cx="338" cy="62" r="3" />
          <circle cx="119" cy="69" r="2" />
          <circle cx="305" cy="177" r="2" />
        </g>
        <g fill="none" stroke="#87b6cd" strokeOpacity="0.45">
          <path
            d="m68 166 51-97 91 50 128-57M210 119l95 58"
            strokeDasharray="3 6"
          />
          <circle cx="68" cy="166" r="8" />
          <circle cx="338" cy="62" r="8" />
        </g>
        <path d="m327 88 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z" fill="#ffd6a8" />
        <path
          d="m100 35 1.5 5 5 1.5-5 1.5-1.5 5-1.5-5-5-1.5 5-1.5Z"
          fill="#c8f7ff"
        />
      </svg>
      <div className={`${styles.coreCrystal} ${styles.motion}`}>
        <IceCrystal />
      </div>
      <span className={styles.coreCaption}>Connected by intention.</span>
    </div>
  );
}

export function AccessStage({ children }: { children: ReactNode }) {
  return (
    <div className={styles.accessStage}>
      <MemoryCore />
      {children}
    </div>
  );
}

export function MotionControl({
  paused,
  onToggle,
}: {
  paused: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      aria-pressed={paused}
      className={styles.motionControl}
      onClick={onToggle}
      type="button"
    >
      <span aria-hidden="true">{paused ? "▷" : "Ⅱ"}</span>
      Pause visual motion
    </button>
  );
}
