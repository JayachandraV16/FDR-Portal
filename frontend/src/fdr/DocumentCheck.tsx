import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Icon } from "../components/icons";
import { RetryButton } from "../dashboard/Panel";
import {
  ApiError,
  getComparison,
  startExtraction,
  type ComparisonOutcome,
  type ComparisonSide,
  type DocumentComparison,
  type ExtractedFieldOut,
  type FieldComparison,
} from "../lib/api";
import { formatDate, formatDateTime, formatInr } from "../lib/format";
import { describeError } from "../lib/useAsync";

/** How often a check that is queued or running is looked at again, and for how long before the page stops
 * polling and tells the reviewer to look again later. */
export const POLL_MS = 2500;
export const MAX_POLLS = 120;

const OUTCOME: Record<ComparisonOutcome, { label: string; symbol: ReactNode; row: string; badge: string }> = {
  MATCH: { label: "Match", symbol: <Icon name="check" className="h-3.5 w-3.5" />, row: "", badge: "border-emerald-300 bg-emerald-50 text-emerald-900" },
  MISMATCH: { label: "Mismatch", symbol: <Icon name="close" className="h-3.5 w-3.5" />, row: "bg-red-50", badge: "border-red-300 bg-red-100 text-red-900" },
  NOT_FOUND: { label: "Not found in document", symbol: "–", row: "bg-slate-50", badge: "border-slate-300 bg-slate-100 text-slate-800" },
  AMBIGUOUS: { label: "Ambiguous", symbol: "?", row: "bg-amber-50", badge: "border-amber-300 bg-amber-100 text-amber-900" },
  NOT_COMPARABLE: { label: "Not comparable", symbol: "≈", row: "bg-slate-50", badge: "border-slate-300 bg-slate-100 text-slate-800" },
};
const CONFIDENCE = { HIGH: "High", MEDIUM: "Medium", LOW: "Low" } as const;

/** A value as the person should read it: amounts in INR grouping, dates spelled out, rates with a sign. */
export function displayValue(field: string, value: string | null): string {
  if (value === null || value === "") return "Not provided";
  if (field === "principal_amount") return formatInr(value);
  if (field === "issue_date" || field === "maturity_date") return formatDate(value);
  if (field === "interest_rate_pct") return `${value}%`;
  return value;
}

/** Plain-language text for the extraction's machine-readable warnings. Unknown ones are not shown raw. */
export function warningText(warning: string): string | null {
  if (warning.startsWith("pages_without_text:")) {
    return `No readable text was found on page ${warning.split(":")[1].split(",").join(", ")}.`;
  }
  if (warning.startsWith("missing_ocr_languages:")) {
    return `OCR language data is not installed on the server: ${warning.split(":")[1]}.`;
  }
  return null;
}

function Found({ row }: { row: FieldComparison }) {
  const f: ExtractedFieldOut = row.extracted;
  if (f.status === "NOT_FOUND") return <span className="text-slate-500">Not found</span>;
  if (f.status === "AMBIGUOUS") {
    return (
      <span>
        {f.candidates.length > 0 ? (
          <>
            Could be: {f.candidates.map((c, i) => <span key={i}>{i > 0 && " or "}<strong>{displayValue(f.name, c.value)}</strong></span>)}
          </>
        ) : (
          "Could not be read reliably"
        )}
        {f.raw && <span className="block text-xs text-slate-600">As printed: <span className="break-words">{f.raw}</span></span>}
      </span>
    );
  }
  return (
    <span>
      <strong className="break-words">{displayValue(f.name, f.value)}</strong>
      {f.raw && <span className="block text-xs text-slate-600">As printed: <span className="break-words">{f.raw}</span></span>}
    </span>
  );
}

function Outcome({ side }: { side: ComparisonSide }) {
  const o = OUTCOME[side.outcome];
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-semibold ${o.badge}`}>
      <span aria-hidden="true">{o.symbol}</span>
      {o.label}
    </span>
  );
}

function ResultsTable({ data }: { data: DocumentComparison }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">Submitted values compared with values found in the document</caption>
        <thead className="border-b border-slate-200 text-xs uppercase text-slate-600">
          <tr>
            <th scope="col" className="py-2 pr-3">Field</th>
            <th scope="col" className="py-2 pr-3">Submitted</th>
            <th scope="col" className="py-2 pr-3">Found in document</th>
            <th scope="col" className="py-2 pr-3">Result</th>
            <th scope="col" className="py-2 pr-3">Confidence</th>
            <th scope="col" className="py-2">Page</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {data.fields.map((row) => {
            const differs =
              row.revision !== null &&
              (row.revision.outcome !== row.current.outcome || row.revision.submitted_value !== row.current.submitted_value);
            return (
              <tr key={row.field} data-outcome={row.current.outcome} className={`align-top ${OUTCOME[row.current.outcome].row}`}>
                <th scope="row" className="py-2 pr-3 font-medium text-slate-900">{row.label}</th>
                <td className="py-2 pr-3 break-words">{displayValue(row.field, row.current.submitted_value)}</td>
                <td className="py-2 pr-3"><Found row={row} /></td>
                <td className="py-2 pr-3">
                  <Outcome side={row.current} />
                  <p className="mt-1 max-w-xs text-xs text-slate-700">{row.current.explanation}</p>
                  {differs && row.revision && (
                    <p className="mt-1 max-w-xs text-xs text-slate-700">
                      Against the submitted version (revision {data.revision_no}, {displayValue(row.field, row.revision.submitted_value)}):{" "}
                      <strong>{OUTCOME[row.revision.outcome].label}</strong>. {row.revision.explanation}
                    </p>
                  )}
                </td>
                <td className="py-2 pr-3">
                  {row.extracted.confidence ? CONFIDENCE[row.extracted.confidence] : "—"}
                  {row.extracted.source === "OCR" && <span className="block text-xs text-slate-600">read by OCR</span>}
                </td>
                <td className="py-2">{row.extracted.page ? `Page ${row.extracted.page}` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function failureText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.kind === "network") return "Cannot reach the server. Check your connection and try again.";
    if (err.status === 503 && err.message) return err.message; // "The extraction queue is full. Try again in a moment."
    if (err.kind === "server") return `The server reported an error (HTTP ${err.status}). Please try again shortly.`;
    if (err.kind === "forbidden") return "You are not permitted to run this check, or the request was blocked by a security check.";
    return err.message;
  }
  return "The request failed unexpectedly. Please try again.";
}

type Loaded =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "success"; data: DocumentComparison; refreshWarning: boolean };

function CheckBody({ fdrId, documentId }: { fdrId: string; documentId: string }) {
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [requesting, setRequesting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [polls, setPolls] = useState(0);
  const inFlight = useRef(false); // closes the gap between a click and the re-render that disables the button
  const alive = useRef(true);
  const lastData = useRef<DocumentComparison | null>(null);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  /** Load the comparison. `silent` keeps what is on screen (used by polling) instead of showing "Loading…". */
  const load = useCallback(
    async (silent: boolean) => {
      if (!silent) setLoaded({ status: "loading" });
      try {
        const data = await getComparison(fdrId, documentId);
        if (!alive.current) return;
        lastData.current = data;
        setLoaded({ status: "success", data, refreshWarning: false });
      } catch (err) {
        if (!alive.current) return;
        if (silent && lastData.current) setLoaded({ status: "success", data: lastData.current, refreshWarning: true });
        else setLoaded({ status: "error", error: err instanceof Error ? err : new Error("Request failed") });
      }
    },
    [fdrId, documentId],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  const data = loaded.status === "success" ? loaded.data : null;
  const job = data?.job ?? null;
  const active = job?.status === "PENDING" || job?.status === "RUNNING";
  const extraction = data?.extraction ?? null;

  useEffect(() => {
    if (!active) return;
    if (polls >= MAX_POLLS) return;
    const timer = setTimeout(() => {
      setPolls((n) => n + 1);
      void load(true);
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [active, polls, load]);

  async function run() {
    if (inFlight.current) return;
    inFlight.current = true;
    setRequesting(true);
    setFailure(null);
    try {
      const accepted = await startExtraction(fdrId, documentId);
      setPolls(0);
      // Show the accepted job straight away, then pick up its progress from the server.
      if (lastData.current) {
        const next = { ...lastData.current, job: accepted };
        lastData.current = next;
        setLoaded({ status: "success", data: next, refreshWarning: false });
      }
      await load(true);
    } catch (err) {
      setFailure(failureText(err));
    } finally {
      inFlight.current = false;
      setRequesting(false);
    }
  }

  const latestFailed = job?.status === "FAILED" || (!active && extraction?.status === "FAILED");
  const failedMessage = job?.status === "FAILED" ? job.error_message : extraction?.error_message;
  const failedCode = job?.status === "FAILED" ? job.error_code : extraction?.error_code;
  const showResults = extraction?.status === "COMPLETED" && data !== null;
  const runLabel = latestFailed ? "Retry document check" : extraction === null && job === null ? "Run document check" : "Run again";
  const stalePolling = active && polls >= MAX_POLLS;

  return (
    <div className="space-y-3 rounded border border-slate-200 bg-white p-3">
      <p role="note" className="text-xs text-slate-700">
        <strong>A reading aid, not a verification.</strong> Values are read automatically from the uploaded file. This does not show
        that the document is genuine or that the bank issued it, and it changes nothing about the FDR. Decide using your own judgement.
      </p>

      {loaded.status === "loading" && <p role="status" className="text-sm text-slate-500">Loading the document check…</p>}
      {loaded.status === "error" && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-red-800">Could not load the document check. {describeError(loaded.error)}.</p>
          <RetryButton onClick={() => void load(false)} />
        </div>
      )}
      {failure && <p role="alert" className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{failure}</p>}
      {loaded.status === "success" && loaded.refreshWarning && (
        <p role="note" className="text-xs text-amber-900">Could not refresh the status just now. Trying again.</p>
      )}

      {active && job && (
        <p role="status" className="rounded border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
          {job.status === "PENDING"
            ? "Document check queued. It will start shortly; this page updates by itself."
            : "Document check running. This page updates by itself."}
          {stalePolling && " It is taking longer than usual: check again in a little while."}
        </p>
      )}
      {stalePolling && <RetryButton onClick={() => { setPolls(0); void load(true); }} label="Check status again" />}

      {data && !active && job === null && extraction === null && (
        <p className="text-sm text-slate-700">No document check has been run for this file yet.</p>
      )}
      {latestFailed && !active && (
        <div role="alert" className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">
          <p className="font-medium">The document could not be checked.</p>
          <p className="mt-1">{failedMessage ?? "The check failed."}</p>
          {failedCode && <p className="mt-1 text-xs">Reason code: {failedCode}</p>}
          {showResults && <p className="mt-1 text-xs">The results below are from an earlier check.</p>}
          {extraction?.warnings.map((w) => warningText(w)).filter(Boolean).map((text) => (
            <p key={text} className="mt-1 text-xs">{text}</p>
          ))}
        </div>
      )}
      {showResults && data && extraction && (
        <>
          <p className="text-xs text-slate-600">
            {active ? "Previous check: " : ""}Checked {formatDateTime(extraction.requested_at)} with {extraction.extractor}.{" "}
            {extraction.pages_processed} of {extraction.pages_total} {extraction.pages_total === 1 ? "page" : "pages"} read
            {extraction.ocr_pages > 0 ? `, ${extraction.ocr_pages} by OCR` : ""}.
            {data.revision_no !== null && ` Compared with the FDR as it is now and with submission ${data.revision_no}.`}
          </p>
          {extraction.ocr_pages > 0 && (
            <p role="note" className="text-xs text-amber-900">Text read by OCR can contain misread characters. Check those values against the document itself.</p>
          )}
          {extraction.truncated && <p role="note" className="text-xs text-amber-900">The document was long: only the first part of its text was analysed.</p>}
          {extraction.warnings.map((w) => warningText(w)).filter((t): t is string => t !== null).map((text) => (
            <p key={text} role="note" className="text-xs text-amber-900">{text}</p>
          ))}
          <ResultsTable data={data} />
        </>
      )}

      <button
        type="button"
        onClick={run}
        disabled={requesting || active || loaded.status === "loading"}
        className="rounded border border-slate-900 px-3 py-1.5 text-xs font-medium text-slate-900 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {requesting ? "Requesting…" : active ? "Check in progress" : runLabel}
      </button>
    </div>
  );
}

/** Collapsed by default so the review page stays quiet until a reviewer asks for the check. */
export default function DocumentCheck({ fdrId, documentId, filename }: { fdrId: string; documentId: string; filename: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} document check for ${filename}`}
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-medium text-slate-900 underline"
      >
        {open ? "Hide document check" : "Document check"}
      </button>
      {open && (
        <div className="mt-2">
          <CheckBody fdrId={fdrId} documentId={documentId} />
        </div>
      )}
    </div>
  );
}
