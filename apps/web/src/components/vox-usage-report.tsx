"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { observeAccountSession } from "../lib/account-session";
import {
  hiddenUsageReport,
  restoreUsageReportAccess,
  type UsageReportState,
  type UsageReportStudent,
  VoxUsageReportController,
  validUsageReportDate,
} from "../lib/vox-usage-report";
import base from "./today-workspace.module.css";
import styles from "./vox-usage-report.module.css";

const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

function minutes(milliseconds: number): string {
  return (milliseconds / 60_000).toLocaleString("en", {
    maximumFractionDigits: 2,
  });
}
function Cost({ row }: { row: UsageReportStudent }) {
  if (row.costBasis === "reconciled" && row.reconciledCost !== null)
    return (
      <>
        <span>${row.reconciledCost} USD</span>
        <small>Reconciled</small>
      </>
    );
  if (row.estimatedCost !== null)
    return (
      <>
        <span>${row.estimatedCost} USD</span>
        <small>Estimate</small>
      </>
    );
  return (
    <>
      <span>Unknown</span>
      <small>Known portion: ${row.knownCost} USD</small>
      <small>
        {row.unknownComponents.toLocaleString("en")} unpriced or pending
        components
      </small>
    </>
  );
}

export function VoxUsageReportTable({ rows }: { rows: UsageReportStudent[] }) {
  return (
    <section
      className={styles.tableRegion}
      aria-label="Daily Vox usage table"
      // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need focus to scroll the wide report.
      tabIndex={0}
    >
      <table className={styles.table}>
        <caption>Daily totals · minutes and USD</caption>
        <thead>
          <tr>
            <th scope="col">Student ID</th>
            <th scope="col">Student-local day</th>
            <th scope="col">Speaking min</th>
            <th scope="col">Idle min</th>
            <th scope="col">Connected min</th>
            <th scope="col">Tokens</th>
            <th scope="col">Cost USD</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.userId}>
              <th scope="row" className={styles.studentId}>
                {row.userId}
              </th>
              <td>
                <span>{row.localDay}</span>
                <small>{row.timezone}</small>
              </td>
              <td>{minutes(row.speakingMs)}</td>
              <td>{minutes(row.idleMs)}</td>
              <td>{minutes(row.connectedMs)}</td>
              <td>
                {row.tokens.toLocaleString("en")}
                {row.reservedTokens > 0 ? (
                  <small>
                    {row.reservedTokens.toLocaleString("en")} reserved, not yet
                    used
                  </small>
                ) : null}
              </td>
              <td>
                <Cost row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function VoxUsageReport({ accountId }: { accountId: string }) {
  const [state, setState] = useState<UsageReportState>(hiddenUsageReport);
  const [dateInput, setDateInput] = useState("");
  const [dateError, setDateError] = useState("");
  const controller = useRef<VoxUsageReportController | null>(null);
  const id = useId();
  useEffect(() => {
    let active = true;
    const report = new VoxUsageReportController({
      apiBaseUrl: api,
      onChange: (next) => {
        if (active) setState(next);
      },
    });
    controller.current = report;
    const observer = observeAccountSession(
      {
        checking: () => report.clear(),
        revoking: () => report.clear(),
        restored: ({ account }) => {
          const restoredOwner = account
            ? `${account.workspace.id}:${account.id}`
            : null;
          if (restoredOwner === accountId) report.activate(accountId);
          else report.clear();
        },
        unavailable: () => report.clear(),
      },
      { apiBaseUrl: api, restoreAccess: () => restoreUsageReportAccess(api) },
    );
    return () => {
      active = false;
      observer.dispose();
      report.dispose();
      if (controller.current === report) controller.current = null;
    };
  }, [accountId]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (dateInput && !validUsageReportDate(dateInput)) {
      setDateError("Choose a real local date from 1970 through 9998.");
      return;
    }
    setDateError("");
    controller.current?.refresh(dateInput || null);
  }
  if (state.phase === "hidden") return null;
  return (
    <section className={base.controlCard} aria-labelledby={`${id}-heading`}>
      <div className={base.controlHeading}>
        <h2 id={`${id}-heading`}>Daily Vox usage</h2>
        <span className={styles.label}>Operator report</span>
      </div>
      <p className={styles.note}>
        Daily totals by student. Connected time includes idle time. Costs are
        estimates unless marked reconciled; missing prices stay unknown.
      </p>
      <form className={styles.filters} onSubmit={submit}>
        <label htmlFor={`${id}-date`}>
          Local date (optional)
          <input
            id={`${id}-date`}
            type="date"
            min="1970-01-01"
            max="9998-12-31"
            value={dateInput}
            onChange={(event) => setDateInput(event.target.value)}
            aria-describedby={
              dateError ? `${id}-date-error` : `${id}-date-help`
            }
            aria-invalid={Boolean(dateError)}
          />
        </label>
        <button type="submit" disabled={state.phase === "loading"}>
          Refresh report
        </button>
        <button
          type="button"
          disabled={state.phase === "loading"}
          onClick={() => {
            setDateInput("");
            setDateError("");
            controller.current?.refresh(null);
          }}
        >
          Student-local today
        </button>
      </form>
      <p id={`${id}-date-help`} className={styles.note}>
        {state.day === null
          ? "Showing today in each student’s saved timezone."
          : `Showing local date ${state.day} in each student’s saved timezone.`}
      </p>
      {dateError ? (
        <p id={`${id}-date-error`} role="alert">
          {dateError}
        </p>
      ) : null}
      {state.phase === "loading" ? (
        <p role="status">Loading daily Vox usage…</p>
      ) : null}
      {state.phase === "error" ? (
        <p role="alert">
          Daily Vox usage could not be loaded. Refresh to try again.
        </p>
      ) : null}
      {state.phase === "ready" ? (
        <>
          {state.students.length ? (
            <VoxUsageReportTable rows={state.students} />
          ) : (
            <p>No students were returned for this page.</p>
          )}
          <div className={styles.actions}>
            <button
              type="button"
              disabled={state.afterUserId === null}
              onClick={() => controller.current?.first()}
            >
              First page
            </button>
            <button
              type="button"
              disabled={state.nextAfterUserId === null}
              onClick={() => controller.current?.next()}
            >
              Next page
            </button>
            <span className={styles.note}>Up to 25 students per page.</span>
          </div>
        </>
      ) : null}
    </section>
  );
}
