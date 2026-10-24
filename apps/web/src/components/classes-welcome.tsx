"use client";
import styles from "./classes-redesign.module.css";
import { NoaArtwork } from "./noa-artwork";
import { useVoxRequest } from "./use-vox-request";

export function ClassesWelcome({
  disabled,
  onChoose,
  onShifts,
}: {
  disabled: boolean;
  onChoose: (mode: "manual" | "ics" | "screenshot") => void;
  onShifts: () => void;
}) {
  const vox = useVoxRequest();
  return (
    <section className={styles.welcome}>
      <header className={styles.welcomeHero}>
        <div className={styles.noa}>
          <span>all ears</span>
          <NoaArtwork size={180} mood="idle" />
        </div>
        <div>
          <h2>Let’s add your classes</h2>
          <p>
            Once they’re in, NavoX tells you what’s next, when to leave, and
            plans study time around them. Pick whatever’s fastest for you.
          </p>
        </div>
      </header>
      <div className={styles.methods}>
        <button
          type="button"
          className={styles.voiceCard}
          disabled={disabled || !vox.available || vox.busy}
          onClick={() =>
            void vox.ask(
              "Help me add my classes. Ask me for the course, days, times, room and term dates, then show me the details to review before saving anything.",
            )
          }
        >
          <span className={styles.methodIcon} aria-hidden="true">
            ♬
          </span>
          <strong>Say them to Vox</strong>
          <span>
            “I have CS 2110 Monday, Wednesday, Friday at 10:10 in Statler.”
          </span>
          <small>About 1 minute</small>
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChoose("ics")}
        >
          <span className={styles.methodIcon} aria-hidden="true">
            ⇩
          </span>
          <strong>Import a file</strong>
          <span>A calendar file from your school’s class schedule page.</span>
          <small>.ics calendar file</small>
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChoose("screenshot")}
        >
          <span className={styles.methodIcon} aria-hidden="true">
            ▣
          </span>
          <strong>Snap a photo</strong>
          <span>
            Of your printed schedule or registration page. You check it before
            saving.
          </span>
          <small>Phone camera</small>
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChoose("manual")}
        >
          <span className={styles.methodIcon} aria-hidden="true">
            ✎
          </span>
          <strong>Type them in</strong>
          <span>Course, days, time and room. One at a time.</span>
          <small>1 minute each</small>
        </button>
      </div>
      {vox.notice && <p role="status">{vox.notice}</p>}
      <div>
        <h3>
          Your week will look like this{" "}
          <small>Example · classes, labs and shifts in one view</small>
        </h3>
        <div
          className={styles.preview}
          role="img"
          aria-label="Example week, not your saved classes"
        >
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, i) => (
            <div key={day}>
              <span>{day}</span>
              {i < 6 && (
                <>
                  <i />
                  <i />
                  <i />
                </>
              )}
            </div>
          ))}
        </div>
      </div>
      <div className={styles.shiftPrompt}>
        <span>
          Have a job or regular shifts? Add them too, so NavoX can plan around
          work.
        </span>
        <button type="button" disabled={disabled} onClick={onShifts}>
          Add shifts
        </button>
      </div>
    </section>
  );
}
