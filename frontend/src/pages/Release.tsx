import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import DemoNotice from "../components/DemoNotice";
import StatusBadge from "../components/StatusBadge";
import { Alert } from "../components/FormParts";
import { AsyncView, Note, PageHeader } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import {
  ApiError, approveRelease, getRelease, listRelease, rejectRelease, requestRelease,
  type RejectionReason, type ReleaseOut, type ReleaseState,
} from "../lib/api";
import { formatDate, formatDateTime, formatInr } from "../lib/format";
import { roleLabel } from "../lib/roles";
import { useAsync } from "../lib/useAsync";

export const STATE_LABELS: Record<ReleaseState, string> = {
  NONE: "No request yet",
  REQUESTED: "Release requested",
  APPROVED: "Release approved",
  REJECTED: "Release request rejected",
};
const STATE_STYLE: Record<ReleaseState, string> = {
  NONE: "border-slate-300 bg-slate-50 text-slate-800",
  REQUESTED: "border-sky-300 bg-sky-50 text-sky-900",
  APPROVED: "border-emerald-300 bg-emerald-50 text-emerald-900",
  REJECTED: "border-red-300 bg-red-50 text-red-900",
};
const REASONS: Record<RejectionReason, string> = {
  INCOMPLETE_SUPPORTING_INFORMATION: "Supporting information is incomplete",
  NOT_YET_DUE: "Not yet due",
  DETAILS_NEED_CORRECTION: "Details need correction",
  OTHER: "Other reason",
};
const ACTION_LABELS = {
  "release.requested": "Release requested",
  "release.approved": "Release approved by an approver",
  "release.rejected": "Release request rejected by an approver",
} as const;

/** What this system does and does not do, shown wherever a release can be acted on. */
export const NO_TRANSFER = "Approval is an authorisation recorded in this system. This system does not transfer funds; any payment happens outside it.";

export function StateBadge({ state }: { state: ReleaseState }) {
  return <span className={`inline-block rounded border px-2 py-0.5 text-xs font-semibold ${STATE_STYLE[state]}`}>{STATE_LABELS[state]}</span>;
}

const PAGE_SIZE = 25;

export default function Release() {
  const [page, setPage] = useState(1);
  const [state, setStateFilter] = useState<ReleaseState | "">("");
  const loaded = useAsync((signal) => listRelease({ state: state || undefined, page, page_size: PAGE_SIZE }, signal), `${state}|${page}`);
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Release / Refund">Accepted FDRs that have reached maturity, and the requests to release them.</PageHeader>
      <DemoNotice />
      <Note>{NO_TRANSFER}</Note>
      <Panel title="Matured, accepted FDRs">
        <div className="mb-4">
          <label htmlFor="release-state" className="block text-xs font-medium text-slate-700">Release state</label>
          <select id="release-state" value={state} onChange={(e) => { setStateFilter(e.target.value as ReleaseState | ""); setPage(1); }}
            className="mt-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm">
            <option value="">All</option>
            {(Object.keys(STATE_LABELS) as ReleaseState[]).map((s) => <option key={s} value={s}>{STATE_LABELS[s]}</option>)}
          </select>
        </div>
        <AsyncView state={loaded.state} reload={loaded.reload} what="release requests">
          {(data) =>
            data.items.length === 0 ? (
              <p className="text-sm text-slate-700">
                {state ? "No FDRs are in this state." : "No accepted FDR has reached its maturity date yet."}
              </p>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-sm">
                    <caption className="sr-only">FDRs eligible for release</caption>
                    <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                      <tr>
                        <th scope="col" className="py-2 pr-4 font-medium">FDR number</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Bank</th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">Principal</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Matured</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Release</th>
                        <th scope="col" className="py-2 font-medium"><span className="sr-only">Open</span></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {data.items.map((r) => (
                        <tr key={r.fdr_id}>
                          <th scope="row" className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{r.fdr_number}</th>
                          <td className="py-2 pr-4 text-slate-700">{r.institution_name}<span className="block text-xs text-slate-500">{r.institution_code}</span></td>
                          <td className="py-2 pr-4 text-slate-700">{r.bank_name}</td>
                          <td className="whitespace-nowrap py-2 pr-4 text-right tabular-nums">{formatInr(r.principal_amount)}</td>
                          <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{formatDate(r.maturity_date)}</td>
                          <td className="py-2 pr-4"><StateBadge state={r.state} /></td>
                          <td className="whitespace-nowrap py-2">
                            <Link to={`/release/${r.fdr_id}`} aria-label={`Open ${r.fdr_number}`} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-800 hover:bg-slate-100">Open</Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-4 flex items-center justify-between text-xs text-slate-600">
                  <span>{data.total.toLocaleString("en-IN")} {data.total === 1 ? "FDR" : "FDRs"}</span>
                  {data.total_pages > 1 && (
                    <span className="flex items-center gap-2">
                      <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded border border-slate-300 px-3 py-1 font-medium disabled:opacity-50">Previous</button>
                      Page {data.page} of {data.total_pages}
                      <button type="button" disabled={page >= data.total_pages} onClick={() => setPage(page + 1)} className="rounded border border-slate-300 px-3 py-1 font-medium disabled:opacity-50">Next</button>
                    </span>
                  )}
                </div>
              </>
            )
          }
        </AsyncView>
      </Panel>
    </div>
  );
}

type Pending = "request" | "approve" | "reject" | null;

function Actions({ release, onChanged }: { release: ReleaseOut; onChanged: (r: ReleaseOut) => void }) {
  const [pending, setPending] = useState<Pending>(null);
  const [reason, setReason] = useState<RejectionReason | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    if (busy || !pending) return;
    if (pending === "reject" && !reason) return;
    setBusy(true);
    setError(null);
    try {
      const updated = pending === "request" ? await requestRelease(release.fdr_id)
        : pending === "approve" ? await approveRelease(release.fdr_id)
        : await rejectRelease(release.fdr_id, reason as RejectionReason);
      setPending(null);
      setReason("");
      onChanged(updated);
    } catch (err) {
      setError(err instanceof ApiError
        ? err.kind === "network" ? "Cannot reach the server. Nothing was changed; check the current state and try again."
          : err.kind === "forbidden" ? "You are not permitted to do this."
          : err.kind === "server" ? `The server reported an error (HTTP ${err.status}).`
          : err.message
        : "The request failed unexpectedly.");
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  const button = "rounded border px-4 py-2 text-sm font-medium disabled:opacity-50";
  return (
    <div className="space-y-3">
      {error && <Alert tone="error">{error}</Alert>}
      <div className="flex flex-wrap gap-3">
        {release.can_request && (
          <button type="button" disabled={pending !== null} onClick={() => setPending("request")} className={`${button} border-slate-900 bg-slate-900 text-white`}>
            Request release
          </button>
        )}
        {release.can_decide && (
          <>
            <button type="button" disabled={pending !== null} onClick={() => setPending("approve")} className={`${button} border-slate-900 bg-slate-900 text-white`}>Approve release</button>
            <button type="button" disabled={pending !== null} onClick={() => setPending("reject")} className={`${button} border-red-700 text-red-800`}>Reject request</button>
          </>
        )}
      </div>
      {pending && (
        <section role="alertdialog" aria-labelledby="release-confirm-title" className="rounded border border-slate-900 bg-white p-4">
          <h2 id="release-confirm-title" className="text-base font-semibold text-slate-900">
            {pending === "request" ? "Request release of this FDR?" : pending === "approve" ? "Approve this release?" : "Reject this release request?"}
          </h2>
          <p className="mt-2 text-sm text-slate-700">
            {pending === "request"
              ? `This asks AICTE to authorise the release of ${release.fdr_number}. `
              : pending === "approve"
                ? `You are authorising the release of ${release.fdr_number}, in your name. `
                : `You are declining the release request for ${release.fdr_number}, in your name. The institution may ask again. `}
            {NO_TRANSFER}
          </p>
          {pending === "reject" && (
            <div className="mt-3">
              <label htmlFor="release-reason" className="block text-xs font-medium text-slate-700">Reason (required)</label>
              <select id="release-reason" value={reason} onChange={(e) => setReason(e.target.value as RejectionReason)} className="mt-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm">
                <option value="">Choose a reason</option>
                {(Object.keys(REASONS) as RejectionReason[]).map((r) => <option key={r} value={r}>{REASONS[r]}</option>)}
              </select>
            </div>
          )}
          <div className="mt-4 flex gap-3">
            <button type="button" onClick={confirm} disabled={busy || (pending === "reject" && !reason)} className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-500">
              {busy ? "Recording…" : pending === "request" ? "Confirm request" : pending === "approve" ? "Confirm approval" : "Confirm rejection"}
            </button>
            <button type="button" onClick={() => setPending(null)} disabled={busy} className="rounded border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 disabled:opacity-50">Cancel</button>
          </div>
        </section>
      )}
    </div>
  );
}

export function ReleaseDetail() {
  const { id = "" } = useParams();
  const loaded = useAsync((signal) => getRelease(id, signal), id);
  const [updated, setUpdated] = useState<ReleaseOut | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title="Release request">One FDR's eligibility, request and decision.</PageHeader>
      <DemoNotice />
      <AsyncView state={loaded.state} reload={loaded.reload} what="this release request">
        {(loadedRelease) => {
          const r = updated ?? loadedRelease;
          return (
            <>
              <Note>{NO_TRANSFER}</Note>
              {flash && <Alert tone="info">{flash}</Alert>}
              <Panel title={r.fdr_number}>
                <dl className="grid gap-3 text-sm sm:grid-cols-2">
                  <div><dt className="text-xs text-slate-500">Institution</dt><dd>{r.institution_name} ({r.institution_code})</dd></div>
                  <div><dt className="text-xs text-slate-500">Bank</dt><dd>{r.bank_name}</dd></div>
                  <div><dt className="text-xs text-slate-500">Principal (as submitted)</dt><dd>{formatInr(r.principal_amount)}</dd></div>
                  <div><dt className="text-xs text-slate-500">Maturity date</dt><dd>{formatDate(r.maturity_date)}</dd></div>
                  <div><dt className="text-xs text-slate-500">FDR status</dt><dd><StatusBadge status={r.fdr_status} /></dd></div>
                  <div><dt className="text-xs text-slate-500">Release</dt><dd><StateBadge state={r.state} /></dd></div>
                </dl>
                <p className="mt-3 text-sm text-slate-700">{r.eligibility_note}</p>
                <p className="mt-2 text-xs"><Link to={`/documents/${r.fdr_id}`} className="underline">View this FDR's documents</Link></p>
              </Panel>
              {(r.can_request || r.can_decide) && (
                <Panel title="Action">
                  <Actions release={r} onChanged={(next) => { setUpdated(next); setFlash(`Recorded: ${STATE_LABELS[next.state].toLowerCase()}.`); }} />
                </Panel>
              )}
              <Panel title="History">
                {r.history.length === 0 ? (
                  <p className="text-sm text-slate-700">No release request has been made for this FDR.</p>
                ) : (
                  <ol className="space-y-3">
                    {r.history.map((e, i) => (
                      <li key={i} className="text-sm">
                        <p className="font-medium text-slate-900">{ACTION_LABELS[e.action]}</p>
                        <p className="text-xs text-slate-600">
                          {e.actor_name ?? "Unknown user"}{e.actor_role ? ` · ${roleLabel(e.actor_role)}` : ""} · {formatDateTime(e.occurred_at)}
                        </p>
                        {e.reason && <p className="text-xs text-slate-700">Reason: {REASONS[e.reason as RejectionReason] ?? e.reason}</p>}
                      </li>
                    ))}
                  </ol>
                )}
              </Panel>
              <Link to="/release" className="text-sm font-medium text-slate-900 underline">Back to release / refund</Link>
            </>
          );
        }}
      </AsyncView>
    </div>
  );
}
