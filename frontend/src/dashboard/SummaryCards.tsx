import { getReport, listFdrs, listInstitutions, listUsers, type FdrStatus, type ReportOut } from "../lib/api";
import { formatInr } from "../lib/format";
import { canReview } from "../lib/roles";
import { describeError, useAsync } from "../lib/useAsync";
import { RetryButton } from "./Panel";

interface Metric {
  key: string;
  label: string;
  hint?: string;
  load: (signal: AbortSignal) => Promise<number>;
}

// Every count is the backend's own `total`, which is computed after role/institution scoping and
// before pagination. A page of one row is requested only to keep the response small; nothing is
// ever counted from the rows that come back.
const fdrCount = (status?: FdrStatus) => (signal: AbortSignal) =>
  listFdrs({ status, page_size: 1 }, signal).then((r) => r.total);

function metricsFor(role: string): Metric[] {
  const institutionUser = role === "INSTITUTION_USER";
  const metrics: Metric[] = [
    {
      key: "total",
      label: "FDRs visible to you",
      hint: institutionUser ? undefined : "Excludes drafts and withdrawn records for your role.",
      load: fdrCount(),
    },
    { key: "submitted", label: "Awaiting review", hint: "Status: Submitted", load: fdrCount("SUBMITTED") },
    { key: "correction", label: "Correction requested", load: fdrCount("CORRECTION_REQUESTED") },
    { key: "accepted", label: "Accepted", hint: "Accepted after human review; not bank-verified.", load: fdrCount("ACCEPTED") },
    { key: "rejected", label: "Rejected", load: fdrCount("REJECTED") },
  ];
  if (institutionUser) {
    metrics.push(
      { key: "draft", label: "Drafts", load: fdrCount("DRAFT") },
      { key: "withdrawn", label: "Withdrawn", load: fdrCount("WITHDRAWN") },
    );
  } else {
    metrics.push({
      key: "institutions",
      label: "Institutions in scope",
      load: (signal) => listInstitutions({ limit: 1 }, signal).then((r) => r.total),
    });
  }
  if (role === "ADMIN") {
    metrics.push({
      key: "users",
      label: "User accounts",
      load: (signal) => listUsers({ limit: 1 }, signal).then((r) => r.total),
    });
  }
  return metrics;
}

const CARD = "rounded border border-slate-200 bg-white p-4";

function MetricCard({ metric }: { metric: Metric }) {
  const { state, reload } = useAsync(metric.load);
  return (
    <div className={CARD} aria-busy={state.status === "loading"} data-testid={`metric-${metric.key}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{metric.label}</p>
      {state.status === "loading" && (
        <p role="status" className="mt-2 text-sm text-slate-500">
          Loading…
        </p>
      )}
      {state.status === "success" && (
        <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-900" aria-label={`${metric.label}: ${state.data}`}>
          {state.data.toLocaleString("en-IN")}
        </p>
      )}
      {state.status === "error" && (
        <div role="alert" className="mt-2 space-y-2">
          <p className="text-sm font-medium text-red-800">Unavailable</p>
          <p className="text-xs text-slate-600">{describeError(state.error)}</p>
          <RetryButton onClick={reload} />
        </div>
      )}
      {metric.hint && <p className="mt-2 text-xs text-slate-500">{metric.hint}</p>}
    </div>
  );
}

interface ReportMetric {
  key: string;
  label: string;
  hint?: string;
  value: (r: ReportOut) => string | null; // null means "unavailable for this role or database state"
  reason?: (r: ReportOut) => string | undefined;
}

const bucket = (r: ReportOut, key: string) => r.maturities.find((m) => m.key === key)?.count ?? 0;
const total = (rows: Array<{ count: number }>) => rows.reduce((sum, row) => sum + row.count, 0);

function reportMetrics(role: string): ReportMetric[] {
  const metrics: ReportMetric[] = [
    { key: "principal", label: "Principal of live FDRs", hint: "Submitted, returned or accepted. As submitted; not bank-verified.", value: (r) => formatInr(r.principal_live_total) },
    { key: "maturing", label: "Maturing within 30 days", hint: "Live FDRs only.", value: (r) => bucket(r, "D30").toLocaleString("en-IN") },
    { key: "overdue", label: "Past maturity", hint: "Live FDRs whose maturity date has passed.", value: (r) => bucket(r, "OVERDUE").toLocaleString("en-IN") },
  ];
  if (canReview(role)) {
    metrics.push(
      {
        key: "unanalysed", label: "Documents not yet analysed", hint: "Open FDRs whose documents have not been checked.",
        value: (r) => (r.verification_status.available ? (r.verification.find((v) => v.key === "NOT_ANALYZED")?.count ?? 0).toLocaleString("en-IN") : null),
        reason: (r) => r.verification_status.note ?? undefined,
      },
      {
        key: "exceptions", label: "Open exceptions", hint: "Mismatches, missing documents, incomplete or overdue records.",
        value: (r) => (r.exceptions_status.available ? total(r.exceptions).toLocaleString("en-IN") : null),
        reason: (r) => r.exceptions_status.note ?? undefined,
      },
    );
  }
  return metrics;
}

/** Figures that need the whole authorised dataset (principal, maturities, exceptions) come from the server's
 * report endpoint in one request; nothing is summed or estimated in the browser. */
function ReportCards({ role }: { role: string }) {
  const { state, reload } = useAsync((signal) => getReport(signal));
  return (
    <>
      {reportMetrics(role).map((metric) => {
        const value = state.status === "success" ? metric.value(state.data) : null;
        return (
          <div key={metric.key} className={CARD} aria-busy={state.status === "loading"} data-testid={`metric-${metric.key}`}>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{metric.label}</p>
            {state.status === "loading" && <p role="status" className="mt-2 text-sm text-slate-500">Loading…</p>}
            {state.status === "success" && value !== null && (
              <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-900" aria-label={`${metric.label}: ${value}`}>{value}</p>
            )}
            {state.status === "success" && value === null && (
              <div className="mt-2">
                <p className="text-sm font-medium text-slate-700">Unavailable</p>
                <p className="mt-1 text-xs text-slate-500">{metric.reason?.(state.data) ?? "Not available for your role."}</p>
              </div>
            )}
            {state.status === "error" && (
              <div role="alert" className="mt-2 space-y-2">
                <p className="text-sm font-medium text-red-800">Unavailable</p>
                <p className="text-xs text-slate-600">{describeError(state.error)}</p>
                <RetryButton onClick={reload} />
              </div>
            )}
            {metric.hint && state.status !== "error" && value !== null && <p className="mt-2 text-xs text-slate-500">{metric.hint}</p>}
          </div>
        );
      })}
    </>
  );
}

export default function SummaryCards({ role }: { role: string }) {
  return (
    <section aria-labelledby="summary-heading">
      <h2 id="summary-heading" className="sr-only">
        Summary
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metricsFor(role).map((metric) => (
          <MetricCard key={metric.key} metric={metric} />
        ))}
        <ReportCards role={role} />
      </div>
    </section>
  );
}
