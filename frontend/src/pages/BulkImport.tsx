import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { Icon } from "../components/icons";
import { Note, PageHeader, RoleNotice } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import {
  ApiError, downloadImportErrors, downloadImportTemplate, previewImport, runImport, type ImportPreview, type ImportResult,
} from "../lib/api";
import { saveBlob } from "../lib/download";

const MAX_BYTES = 1_000_000;
type Step = "choose" | "checking" | "preview" | "confirm" | "importing" | "done";

function message(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.kind === "network") return "Cannot reach the server. Check your connection and try again.";
    if (err.kind === "forbidden") return "Your account is not permitted to import FDRs.";
    if (typeof err.detail === "string" && err.detail) return err.detail;
  }
  return fallback;
}

/** Row table shared by the preview (Valid / Rejected) and the result (Created / Rejected). */
function Rows({ rows }: { rows: Array<{ line: number; fdr_number: string; ok: boolean; label: string; problems: string[]; link?: string | null }> }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">Rows of the file</caption>
        <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" className="py-2 pr-4 font-medium">Line</th>
            <th scope="col" className="py-2 pr-4 font-medium">FDR number</th>
            <th scope="col" className="py-2 pr-4 font-medium">Result</th>
            <th scope="col" className="py-2 font-medium">What to fix</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r) => (
            <tr key={r.line} className="align-top" data-testid={r.ok ? "row-ok" : "row-bad"}>
              <td className="whitespace-nowrap py-2 pr-4 tabular-nums text-slate-700">{r.line}</td>
              <th scope="row" className="py-2 pr-4 font-medium text-slate-900">
                {r.link ? <Link to={r.link} className="underline">{r.fdr_number}</Link> : r.fdr_number || <span className="text-slate-500">(blank)</span>}
              </th>
              <td className="whitespace-nowrap py-2 pr-4">
                <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-semibold ${r.ok ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-red-300 bg-red-50 text-red-900"}`}>
                  <Icon name={r.ok ? "check" : "close"} className="h-3 w-3" />{r.label}
                </span>
              </td>
              <td className="py-2 text-slate-800">
                {r.problems.length === 0 ? <span className="text-slate-500">None</span> : (
                  <ul className="list-disc space-y-0.5 pl-4">{r.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Importer() {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [step, setStep] = useState<Step>("choose");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyRejected, setOnlyRejected] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  function reset() {
    setFile(null);
    setStep("choose");
    setPreview(null);
    setResult(null);
    setError(null);
    setNote(null);
    setOnlyRejected(false);
    if (input.current) input.current.value = "";
  }

  function choose(next: File | null) {
    setPreview(null);
    setResult(null);
    setError(null);
    setNote(null);
    setStep("choose");
    if (next && next.size > MAX_BYTES) {
      setFile(null);
      setError("That file is larger than 1 MB. Split it into smaller files.");
      return;
    }
    setFile(next);
  }

  async function template() {
    setError(null);
    try {
      saveBlob(await downloadImportTemplate(), "fdr-import-template.csv");
    } catch (err) {
      setError(message(err, "The template could not be downloaded. Please try again."));
    }
  }

  async function check() {
    if (!file) return;
    setStep("checking");
    setError(null);
    try {
      setPreview(await previewImport(file));
      setStep("preview");
    } catch (err) {
      setError(message(err, "The file could not be checked. Please try again."));
      setStep("choose");
    }
  }

  async function importNow() {
    if (!file || !preview) return;
    setStep("importing");
    setError(null);
    try {
      setResult(await runImport(file, preview.digest));
      setStep("done");
    } catch (err) {
      setError(message(err, "The import failed and nothing was reported as created. Check the registry, then try again."));
      setStep("preview");
    }
  }

  async function rejectedRows() {
    if (!file) return;
    setNote(null);
    try {
      saveBlob(await downloadImportErrors(file), "fdr-import-rejected-rows.csv");
      setNote("The rejected rows were downloaded.");
    } catch (err) {
      setError(message(err, "The rejected rows could not be downloaded. Please try again."));
    }
  }

  const busy = step === "checking" || step === "importing";
  const button = "rounded border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-50";
  const primary = "rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-400";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="Bulk import">Create many FDRs at once from a CSV file. Every row becomes a draft that only your institution can see.</PageHeader>
      <DemoNotice />

      {step !== "done" && (
        <>
          <Panel title="1. Get the template" description="Use exactly these columns. Dates are written YYYY-MM-DD and amounts as plain numbers such as 1500000.00.">
            <button type="button" onClick={template} className={`${button} inline-flex items-center gap-1.5`}>
              <Icon name="download" className="h-4 w-4" /> Download the CSV template
            </button>
            <p className="mt-2 text-xs text-slate-600">
              The template has one example row (FDR number SAMPLE-0001): replace it or delete it before importing, or it will be imported as a draft like any other row.
              The bank is given by its bank code. There is no status column: an import never submits, accepts or releases anything.
            </p>
          </Panel>

          <Panel title="2. Check your file" description="Nothing is saved at this step: you will see every row and what is wrong with it first.">
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label htmlFor="import-file" className="block text-xs font-medium text-slate-700">CSV file (up to 1 MB and 500 rows)</label>
                <input
                  id="import-file" ref={input} type="file" accept=".csv,text/csv" disabled={busy}
                  onChange={(e) => choose(e.target.files?.[0] ?? null)}
                  className="mt-1 block text-sm text-slate-800 file:mr-3 file:rounded file:border file:border-slate-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium"
                />
              </div>
              <button type="button" disabled={!file || busy} onClick={check} className={primary}>{step === "checking" ? "Checking…" : "Check file"}</button>
            </div>
          </Panel>
        </>
      )}

      {error && <p role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">{error}</p>}
      {step === "checking" && <p role="status" className="text-sm text-slate-600">Checking the file…</p>}
      {step === "importing" && <p role="status" className="text-sm text-slate-600">Creating drafts…</p>}

      {preview && (step === "preview" || step === "confirm" || step === "importing") && (
        <Panel title="3. Review the result" description="This is what the server found. Rows with problems will not be imported.">
          <div className="mb-4 grid gap-3 sm:grid-cols-3">
            <div className="rounded border border-slate-200 bg-white p-3" data-testid="count-total"><p className="text-xs font-medium text-slate-600">Rows in the file</p><p className="mt-1 text-2xl font-semibold tabular-nums">{preview.rows_total}</p></div>
            <div className="rounded border border-slate-200 bg-white p-3" data-testid="count-valid"><p className="text-xs font-medium text-slate-600">Ready to import</p><p className="mt-1 text-2xl font-semibold tabular-nums">{preview.valid_count}</p></div>
            <div className="rounded border border-slate-200 bg-white p-3" data-testid="count-invalid"><p className="text-xs font-medium text-slate-600">Rejected</p><p className="mt-1 text-2xl font-semibold tabular-nums">{preview.invalid_count}</p></div>
          </div>
          {preview.invalid_count > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-sm text-slate-800">
                <input type="checkbox" checked={onlyRejected} onChange={(e) => setOnlyRejected(e.target.checked)} /> Show only rejected rows
              </label>
              <button type="button" onClick={rejectedRows} className={`${button} inline-flex items-center gap-1.5`}><Icon name="download" className="h-4 w-4" /> Download rejected rows (CSV)</button>
              {note && <span role="status" className="text-xs text-emerald-900">{note}</span>}
            </div>
          )}
          <Rows rows={preview.rows.filter((r) => !onlyRejected || !r.valid).map((r) => ({ line: r.line, fdr_number: r.fdr_number, ok: r.valid, label: r.valid ? "Ready" : "Rejected", problems: r.errors }))} />

          <div className="mt-5 border-t border-slate-100 pt-4">
            {step === "confirm" ? (
              <div role="group" aria-label="Confirm the import" className="flex flex-wrap items-center gap-3 text-sm">
                <span>
                  Create <strong>{preview.valid_count}</strong> draft {preview.valid_count === 1 ? "FDR" : "FDRs"}?
                  {preview.invalid_count > 0 && ` The ${preview.invalid_count} rejected ${preview.invalid_count === 1 ? "row" : "rows"} will not be imported.`}
                </span>
                <button type="button" onClick={importNow} className={primary}>Confirm import</button>
                <button type="button" onClick={() => setStep("preview")} className={button}>Cancel</button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" disabled={preview.valid_count === 0 || busy} onClick={() => setStep("confirm")} className={primary}>
                  Create {preview.valid_count} {preview.valid_count === 1 ? "draft" : "drafts"}
                </button>
                <button type="button" onClick={reset} disabled={busy} className={button}>Start again</button>
                {preview.valid_count === 0 && <span className="text-xs text-slate-600">No row is ready to import. Fix the file and check it again.</span>}
              </div>
            )}
            <p className="mt-2 text-xs text-slate-600">{preview.note} The file is checked again when you confirm.</p>
          </div>
        </Panel>
      )}

      {result && step === "done" && (
        <Panel title="Import finished" description={result.note}>
          <p role="status" className="mb-3 text-sm text-slate-900" data-testid="result-summary">
            <strong>{result.created}</strong> {result.created === 1 ? "draft was" : "drafts were"} created and <strong>{result.rejected}</strong> {result.rejected === 1 ? "row was" : "rows were"} rejected.
          </p>
          <Rows rows={result.rows.map((r) => ({ line: r.line, fdr_number: r.fdr_number, ok: r.outcome === "created", label: r.outcome === "created" ? "Created" : "Rejected", problems: r.errors, link: r.fdr_id ? `/fdrs/${r.fdr_id}` : null }))} />
          <div className="mt-4 flex flex-wrap gap-3">
            <Link to="/fdrs" className={button}>Open the FDR registry</Link>
            <button type="button" onClick={reset} className={button}>Import another file</button>
          </div>
        </Panel>
      )}

      <Note>
        The server checks every row again with the same rules as the Create FDR form. An FDR number that already exists at the same bank in your
        institution (and is not rejected or withdrawn), or that repeats within the file, is rejected. Importing the same file twice therefore creates nothing the second time.
      </Note>
    </div>
  );
}

export default function BulkImport() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  return role === "INSTITUTION_USER" ? <Importer /> : <RoleNotice title="Bulk import" who="institution users" />;
}
