"use client";

import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useId,
  useState,
  useSyncExternalStore,
} from "react";
import {
  observeWorkExportPrivacy,
  StudentWorkExportController,
  type WorkExportContext,
  type WorkExportDraft,
} from "../lib/student-work-export";
import styles from "./student-work-export.module.css";

export function WorkExportPreview({
  draft,
  context,
}: {
  draft: WorkExportDraft;
  context: WorkExportContext;
}) {
  return (
    <section className={styles.preview} aria-label="Preview your document">
      <p className={styles.eyebrow}>Preview your document</p>
      <h3>{draft.title || "Add your own title"}</h3>
      <dl className={styles.metadata}>
        <div>
          <dt>Author / attribution</dt>
          <dd>{draft.author || "Omitted"}</dd>
        </div>
        <div>
          <dt>Saved course label</dt>
          <dd>{draft.include_course ? context.course_label : "Omitted"}</dd>
        </div>
      </dl>
      <pre className={styles.textPreview}>
        {draft.text || "Your own text will appear here as you enter it."}
      </pre>
      <p className={styles.hint}>
        Plain-text preview. Final page layout is checked during formatting; the
        title page counts toward the 20-page limit.
      </p>
    </section>
  );
}
export function WorkExportFields({
  draft,
  context,
  disabled,
  onEdit,
  onInclude,
  onConfirm,
  onDownload,
}: {
  draft: WorkExportDraft;
  context: WorkExportContext;
  disabled: boolean;
  onEdit: (field: "title" | "text" | "author", value: string) => void;
  onInclude: (value: boolean) => void;
  onConfirm: (
    field: "confirmed_own_work" | "confirmed_metadata",
    value: boolean,
  ) => void;
  onDownload: () => void;
}) {
  const prefix = useId(),
    count = [...draft.text].length;
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onDownload();
  };
  return (
    <form onSubmit={submit} autoComplete="off">
      <fieldset disabled={disabled} className={styles.fields}>
        <label htmlFor={`${prefix}-title`}>
          Your title
          <input
            id={`${prefix}-title`}
            required
            maxLength={160}
            value={draft.title}
            spellCheck={false}
            onChange={(event) => onEdit("title", event.target.value)}
            placeholder="Enter a title for your own work"
          />
        </label>
        <label htmlFor={`${prefix}-text`}>
          Your own text
          <textarea
            id={`${prefix}-text`}
            required
            rows={16}
            value={draft.text}
            spellCheck={false}
            autoCorrect="off"
            autoCapitalize="off"
            onChange={(event) => onEdit("text", event.target.value)}
            placeholder="Enter the work you want to format"
          />
          <span className={styles.hint}>
            {count.toLocaleString("en-US")} / 20,000 characters · spaces and
            line breaks · plain text
          </span>
        </label>
        <label htmlFor={`${prefix}-author`}>
          Author / attribution to include (optional)
          <input
            id={`${prefix}-author`}
            maxLength={120}
            value={draft.author}
            spellCheck={false}
            onChange={(event) => onEdit("author", event.target.value)}
            placeholder="Only the attribution you choose"
          />
        </label>
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={draft.include_course}
            onChange={(event) => onInclude(event.target.checked)}
          />
          Include saved course label:{" "}
          <span className={styles.savedLabel}>{context.course_label}</span>
        </label>
        <div className={styles.confirmations}>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={draft.confirmed_own_work}
              onChange={(event) =>
                onConfirm("confirmed_own_work", event.target.checked)
              }
            />
            This text is my own work, and I have reviewed it for this export.
          </label>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={draft.confirmed_metadata}
              onChange={(event) =>
                onConfirm("confirmed_metadata", event.target.checked)
              }
            />
            I approve the title and any author or course label shown in the
            preview.
          </label>
        </div>
        <button
          type="submit"
          className={styles.primary}
          disabled={
            disabled ||
            !draft.title.trim() ||
            !draft.text.trim() ||
            !draft.confirmed_own_work ||
            !draft.confirmed_metadata
          }
        >
          Download PDF
        </button>
      </fieldset>
    </form>
  );
}
export function WorkExportPrivateSurface({
  masked,
  children,
}: {
  masked: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={styles.privateSurface} hidden={masked} inert={masked}>
      {children}
    </div>
  );
}
/** The editor mounts only after this explicit course-scoped action. */
export function StudentWorkExportEntry({
  accountId,
  courseId,
  enabled = false,
  archived = false,
}: {
  accountId: string;
  courseId: string;
  enabled?: boolean;
  archived?: boolean;
}) {
  const [opened, setOpened] = useState(false);
  if (!enabled || archived) return null;
  return opened ? (
    <StudentWorkExport
      accountId={accountId}
      courseId={courseId}
      onClose={() => setOpened(false)}
    />
  ) : (
    <section className={`${styles.panel} ${styles.card}`}>
      <h3>Put your own work into a PDF</h3>
      <p>Start with a blank editor, review your text, and download it.</p>
      <button type="button" onClick={() => setOpened(true)}>
        Format my own work
      </button>
    </section>
  );
}
export function StudentWorkExport({
  accountId,
  courseId,
  onClose,
}: {
  accountId: string;
  courseId: string;
  onClose?: () => void;
}) {
  return (
    <WorkExportSession
      key={`${accountId}:${courseId}`}
      accountId={accountId}
      courseId={courseId}
      onClose={onClose}
    />
  );
}
function WorkExportSession({
  accountId,
  courseId,
  onClose,
}: {
  accountId: string;
  courseId: string;
  onClose?: () => void;
}) {
  const [controller] = useState(
    () => new StudentWorkExportController(accountId, courseId),
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  useEffect(() => {
    const stop = observeWorkExportPrivacy(
      () => controller.clear(),
      controller.suspend,
      () => void controller.resume(),
      {
        window,
        document,
        channel:
          typeof BroadcastChannel === "undefined"
            ? null
            : new BroadcastChannel("navox-account-session"),
      },
    );
    void controller.open();
    return stop;
  }, [controller]);
  const disabled = state.busy || state.masked || state.blocked;
  const phase =
    state.phase === "opening"
      ? "Opening your editor…"
      : state.phase === "checking"
        ? "Checking access…"
        : "Formatting your PDF…";
  return (
    <section
      className={styles.panel}
      aria-label="Format your own work as a PDF"
      aria-busy={state.busy}
    >
      <header className={`${styles.card} ${styles.hero}`}>
        <div>
          <p className={styles.eyebrow}>Your work, ready to share</p>
          <h2>Format my own text as a PDF</h2>
          <p>
            Enter the text you want in the document, then review its title and
            optional attribution.
          </p>
        </div>
        <div className={styles.actions}>
          <button type="button" onClick={() => controller.clear()}>
            Clear editor
          </button>
          {onClose && (
            <button
              type="button"
              onClick={() => {
                controller.clear();
                onClose();
              }}
            >
              Close editor
            </button>
          )}
        </div>
      </header>
      <p className={styles.hint}>
        Drafts aren’t saved. Keep a copy elsewhere; this editor clears 10
        minutes after opening.
      </p>
      {state.masked && (
        <div className={styles.card} role="status">
          <h3>Your editor is hidden</h3>
          <p>
            Your editor is hidden while we check that you still have access.
          </p>
          <button
            type="button"
            disabled={state.busy || state.blocked}
            onClick={() => void controller.resume()}
          >
            {state.busy ? "Checking access…" : "Return to editor"}
          </button>
          {state.error && (
            <p role="alert" className={styles.warning}>
              {state.error}
            </p>
          )}
        </div>
      )}
      <WorkExportPrivateSurface masked={state.masked}>
        <p role="status" className={styles.notice}>
          {state.busy ? phase : state.notice}
        </p>
        {state.error && (
          <p role="alert" className={styles.warning}>
            {state.error}
          </p>
        )}
        {state.context ? (
          <>
            <div className={styles.limits}>
              <span>Up to 20,000 characters</span>
              <span>20 pages including title</span>
              <span>PDF up to 2 MiB</span>
            </div>
            <div className={styles.layout}>
              <div className={styles.card}>
                <WorkExportFields
                  draft={state.draft}
                  context={state.context}
                  disabled={disabled}
                  onEdit={controller.edit}
                  onInclude={controller.includeCourse}
                  onConfirm={controller.confirm}
                  onDownload={() => void controller.download()}
                />
              </div>
              <WorkExportPreview draft={state.draft} context={state.context} />
            </div>
            <div className={styles.card}>
              <h3>Plain text, with a supported font</h3>
              <p className={styles.muted}>
                The formatter supports a limited set of precomposed Latin,
                Greek, Cyrillic and math characters. Unsupported characters or
                writing systems produce a clear error so you can keep editing or
                use another document tool.
              </p>
              <p className={styles.hint}>
                NavoX doesn’t save your text or PDF. Downloaded as{" "}
                <strong>navox-own-work.pdf</strong>.
              </p>
            </div>
          </>
        ) : (
          <div className={styles.card}>
            <h3>Start with a blank editor</h3>
            <p>
              Opening begins a new 10-minute editing window for this course.
            </p>
            <button
              type="button"
              disabled={disabled}
              onClick={() => void controller.open()}
            >
              Open a new blank editor
            </button>
          </div>
        )}
      </WorkExportPrivateSurface>
    </section>
  );
}
