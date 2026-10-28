import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { Alert } from "../components/FormParts";
import { RetryButton } from "../dashboard/Panel";
import DocumentCheckCell from "../fdr/DocumentCheckSummary";
import { listReviewQueue } from "../lib/api";
import { formatDate, formatInr } from "../lib/format";
import { canReview } from "../lib/roles";
import { describeError, useAsync } from "../lib/useAsync";
import { BankFilter, InstitutionFilter } from "./FdrRegistry";
import { NotPermitted } from "./FdrCreate";

const PAGE_SIZE = 25;
const SEARCH_DEBOUNCE_MS = 300;

interface Applied {
  q: string;
  bank: string;
  institution: string;
  page: number;
}
const EMPTY: Applied = { q: "", bank: "", institution: "", page: 1 };

const FIELD_CLASS =
  "mt-1 block w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-300";

/** The route's entry point: the data hooks live in QueueContent, so a role that may not review never
 * even asks the server for the queue. (The server refuses it regardless.) */
export default function ReviewQueue() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  return canReview(role) ? <QueueContent /> : <NotPermitted what="Review queue" />;
}

function QueueContent() {
  const location = useLocation();
  const navigate = useNavigate();
  const [flash, setFlash] = useState<string | null>((location.state as { flash?: string } | null)?.flash ?? null);
  useEffect(() => {
    if ((location.state as { flash?: string } | null)?.flash) navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState<Applied>(EMPTY);
  useEffect(() => {
    const q = search.trim();
    const timer = setTimeout(() => setApplied((a) => (a.q === q ? a : { ...a, q, page: 1 })), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const { state, reload } = useAsync(
    (signal) =>
      listReviewQueue(
        {
          page: applied.page,
          page_size: PAGE_SIZE,
          q: applied.q || undefined,
          bank_id: applied.bank || undefined,
          institution_id: applied.institution || undefined,
        },
        signal,
      ),
    JSON.stringify(applied),
  );

  const filtersActive = Boolean(applied.q || applied.bank || applied.institution);
  const change = (patch: Partial<Applied>) => setApplied((a) => ({ ...a, ...patch, page: 1 }));
  const clearAll = () => {
    setSearch("");
    setApplied(EMPTY);
  };

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Review queue</h1>
        <p className="mt-1 text-sm text-slate-600">
          FDRs submitted by institutions and waiting for a decision, oldest first. Details are as submitted; none
          has been verified with a bank.
        </p>
        <p className="mt-1 text-xs text-slate-500">
          The document check is an automatic reading aid built from checks already run on uploaded files. A mismatch
          means a document differs from the submitted record and deserves a look; it does not show that a document is
          genuine or fraudulent. &ldquo;Not analyzed&rdquo; is neutral, and nothing here starts a check.
        </p>
      </div>

      <DemoNotice />

      {flash && (
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <Alert tone="info">{flash}</Alert>
          </div>
          <RetryButton onClick={() => setFlash(null)} label="Dismiss" />
        </div>
      )}

      <section aria-labelledby="queue-filters-heading" className="rounded border border-slate-200 bg-white p-4">
        <h2 id="queue-filters-heading" className="sr-only">
          Search and filters
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label htmlFor="queue-search" className="block text-xs font-medium text-slate-700">
              FDR number
            </label>
            <input
              id="queue-search"
              type="search"
              maxLength={64}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-describedby="queue-search-note"
              className={FIELD_CLASS}
            />
            <p id="queue-search-note" className="mt-1 text-xs text-slate-500">
              Matches the start of the FDR number. Spaces and letter case are ignored.
            </p>
          </div>
          <BankFilter value={applied.bank} onChange={(bank) => change({ bank })} />
          <InstitutionFilter value={applied.institution} onChange={(institution) => change({ institution })} />
        </div>
        {(filtersActive || search) && (
          <div className="mt-4">
            <RetryButton onClick={clearAll} label="Clear search and filters" />
          </div>
        )}
      </section>

      <section aria-labelledby="queue-results-heading" className="rounded border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 id="queue-results-heading" className="text-sm font-semibold text-slate-900">
            Awaiting review
          </h2>
          {state.status === "success" && (
            <p className="mt-0.5 text-xs text-slate-500" data-testid="queue-count">
              {state.data.total.toLocaleString("en-IN")} {state.data.total === 1 ? "FDR" : "FDRs"}{" "}
              {filtersActive ? "match the current filters" : "awaiting review"}
            </p>
          )}
        </div>
        <div className="p-4" aria-live="polite">
          {state.status === "loading" && (
            <p role="status" className="text-sm text-slate-500">
              Loading the review queue…
            </p>
          )}
          {state.status === "error" && (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-red-800">Could not load the review queue.</p>
                <p className="mt-0.5 text-xs text-slate-600">{describeError(state.error)}.</p>
              </div>
              <RetryButton onClick={reload} />
            </div>
          )}
          {state.status === "success" && state.data.total === 0 && (
            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="text-sm font-medium text-slate-700">
                {filtersActive ? "No queued FDRs match the current search and filters." : "No FDRs are awaiting review."}
              </p>
              {filtersActive && <RetryButton onClick={clearAll} label="Clear search and filters" />}
            </div>
          )}
          {state.status === "success" && state.data.total > 0 && state.data.items.length === 0 && (
            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="text-sm text-slate-700">There are no records on this page.</p>
              <RetryButton onClick={() => setApplied((a) => ({ ...a, page: 1 }))} label="Go to the first page" />
            </div>
          )}
          {state.status === "success" && state.data.items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">FDRs awaiting review</caption>
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="py-2 pr-4 font-medium">FDR number</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Bank</th>
                    <th scope="col" className="py-2 pr-4 text-right font-medium">Principal</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Matures</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Submitted</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Document check</th>
                    <th scope="col" className="py-2 font-medium"><span className="sr-only">Action</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {state.data.items.map((fdr) => (
                    <tr key={fdr.id}>
                      <th scope="row" className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{fdr.fdr_number}</th>
                      <td className="py-2 pr-4 text-slate-700">
                        {fdr.institution.name}
                        <span className="block text-xs text-slate-500">{fdr.institution.code}</span>
                      </td>
                      <td className="py-2 pr-4 text-slate-700">{fdr.bank.name}</td>
                      <td className="whitespace-nowrap py-2 pr-4 text-right tabular-nums text-slate-900">{formatInr(fdr.principal_amount)}</td>
                      <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{formatDate(fdr.maturity_date)}</td>
                      <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{fdr.submitted_at ? formatDate(fdr.submitted_at) : "—"}</td>
                      <td className="py-2 pr-4"><DocumentCheckCell summary={fdr.document_check} /></td>
                      <td className="whitespace-nowrap py-2">
                        <Link
                          to={`/review/${fdr.id}`}
                          aria-label={`Review ${fdr.fdr_number}`}
                          className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-800 hover:bg-slate-100"
                        >
                          Review
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {state.status === "success" && state.data.total > PAGE_SIZE && (
            <nav aria-label="Pagination" className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
              <p className="text-xs text-slate-600">
                Showing {(state.data.page - 1) * state.data.page_size + 1}–
                {(state.data.page - 1) * state.data.page_size + state.data.items.length} of{" "}
                {state.data.total.toLocaleString("en-IN")}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setApplied((a) => ({ ...a, page: a.page - 1 }))}
                  disabled={applied.page <= 1}
                  className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Previous
                </button>
                <span className="text-xs text-slate-600">
                  Page {state.data.page} of {state.data.total_pages}
                </span>
                <button
                  type="button"
                  onClick={() => setApplied((a) => ({ ...a, page: a.page + 1 }))}
                  disabled={state.data.page >= state.data.total_pages}
                  className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </nav>
          )}
        </div>
      </section>
    </div>
  );
}
