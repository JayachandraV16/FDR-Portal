import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import StatusBadge from "../components/StatusBadge";
import { AsyncView, Note, PageHeader } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import DocumentsPanel from "../fdr/DocumentsPanel";
import { getFdr, listFdrs } from "../lib/api";
import { formatDate, formatInr } from "../lib/format";
import { canReview } from "../lib/roles";
import { useAsync } from "../lib/useAsync";
import { NotPermitted } from "./FdrCreate";

const PAGE_SIZE = 25;
const EDITABLE = ["DRAFT", "CORRECTION_REQUESTED"];

/** Roles the document endpoints serve (the server decides; this only avoids offering a dead end). */
const mayUseDocuments = (role: string) => role === "INSTITUTION_USER" || canReview(role);

function DocumentsList() {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    const t = setTimeout(() => { setQ(search.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [search]);
  const { state, reload } = useAsync((signal) => listFdrs({ q: q || undefined, page, page_size: PAGE_SIZE }, signal), `${q}|${page}`);
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Documents">Choose an FDR to see, upload or download its supporting documents.</PageHeader>
      <DemoNotice />
      <Panel title="FDRs">
        <div className="mb-4 max-w-sm">
          <label htmlFor="doc-search" className="block text-xs font-medium text-slate-700">FDR number</label>
          <input id="doc-search" type="search" value={search} maxLength={64} onChange={(e) => setSearch(e.target.value)}
            className="mt-1 block w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm" />
          <p className="mt-1 text-xs text-slate-500">Matches the start of the FDR number.</p>
        </div>
        <AsyncView state={state} reload={reload} what="FDRs">
          {(data) =>
            data.items.length === 0 ? (
              <p className="text-sm text-slate-700">{q ? "No FDRs match this search." : "There are no FDRs to show."}</p>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-sm">
                    <caption className="sr-only">FDRs</caption>
                    <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                      <tr>
                        <th scope="col" className="py-2 pr-4 font-medium">FDR number</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                        <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                        <th scope="col" className="py-2 font-medium"><span className="sr-only">Documents</span></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {data.items.map((f) => (
                        <tr key={f.id}>
                          <th scope="row" className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{f.fdr_number}</th>
                          <td className="py-2 pr-4 text-slate-700">{f.institution.name}</td>
                          <td className="py-2 pr-4"><StatusBadge status={f.status} /></td>
                          <td className="whitespace-nowrap py-2">
                            <Link to={`/documents/${f.id}`} aria-label={`Documents for ${f.fdr_number}`} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-800 hover:bg-slate-100">Documents</Link>
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

export default function Documents() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  return mayUseDocuments(role) ? <DocumentsList /> : <NotPermitted what="Documents" />;
}

function DocumentsForFdr({ role }: { role: string }) {
  const { id = "" } = useParams();
  const { state, reload } = useAsync((signal) => getFdr(id, signal), id);
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title="FDR documents" />
      <DemoNotice />
      <AsyncView state={state} reload={reload} what="this FDR">
        {(fdr) => {
          const manage = role === "INSTITUTION_USER" && EDITABLE.includes(fdr.status);
          return (
            <>
              <Panel title={fdr.fdr_number}>
                <dl className="grid gap-3 text-sm sm:grid-cols-2">
                  <div><dt className="text-xs text-slate-500">Institution</dt><dd>{fdr.institution.name}</dd></div>
                  <div><dt className="text-xs text-slate-500">Status</dt><dd><StatusBadge status={fdr.status} /></dd></div>
                  <div><dt className="text-xs text-slate-500">Principal (as submitted)</dt><dd>{formatInr(fdr.principal_amount)}</dd></div>
                  <div><dt className="text-xs text-slate-500">Maturity date</dt><dd>{formatDate(fdr.maturity_date)}</dd></div>
                </dl>
                <p className="mt-3 flex flex-wrap gap-4 text-xs">
                  {role === "INSTITUTION_USER" && <Link to={`/fdrs/${fdr.id}/edit`} className="underline">Open the FDR record</Link>}
                  {canReview(role) && <Link to={`/review/${fdr.id}`} className="underline">Open for review</Link>}
                  {canReview(role) && <Link to={`/verification/${fdr.id}`} className="underline">Open verification</Link>}
                </p>
              </Panel>
              {!manage && role === "INSTITUTION_USER" && (
                <Note>Documents can only be added or removed while the FDR is a draft or has been returned for correction.</Note>
              )}
              {role === "INSTITUTION_USER" && (
                <Note>Document analysis results are visible to AICTE reviewers only. Uploading a document does not verify the FDR.</Note>
              )}
              <DocumentsPanel fdrId={fdr.id} mode={manage ? "manage" : "view"} checks={canReview(role)} />
            </>
          );
        }}
      </AsyncView>
      <Link to="/documents" className="text-sm font-medium text-slate-900 underline">Back to documents</Link>
    </div>
  );
}

export function DocumentsDetail() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  return mayUseDocuments(role) ? <DocumentsForFdr role={role} /> : <NotPermitted what="Documents" />;
}
