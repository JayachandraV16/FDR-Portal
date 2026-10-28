import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import StatusBadge from "../components/StatusBadge";
import { AsyncView, Note, PageHeader } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import DocumentCheckCell from "../fdr/DocumentCheckSummary";
import DocumentsPanel from "../fdr/DocumentsPanel";
import { getFdr, listFdrs, listReviewQueue } from "../lib/api";
import { formatDate, formatInr } from "../lib/format";
import { canReview } from "../lib/roles";
import { useAsync } from "../lib/useAsync";
import { NotPermitted } from "./FdrCreate";

/** What this page is: extraction and comparison are a reading aid. This is shown wherever results are. */
export const NOT_VERIFICATION =
  "Reading values from a document and comparing them with the submitted record is not independent verification. " +
  "It does not show that a document is genuine or that the bank issued it. Only the bank can confirm that; " +
  "a person decides what to do with these results.";

const QUEUE_PAGE = 25;

function VerificationList() {
  const queue = useAsync((signal) => listReviewQueue({ page: 1, page_size: QUEUE_PAGE }, signal));
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const found = useAsync((signal) => (q ? listFdrs({ q, page_size: 10 }, signal) : Promise.resolve(null)), q);
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Verification">Document extraction and comparison for submitted FDRs.</PageHeader>
      <DemoNotice />
      <Note>{NOT_VERIFICATION}</Note>
      <Panel title="Awaiting review" description="Submitted FDRs with the state of their document checks. Checks are never started automatically.">
        <AsyncView state={queue.state} reload={queue.reload} what="the review queue">
          {(data) =>
            data.items.length === 0 ? (
              <p className="text-sm text-slate-700">No FDRs are awaiting review.</p>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-sm">
                    <caption className="sr-only">Submitted FDRs and their document checks</caption>
                    <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                      <tr>
                        <th scope="col" className="py-2 pr-4 font-medium">FDR number</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Document check</th>
                        <th scope="col" className="py-2 font-medium"><span className="sr-only">Open</span></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {data.items.map((f) => (
                        <tr key={f.id}>
                          <th scope="row" className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{f.fdr_number}</th>
                          <td className="py-2 pr-4 text-slate-700">{f.institution.name}</td>
                          <td className="py-2 pr-4"><DocumentCheckCell summary={f.document_check} /></td>
                          <td className="whitespace-nowrap py-2">
                            <Link to={`/verification/${f.id}`} aria-label={`Open verification for ${f.fdr_number}`} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-800 hover:bg-slate-100">Open</Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {data.total > data.items.length && (
                  <p className="mt-3 text-xs text-slate-600">
                    Showing the oldest {data.items.length} of {data.total.toLocaleString("en-IN")}. The <Link to="/review" className="underline">review queue</Link> lists them all.
                  </p>
                )}
              </>
            )
          }
        </AsyncView>
      </Panel>
      <Panel title="Find another FDR" description="Records that were already decided or returned are not in the queue.">
        <label htmlFor="verification-search" className="block text-xs font-medium text-slate-700">FDR number</label>
        <input id="verification-search" type="search" value={search} maxLength={64} onChange={(e) => setSearch(e.target.value)}
          className="mt-1 block w-full max-w-sm rounded border border-slate-300 bg-white px-3 py-1.5 text-sm" />
        {q && (
          <div className="mt-3">
            <AsyncView state={found.state} reload={found.reload} what="matching FDRs">
              {(data) =>
                !data || data.items.length === 0 ? (
                  <p className="text-sm text-slate-700">No FDRs match this search.</p>
                ) : (
                  <ul className="divide-y divide-slate-100 text-sm">
                    {data.items.map((f) => (
                      <li key={f.id} className="flex items-center justify-between gap-3 py-2">
                        <span><strong>{f.fdr_number}</strong> <span className="text-slate-600">{f.institution.name}</span> <StatusBadge status={f.status} /></span>
                        <Link to={`/verification/${f.id}`} aria-label={`Open verification for ${f.fdr_number}`} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-100">Open</Link>
                      </li>
                    ))}
                  </ul>
                )
              }
            </AsyncView>
          </div>
        )}
      </Panel>
    </div>
  );
}

export default function Verification() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  return canReview(role) ? <VerificationList /> : <NotPermitted what="Verification" />;
}

function VerificationForFdr() {
  const { id = "" } = useParams();
  const { state, reload } = useAsync((signal) => getFdr(id, signal), id);
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="Verification" />
      <DemoNotice />
      <AsyncView state={state} reload={reload} what="this FDR">
        {(fdr) => (
          <>
            <Panel title={fdr.fdr_number} description="The submitted values the documents are compared with.">
              <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <div><dt className="text-xs text-slate-500">Institution</dt><dd>{fdr.institution.name}</dd></div>
                <div><dt className="text-xs text-slate-500">Bank</dt><dd>{fdr.bank.name}</dd></div>
                <div><dt className="text-xs text-slate-500">Status</dt><dd><StatusBadge status={fdr.status} /></dd></div>
                <div><dt className="text-xs text-slate-500">Principal</dt><dd>{formatInr(fdr.principal_amount)}</dd></div>
                <div><dt className="text-xs text-slate-500">Issue date</dt><dd>{formatDate(fdr.issue_date)}</dd></div>
                <div><dt className="text-xs text-slate-500">Maturity date</dt><dd>{formatDate(fdr.maturity_date)}</dd></div>
              </dl>
            </Panel>
            <Note>{NOT_VERIFICATION}</Note>
            <DocumentsPanel fdrId={fdr.id} mode="view" checks />
            <p className="text-sm text-slate-700">
              Running a check never approves, rejects or returns an FDR. Decisions are made, by a person, on the{" "}
              <Link to={`/review/${fdr.id}`} className="font-medium underline">review page</Link>.
            </p>
          </>
        )}
      </AsyncView>
      <Link to="/verification" className="text-sm font-medium text-slate-900 underline">Back to verification</Link>
    </div>
  );
}

export function VerificationDetail() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  return canReview(role) ? <VerificationForFdr /> : <NotPermitted what="Verification" />;
}
