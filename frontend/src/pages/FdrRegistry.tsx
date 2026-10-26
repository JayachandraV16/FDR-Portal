import { useEffect, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import StatusBadge from "../components/StatusBadge";
import { RetryButton } from "../dashboard/Panel";
import { listBanks, listFdrs, listInstitutions, type FdrOut, type FdrStatus } from "../lib/api";
import { FDR_STATUS_LABELS, formatDate, formatInr } from "../lib/format";
import { describeError, useAsync } from "../lib/useAsync";

const PAGE_SIZE = 25;
const SEARCH_DEBOUNCE_MS = 300;

// Mirrors backend FdrStatus. Cross-institution roles are never shown DRAFT or WITHDRAWN records
// (backend/app/security/scope.py), so those two options would only ever give an empty result.
const ALL_STATUSES: FdrStatus[] = ["DRAFT", "SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED", "REJECTED", "WITHDRAWN"];
const SUBMITTED_STATE_STATUSES: FdrStatus[] = ["SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED", "REJECTED"];

interface Applied {
  q: string;
  status: FdrStatus | "";
  bank: string;
  institution: string;
  page: number;
}

const EMPTY: Applied = { q: "", status: "", bank: "", institution: "", page: 1 };

const FIELD_CLASS =
  "mt-1 block w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-300 disabled:bg-slate-100 disabled:text-slate-500";

export default function FdrRegistry() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  const institutionUser = role === "INSTITUTION_USER";

  const [search, setSearch] = useState(""); // what the user typed
  const [applied, setApplied] = useState<Applied>(EMPTY); // what is actually requested

  // Debounce typing into one request; a changed search always returns to page 1.
  useEffect(() => {
    const q = search.trim();
    const timer = setTimeout(() => setApplied((a) => (a.q === q ? a : { ...a, q, page: 1 })), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const filtersActive = Boolean(applied.q || applied.status || applied.bank || applied.institution);
  const { state, reload } = useAsync(
    (signal) =>
      listFdrs(
        {
          page: applied.page,
          page_size: PAGE_SIZE,
          q: applied.q || undefined,
          status: applied.status || undefined,
          bank_id: applied.bank || undefined,
          institution_id: institutionUser ? undefined : applied.institution || undefined,
        },
        signal,
      ),
    JSON.stringify(applied),
  );

  const change = (patch: Partial<Applied>) => setApplied((a) => ({ ...a, ...patch, page: 1 }));
  const clearAll = () => {
    setSearch("");
    setApplied(EMPTY);
  };
  const statusOptions = institutionUser ? ALL_STATUSES : SUBMITTED_STATE_STATUSES;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">FDR Registry</h1>
        <p className="mt-1 text-sm text-slate-600">
          Fixed deposit receipts visible to your account. This list is read-only.
        </p>
      </div>

      <DemoNotice />

      <section aria-labelledby="fdr-filters-heading" className="rounded border border-slate-200 bg-white p-4">
        <h2 id="fdr-filters-heading" className="sr-only">
          Search and filters
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label htmlFor="fdr-search" className="block text-xs font-medium text-slate-700">
              FDR number
            </label>
            <input
              id="fdr-search"
              type="search"
              maxLength={64}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-describedby="fdr-search-note"
              className={FIELD_CLASS}
            />
            <p id="fdr-search-note" className="mt-1 text-xs text-slate-500">
              Matches the start of the FDR number. Spaces and letter case are ignored. No other field is searched.
            </p>
          </div>

          <div>
            <label htmlFor="fdr-status" className="block text-xs font-medium text-slate-700">
              Status
            </label>
            <select
              id="fdr-status"
              value={applied.status}
              onChange={(e) => change({ status: e.target.value as FdrStatus | "" })}
              className={FIELD_CLASS}
            >
              <option value="">All statuses</option>
              {statusOptions.map((s) => (
                <option key={s} value={s}>
                  {FDR_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </div>

          <BankFilter value={applied.bank} onChange={(bank) => change({ bank })} />
          {!institutionUser && <InstitutionFilter value={applied.institution} onChange={(institution) => change({ institution })} />}
        </div>
        {(filtersActive || search) && (
          <div className="mt-4">
            <RetryButton onClick={clearAll} label="Clear search and filters" />
          </div>
        )}
      </section>

      <section aria-labelledby="fdr-results-heading" className="rounded border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 id="fdr-results-heading" className="text-sm font-semibold text-slate-900">
            Records
          </h2>
          {state.status === "success" && (
            <p className="mt-0.5 text-xs text-slate-500" data-testid="fdr-count">
              {state.data.total.toLocaleString("en-IN")} {state.data.total === 1 ? "FDR" : "FDRs"}{" "}
              {filtersActive ? "match the current filters" : "visible to you"}
              {!institutionUser && !filtersActive && " (drafts and withdrawn records are not visible to your role)"}
            </p>
          )}
        </div>

        <div className="p-4" aria-live="polite">
          {state.status === "loading" && (
            <p role="status" className="text-sm text-slate-500">
              Loading FDR records…
            </p>
          )}

          {state.status === "error" && (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-red-800">Could not load FDR records.</p>
                <p className="mt-0.5 text-xs text-slate-600">{describeError(state.error)}.</p>
              </div>
              <RetryButton onClick={reload} />
            </div>
          )}

          {state.status === "success" && state.data.total === 0 && (
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-700">
                  {filtersActive ? "No FDRs match the current search and filters." : "No FDR records are available to you yet."}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {filtersActive
                    ? "Try a shorter FDR number or clear a filter."
                    : "Records will appear here once FDRs have been added and are visible to your role."}
                </p>
              </div>
              {filtersActive && <RetryButton onClick={clearAll} label="Clear search and filters" />}
            </div>
          )}

          {state.status === "success" && state.data.total > 0 && state.data.items.length === 0 && (
            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="text-sm text-slate-700">There are no records on this page.</p>
              <RetryButton onClick={() => setApplied((a) => ({ ...a, page: 1 }))} label="Go to the first page" />
            </div>
          )}

          {state.status === "success" && state.data.items.length > 0 && <FdrTable rows={state.data.items} />}

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

function FdrTable({ rows }: { rows: FdrOut[] }) {
  const missing = <span className="text-slate-400">Not provided</span>;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">FDR records</caption>
        <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" className="py-2 pr-4 font-medium">FDR number</th>
            <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
            <th scope="col" className="py-2 pr-4 font-medium">Bank</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">Principal</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">Interest rate</th>
            <th scope="col" className="py-2 pr-4 font-medium">Issued</th>
            <th scope="col" className="py-2 pr-4 font-medium">Matures</th>
            <th scope="col" className="py-2 pr-4 font-medium">Status</th>
            <th scope="col" className="py-2 font-medium">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((fdr) => (
            <tr key={fdr.id}>
              <th scope="row" className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{fdr.fdr_number}</th>
              <td className="py-2 pr-4 text-slate-700">
                {fdr.institution.name}
                <span className="block text-xs text-slate-500">{fdr.institution.code}</span>
              </td>
              <td className="py-2 pr-4 text-slate-700">{fdr.bank.name}</td>
              <td className="whitespace-nowrap py-2 pr-4 text-right tabular-nums text-slate-900">{formatInr(fdr.principal_amount)}</td>
              <td className="whitespace-nowrap py-2 pr-4 text-right tabular-nums text-slate-700">
                {fdr.interest_rate_pct === null ? missing : `${fdr.interest_rate_pct}%`}
              </td>
              <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{formatDate(fdr.issue_date)}</td>
              <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{formatDate(fdr.maturity_date)}</td>
              <td className="py-2 pr-4"><StatusBadge status={fdr.status} /></td>
              <td className="whitespace-nowrap py-2 text-slate-700">{formatDate(fdr.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-slate-500">
        Newest first. “Accepted” means accepted after human review of the submitted information; it does not mean
        the FDR has been verified with the bank.
      </p>
    </div>
  );
}

function BankFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const { state, reload } = useAsync((signal) => listBanks({ page_size: 500 }, signal));
  return (
    <div>
      <label htmlFor="fdr-bank" className="block text-xs font-medium text-slate-700">
        Bank
      </label>
      <select
        id="fdr-bank"
        value={value}
        disabled={state.status !== "success"}
        onChange={(e) => onChange(e.target.value)}
        className={FIELD_CLASS}
      >
        <option value="">{state.status === "loading" ? "Loading banks…" : "All banks"}</option>
        {state.status === "success" &&
          state.data.items.map((bank) => (
            <option key={bank.id} value={bank.id}>
              {bank.name}
            </option>
          ))}
      </select>
      {state.status === "error" && (
        <p role="alert" className="mt-1 text-xs text-red-800">
          Banks could not be loaded. <button type="button" onClick={reload} className="underline">Retry</button>
        </p>
      )}
      {state.status === "success" && state.data.total > state.data.items.length && (
        <p className="mt-1 text-xs text-slate-500">
          Showing the first {state.data.items.length} of {state.data.total} banks.
        </p>
      )}
    </div>
  );
}

function InstitutionFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const { state, reload } = useAsync((signal) => listInstitutions({ limit: 200 }, signal));
  return (
    <div>
      <label htmlFor="fdr-institution" className="block text-xs font-medium text-slate-700">
        Institution
      </label>
      <select
        id="fdr-institution"
        value={value}
        disabled={state.status !== "success"}
        onChange={(e) => onChange(e.target.value)}
        className={FIELD_CLASS}
      >
        <option value="">{state.status === "loading" ? "Loading institutions…" : "All institutions"}</option>
        {state.status === "success" &&
          state.data.items.map((inst) => (
            <option key={inst.id} value={inst.id}>
              {inst.code} — {inst.name}
            </option>
          ))}
      </select>
      {state.status === "error" && (
        <p role="alert" className="mt-1 text-xs text-red-800">
          Institutions could not be loaded. <button type="button" onClick={reload} className="underline">Retry</button>
        </p>
      )}
      {state.status === "success" && state.data.total > state.data.items.length && (
        <p className="mt-1 text-xs text-slate-500">
          Showing the first {state.data.items.length} of {state.data.total} institutions.
        </p>
      )}
    </div>
  );
}
