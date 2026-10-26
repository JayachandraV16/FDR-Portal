import { listFdrs, listInstitutions, listUsers, type FdrStatus } from "../lib/api";
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

// Metrics the current API cannot answer correctly. Shown as unavailable, never estimated.
const UNSUPPORTED: Array<{ label: string; reason: string }> = [
  {
    label: "Total principal amount",
    reason: "No aggregate endpoint exists; summing one page of FDRs would give a wrong total.",
  },
  {
    label: "Maturing soon",
    reason: "The FDR list has no maturity-date filter or count.",
  },
];

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
        {UNSUPPORTED.map((item) => (
          <div key={item.label} className={`${CARD} border-dashed bg-slate-50`} data-testid="metric-unsupported">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{item.label}</p>
            <p className="mt-2 text-sm font-medium text-slate-700">Unavailable</p>
            <p className="mt-1 text-xs text-slate-500">{item.reason}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
