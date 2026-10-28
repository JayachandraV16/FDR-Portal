import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { AsyncView, Note, PageHeader, RoleNotice } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import { getWorkload, type WorkloadOut } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { roleLabel } from "../lib/roles";
import { useAsync } from "../lib/useAsync";

const PERIODS = [7, 30, 90] as const;
const ALLOWED = ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN"];

function Stat({ label, value, hint, testId }: { label: string; value: string; hint?: string; testId: string }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-3" data-testid={testId}>
      <p className="text-xs font-medium text-slate-600">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-600">{hint}</p>}
    </div>
  );
}

/** A horizontal bar per row, scaled to the largest. The numbers beside the bars are the real information. */
function Bars({ rows, label }: { rows: Array<{ key: string; label: string; count: number }>; label: string }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <ul aria-label={label} className="space-y-2">
      {rows.map((r) => (
        <li key={r.key} className="text-sm">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-slate-800">{r.label}</span>
            <span className="tabular-nums font-medium text-slate-900">{r.count.toLocaleString("en-IN")}</span>
          </div>
          <div aria-hidden="true" data-testid="bar" className="mt-1 h-1.5 overflow-hidden rounded bg-slate-100">
            <div className="h-full rounded bg-slate-600" style={{ width: `${(r.count / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

const hours = (h: number | null) => (h === null ? "Not available" : h < 48 ? `${h} hours` : `${(h / 24).toFixed(1)} days`);

function Content({ data }: { data: WorkloadOut }) {
  return (
    <>
      <Note>
        Figures are calculated from the real submission and decision times of the FDRs you may see, generated {formatDateTime(data.generated_at)}.
        Completed work covers the last {data.period_days} days (since {formatDateTime(data.window_start)}).
      </Note>

      <section aria-label="Review queue" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat testId="pending" label="Awaiting review now" value={data.pending.toLocaleString("en-IN")} hint="FDRs in status Submitted." />
        <Stat
          testId="oldest"
          label="Longest wait"
          value={data.oldest_pending_days === null ? "None" : `${data.oldest_pending_days.toLocaleString("en-IN")} ${data.oldest_pending_days === 1 ? "day" : "days"}`}
          hint={data.oldest_pending_at ? `Submitted ${formatDateTime(data.oldest_pending_at)}` : "No pending FDR has a submission time."}
        />
        <Stat testId="completed" label={`Reviews completed (${data.period_days} days)`} value={data.completed_total.toLocaleString("en-IN")} hint="Accepted, rejected or returned for correction." />
        <Stat
          testId="turnaround"
          label="Typical turnaround"
          value={hours(data.turnaround.median_hours)}
          hint={data.turnaround.measured === 0 ? "No completed review could be measured." : `Median of ${data.turnaround.measured} review${data.turnaround.measured === 1 ? "" : "s"}; average ${hours(data.turnaround.average_hours)}.`}
        />
      </section>
      <p className="-mt-3 text-xs text-slate-600">
        {data.turnaround.note}
        {data.turnaround.excluded > 0 && ` ${data.turnaround.excluded} decision${data.turnaround.excluded === 1 ? " was" : "s were"} excluded for a missing timestamp.`}
        {data.pending_missing_timestamp > 0 && ` ${data.pending_missing_timestamp} pending FDR${data.pending_missing_timestamp === 1 ? " has" : "s have"} no submission time and is not included in the waiting times.`}
      </p>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="How long the queue has waited" description="Pending FDRs by time since their latest submission.">
          <Bars rows={data.waiting} label="Waiting time" />
          <div className="mt-4 rounded border border-slate-200 bg-slate-50 p-3 text-sm" data-testid="sla">
            <p className="font-medium text-slate-900">{data.sla.configured ? `Local threshold: ${data.sla.threshold_days} days` : "No review deadline configured"}</p>
            {data.sla.configured && (
              <p className="mt-1 text-slate-800">
                {data.sla.beyond} beyond the threshold · {data.sla.approaching} approaching it
              </p>
            )}
            <p className="mt-1 text-xs text-slate-600">{data.sla.note}</p>
          </div>
          <p className="mt-3 text-xs"><Link to={data.queue_link} className="font-medium underline">Open the review queue</Link></p>
        </Panel>

        <Panel title="Queue by status" description="Every FDR you may see, by its current status.">
          <Bars rows={data.distribution} label="FDRs by status" />
        </Panel>

        <Panel title="Completed in this period" description="Decisions taken on submitted FDRs.">
          <Bars rows={data.completed} label="Completed reviews by outcome" />
          {data.mine !== null && <p className="mt-3 text-sm text-slate-800">Your own decisions in this period: <strong data-testid="mine">{data.mine}</strong></p>}
        </Panel>

        <Panel title="Who took the decisions" description={data.by_person ? "By named person (visible to administrators only)." : "Totals by role; individual names are visible to administrators only."}>
          {data.by_person ? (
            data.by_person.length === 0 ? <p className="text-sm text-slate-700">No decisions in this period.</p> : (
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">Decisions by person</caption>
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr><th scope="col" className="py-2 pr-4 font-medium">Person</th><th scope="col" className="py-2 pr-4 font-medium">Role</th><th scope="col" className="py-2 text-right font-medium">Decisions</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.by_person.map((p, i) => (
                    <tr key={i}><th scope="row" className="py-2 pr-4 font-normal">{p.name}</th><td className="py-2 pr-4">{roleLabel(p.role)}</td><td className="py-2 text-right tabular-nums">{p.count}</td></tr>
                  ))}
                </tbody>
              </table>
            )
          ) : data.by_role.length === 0 ? (
            <p className="text-sm text-slate-700">No decisions in this period.</p>
          ) : (
            <Bars rows={data.by_role.map((r) => ({ key: r.role, label: roleLabel(r.role), count: r.count }))} label="Decisions by role" />
          )}
          <p className="mt-3 rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700" data-testid="assignment">
            <strong>Assignment not tracked.</strong> {data.assignment_note}
          </p>
        </Panel>
      </div>
    </>
  );
}

export default function Workload() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const { state, reload } = useAsync((signal) => (ALLOWED.includes(role) ? getWorkload(days, signal) : Promise.reject(new Error("not permitted"))), `${role}|${days}`);
  if (!ALLOWED.includes(role)) return <RoleNotice title="Reviewer workload" who="AICTE reviewers, approvers and administrators" />;
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Reviewer workload">How long submitted FDRs wait for review and how much review work has been completed.</PageHeader>
      <DemoNotice />
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Period for completed reviews">
        <span className="text-sm text-slate-700">Completed reviews in the last</span>
        {PERIODS.map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={days === n}
            onClick={() => setDays(n)}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-100 aria-pressed:bg-slate-900 aria-pressed:text-white"
          >
            {n} days
          </button>
        ))}
      </div>
      <AsyncView state={state} reload={reload} what="the workload">{(data) => <Content data={data} />}</AsyncView>
    </div>
  );
}
