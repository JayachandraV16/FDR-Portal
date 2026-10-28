import { Link } from "react-router-dom";

import { SeverityBadge } from "../components/ui";
import { getActivity, getAlerts } from "../lib/api";
import { fdrStatusLabel, formatDateTime } from "../lib/format";
import { roleLabel } from "../lib/roles";
import { describeError, useAsync } from "../lib/useAsync";
import { Panel, RetryButton } from "./Panel";

/** Only destinations that exist as actions today; sections are reached from the sidebar. */
export function QuickActions() {
  return (
    <Panel title="Quick actions">
      <ul className="space-y-2 text-sm">
        <li>
          <Link to="/change-password" className="font-medium text-slate-900 underline hover:text-slate-700">
            Change your password
          </Link>
        </li>
      </ul>
      <p className="mt-4 text-xs text-slate-500">
        Every section is reached from the sidebar: institutions, FDRs, documents, verification, alerts,
        exceptions, release / refund, reports and (for administrators) administration. What you see depends
        on your role.
      </p>
    </Panel>
  );
}

/** The most urgent computed alerts, from the same endpoint as the Alerts page (so the two always agree). */
export function NeedsAttention() {
  const { state, reload } = useAsync((signal) => getAlerts(signal));
  return (
    <Panel title="Needs attention" description="The most urgent alerts for the records you can see.">
      {state.status === "loading" && <p role="status" className="text-sm text-slate-500">Loading alerts…</p>}
      {state.status === "error" && (
        <div role="alert" className="flex items-center justify-between gap-4">
          <p className="text-sm text-red-800">Could not load alerts. {describeError(state.error)}.</p>
          <RetryButton onClick={reload} />
        </div>
      )}
      {state.status === "success" && state.data.items.length === 0 && <p className="text-sm text-slate-700">Nothing needs attention right now.</p>}
      {state.status === "success" && state.data.items.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {state.data.items.slice(0, 5).map((a, i) => (
            <li key={`${a.category}-${a.fdr_id}-${i}`} className="flex items-start justify-between gap-3 py-2 text-sm">
              <div className="min-w-0">
                <p className="font-medium text-slate-900">
                  <Link to={a.link} className="underline">{a.fdr_number}</Link> <span className="font-normal text-slate-600">{a.institution_code}</span>
                </p>
                <p className="text-xs text-slate-700">{a.message}</p>
              </div>
              <SeverityBadge severity={a.severity} />
            </li>
          ))}
        </ul>
      )}
      {state.status === "success" && (
        <p className="mt-3 text-xs"><Link to="/alerts" className="font-medium underline">All alerts</Link> · <Link to="/exceptions" className="font-medium underline">Exceptions</Link></p>
      )}
    </Panel>
  );
}

/** Recent workflow status changes for FDRs the user may see. This is not the audit log: it shows what changed
 * and which role did it, never comments or personal names. */
export function RecentActivity() {
  const { state, reload } = useAsync((signal) => getActivity(8, signal));
  return (
    <Panel title="Recent activity" description="Status changes for FDRs you can see. This is not the audit log.">
      {state.status === "loading" && <p role="status" className="text-sm text-slate-500">Loading activity…</p>}
      {state.status === "error" && (
        <div role="alert" className="flex items-center justify-between gap-4">
          <p className="text-sm text-red-800">Could not load activity. {describeError(state.error)}.</p>
          <RetryButton onClick={reload} />
        </div>
      )}
      {state.status === "success" && state.data.items.length === 0 && <p className="text-sm text-slate-700">No workflow activity yet.</p>}
      {state.status === "success" && state.data.items.length > 0 && (
        <ol className="space-y-3">
          {state.data.items.map((e, i) => (
            <li key={`${e.fdr_id}-${e.occurred_at}-${i}`} className="text-sm">
              <p className="text-slate-900">
                <Link to={`/fdrs/${e.fdr_id}`} className="font-medium underline">{e.fdr_number}</Link>{" "}
                {e.from_status ? `${fdrStatusLabel(e.from_status)} → ${fdrStatusLabel(e.to_status)}` : `created as ${fdrStatusLabel(e.to_status)}`}
              </p>
              <p className="text-xs text-slate-500">{roleLabel(e.actor_role)} · {formatDateTime(e.occurred_at)}</p>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
