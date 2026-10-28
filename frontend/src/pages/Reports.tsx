import { useState } from "react";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { AsyncView, Note, PageHeader } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import { ApiError, downloadReportCsv, downloadReportPdf, getReport, type MetricStatus, type PdfKind, type RegisterRow } from "../lib/api";
import { formatDate, formatInr } from "../lib/format";
import { saveBlob } from "../lib/download";
import { canReview } from "../lib/roles";
import { useAsync } from "../lib/useAsync";
import { Icon } from "../components/icons";
import { InstitutionFilter } from "./FdrRegistry";

function Table({ title, rows, showPrincipal, status, empty }: {
  title: string; rows: RegisterRow[]; showPrincipal: boolean; status?: MetricStatus; empty?: string;
}) {
  // Bars are drawn from the same counts as the figures beside them (the table stays the accessible version).
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <Panel title={title}>
      {status && !status.available ? (
        <p className="text-sm text-slate-700"><strong>Unavailable.</strong> {status.note}</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-slate-700">{empty ?? "Nothing to report."}</p>
      ) : (
        <>
          <table className="min-w-full text-left text-sm">
            <caption className="sr-only">{title}</caption>
            <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">Item</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Count</th>
                {showPrincipal && <th scope="col" className="py-2 text-right font-medium">Principal</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.key} data-key={r.key}>
                  <th scope="row" className="py-2 pr-4 font-normal text-slate-800">{r.label}</th>
                  <td className="py-2 pr-4 text-right tabular-nums text-slate-900">
                    {r.count.toLocaleString("en-IN")}
                    <div aria-hidden="true" data-testid="bar" className="ml-auto mt-1 h-1.5 w-24 overflow-hidden rounded bg-slate-100">
                      <div className="h-full rounded bg-slate-600" style={{ width: `${(r.count / max) * 100}%` }} />
                    </div>
                  </td>
                  {showPrincipal && <td className="py-2 text-right tabular-nums text-slate-900">{r.principal === null ? "—" : formatInr(r.principal)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {status?.note && <p className="mt-2 text-xs text-slate-600">{status.note}</p>}
        </>
      )}
    </Panel>
  );
}

function CsvButton({ kind, label, date }: { kind: "fdrs" | "exceptions"; label: string; date: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function download() {
    setBusy(true);
    setError(null);
    try {
      saveBlob(await downloadReportCsv(kind), `${kind === "fdrs" ? "fdr-report" : "exceptions"}-${date}.csv`);
    } catch (err) {
      setError(err instanceof ApiError && err.kind === "forbidden" ? "You are not permitted to export this report." : "The export failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="inline-flex flex-col">
      <button type="button" onClick={download} disabled={busy} className="rounded border border-slate-900 px-3 py-1.5 text-xs font-medium text-slate-900 hover:bg-slate-100 disabled:opacity-50">
        {busy ? "Preparing…" : label}
      </button>
      {error && <span role="alert" className="mt-1 text-xs text-red-800">{error}</span>}
    </span>
  );
}

const REVIEW = ["AICTE_REVIEWER", "AICTE_APPROVER"];
const PDF_REPORTS: Array<{ kind: PdfKind; label: string; description: string; roles?: string[] }> = [
  { kind: "summary", label: "FDR summary", description: "FDRs and principal by status, upcoming maturities and release requests." },
  { kind: "institutions", label: "Institution-wise summary", description: "Live FDRs and their principal, institution by institution." },
  { kind: "maturity", label: "Maturity report", description: "Live FDRs by maturity date, soonest first. Uses the period and institution chosen above." },
  { kind: "pending-review", label: "Pending review report", description: "FDRs awaiting a decision, oldest first. Uses the institution chosen above.", roles: REVIEW },
  { kind: "exceptions", label: "Exceptions and discrepancies report", description: "The exceptions found in the records, most serious first.", roles: REVIEW },
  { kind: "audit", label: "Audit trail report", description: "Events by action and outcome, the latest events and the hash-chain check. Uses the audit window chosen above.", roles: ["ADMIN", "AUDITOR"] },
];

function PdfButton({ kind, label, params, date }: { kind: PdfKind; label: string; params: { days?: number; institution_id?: string }; date: string }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  async function download() {
    setBusy(true);
    setMessage(null);
    try {
      saveBlob(await downloadReportPdf(kind, params), `fdr-${kind}-${date}.pdf`);
      setMessage({ tone: "ok", text: "The PDF was downloaded." });
    } catch (err) {
      const text = err instanceof ApiError && err.kind === "forbidden" ? "You are not permitted to export this report."
        : err instanceof ApiError && err.kind === "network" ? "Cannot reach the server. Check your connection and try again."
        : "The PDF could not be generated. Please try again.";
      setMessage({ tone: "error", text });
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="inline-flex flex-col">
      <button type="button" onClick={download} disabled={busy} aria-label={`Download ${label} (PDF)`} className="inline-flex items-center gap-1.5 rounded border border-slate-900 px-3 py-1.5 text-xs font-medium text-slate-900 hover:bg-slate-100 disabled:opacity-50">
        <Icon name="download" className="h-4 w-4" />
        {busy ? "Preparing…" : "PDF"}
      </button>
      {message && <span role={message.tone === "error" ? "alert" : "status"} className={`mt-1 text-xs ${message.tone === "error" ? "text-red-800" : "text-emerald-900"}`}>{message.text}</span>}
    </span>
  );
}

function PdfReports({ role, date }: { role: string; date: string }) {
  const [days, setDays] = useState(90);
  const [auditDays, setAuditDays] = useState(30);
  const [institution, setInstitution] = useState("");
  const available = PDF_REPORTS.filter((r) => !r.roles || r.roles.includes(role));
  const params = (kind: PdfKind) =>
    kind === "maturity" ? { days, institution_id: institution || undefined }
    : kind === "pending-review" ? { institution_id: institution || undefined }
    : kind === "audit" ? { days: auditDays } : {};
  const select = "mt-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm";
  return (
    <Panel title="PDF reports" description="Printable reports for the records you may see. Each PDF states its scope and filters; it is not digitally signed and shows English text only.">
      <div className="mb-4 flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor="pdf-days" className="block text-xs font-medium text-slate-700">Maturity period</label>
          <select id="pdf-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className={select}>
            {[30, 60, 90].map((n) => <option key={n} value={n}>Within {n} days</option>)}
          </select>
        </div>
        {role !== "INSTITUTION_USER" && <InstitutionFilter value={institution} onChange={setInstitution} />}
        {role === "ADMIN" || role === "AUDITOR" ? (
          <div>
            <label htmlFor="pdf-audit-days" className="block text-xs font-medium text-slate-700">Audit window</label>
            <select id="pdf-audit-days" value={auditDays} onChange={(e) => setAuditDays(Number(e.target.value))} className={select}>
              {[7, 30, 90].map((n) => <option key={n} value={n}>Last {n} days</option>)}
            </select>
          </div>
        ) : null}
      </div>
      <ul className="divide-y divide-slate-100">
        {available.map((r) => (
          <li key={r.kind} className="flex flex-wrap items-start justify-between gap-3 py-2.5">
            <span className="min-w-0 text-sm"><span className="font-medium text-slate-900">{r.label}</span><span className="block text-xs text-slate-600">{r.description}</span></span>
            <PdfButton kind={r.kind} label={r.label} params={params(r.kind)} date={date} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}

export default function Reports() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  const { state, reload } = useAsync((signal) => getReport(signal));
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="Reports">Summaries of the records you are allowed to see.</PageHeader>
      <DemoNotice />
      <AsyncView state={state} reload={reload} what="the report">
        {(r) => (
          <>
            <Note>
              {r.scope} Figures are computed over everything in that scope on {formatDate(r.generated_on)}, not over one page of a
              list. Amounts are as submitted by institutions; none has been verified with a bank.
            </Note>
            <div className="grid gap-3 sm:grid-cols-2" aria-label="Totals">
              <div className="rounded border border-slate-200 bg-white p-3">
                <p className="text-xs font-medium text-slate-600">FDRs in scope</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums" data-testid="fdr-total">{r.fdr_total.toLocaleString("en-IN")}</p>
              </div>
              <div className="rounded border border-slate-200 bg-white p-3">
                <p className="text-xs font-medium text-slate-600">Principal of live FDRs (submitted, returned or accepted)</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums" data-testid="principal-total">{formatInr(r.principal_live_total)}</p>
              </div>
            </div>
            <p className="text-xs text-slate-600">
              The tables and totals on this page always cover everything in your scope. The PDF reports below can be narrowed by
              period or institution where an option is offered, and each prints the filters it used. The CSV downloads contain
              exactly the records you are allowed to see.
            </p>
            <div className="flex flex-wrap gap-3">
              <CsvButton kind="fdrs" label="Download FDR report (CSV)" date={r.generated_on} />
              {canReview(role) && <CsvButton kind="exceptions" label="Download exceptions (CSV)" date={r.generated_on} />}
            </div>
            <PdfReports role={role} date={r.generated_on} />
            <Table title="FDRs by status" rows={r.by_status} showPrincipal />
            <Table title="Live FDRs by institution" rows={r.by_institution} showPrincipal />
            <Table title="Upcoming maturities (live FDRs)" rows={r.maturities} showPrincipal />
            <Table title="Release / refund requests (matured, accepted FDRs)" rows={r.release} showPrincipal={false} status={r.release_status} />
            <Table title="Document verification" rows={r.verification} showPrincipal={false} status={r.verification_status} />
            <Table title="Exceptions" rows={r.exceptions} showPrincipal={false} status={r.exceptions_status} empty="No exception types could be computed for your role." />
          </>
        )}
      </AsyncView>
    </div>
  );
}
