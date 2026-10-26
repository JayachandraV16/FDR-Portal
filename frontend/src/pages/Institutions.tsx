import { useState } from "react";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { RetryButton } from "../dashboard/Panel";
import { listInstitutions, type InstitutionOut } from "../lib/api";
import { formatDate } from "../lib/format";
import { describeError, useAsync } from "../lib/useAsync";

const PAGE_SIZE = 25;

function matchesFilter(row: InstitutionOut, filter: string): boolean {
  const needle = filter.trim().toLowerCase();
  if (!needle) return true;
  return [row.code, row.name, row.state, row.institution_type, row.status, row.contact_email ?? ""].some((value) =>
    value.toLowerCase().includes(needle),
  );
}

export default function Institutions() {
  const { state: auth } = useAuth();
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState("");
  // Pagination is done by the server (limit/offset); `total` is the complete count for this user's scope.
  const { state, reload } = useAsync(
    (signal) => listInstitutions({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }, signal),
    page,
  );
  const ownInstitutionOnly = auth.status === "authenticated" && auth.user.role === "INSTITUTION_USER";

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Institutions</h1>
        <p className="mt-1 text-sm text-slate-600">
          {ownInstitutionOnly
            ? "The institution linked to your account. This list is read-only."
            : "Institutions recorded in the portal. This list is read-only."}
        </p>
      </div>

      <DemoNotice />

      <section aria-labelledby="institutions-heading" className="rounded border border-slate-200 bg-white">
        <div className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 px-4 py-3">
          <div>
            <h2 id="institutions-heading" className="text-sm font-semibold text-slate-900">
              Institution register
            </h2>
            {state.status === "success" && (
              <p className="mt-0.5 text-xs text-slate-500" data-testid="institution-count">
                {state.data.total.toLocaleString("en-IN")} {state.data.total === 1 ? "institution" : "institutions"}
                {ownInstitutionOnly ? " linked to your account" : " in total"}
              </p>
            )}
          </div>
          {state.status === "success" && state.data.items.length > 0 && (
            <div className="w-full sm:w-72">
              <label htmlFor="institution-filter" className="block text-xs font-medium text-slate-700">
                Filter this page
              </label>
              <input
                id="institution-filter"
                type="search"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                aria-describedby="institution-filter-note"
                className="mt-1 block w-full rounded border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-300"
              />
              <p id="institution-filter-note" className="mt-1 text-xs text-slate-500">
                Filters only the {state.data.items.length} rows shown on this page; the server does not support search yet.
              </p>
            </div>
          )}
        </div>

        <div className="p-4">
          {state.status === "loading" && (
            <p role="status" className="text-sm text-slate-500">
              Loading institutions…
            </p>
          )}

          {state.status === "error" && (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-red-800">Could not load institutions.</p>
                <p className="mt-0.5 text-xs text-slate-600">{describeError(state.error)}.</p>
              </div>
              <RetryButton onClick={reload} />
            </div>
          )}

          {state.status === "success" && state.data.total === 0 && (
            <div>
              <p className="text-sm font-medium text-slate-700">
                {ownInstitutionOnly ? "No institution is linked to your account." : "No institutions have been added yet."}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {ownInstitutionOnly
                  ? "Contact your administrator to have your account linked to an institution."
                  : "Records will appear here once institutions exist in the portal."}
              </p>
            </div>
          )}

          {state.status === "success" && state.data.total > 0 && state.data.items.length === 0 && (
            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="text-sm text-slate-700">There are no institutions on this page.</p>
              <RetryButton onClick={() => setPage(1)} label="Go to the first page" />
            </div>
          )}

          {state.status === "success" && state.data.items.length > 0 && (
            <ResultTable
              rows={state.data.items}
              filter={filter}
              clearFilter={() => setFilter("")}
              hasOtherPages={state.data.total > state.data.items.length}
            />
          )}

          {state.status === "success" && state.data.total > PAGE_SIZE && (
            <nav aria-label="Pagination" className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3 text-sm">
              <p className="text-xs text-slate-600">
                Showing {state.data.offset + 1}–{state.data.offset + state.data.items.length} of{" "}
                {state.data.total.toLocaleString("en-IN")}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPage((p) => p - 1)}
                  disabled={page <= 1}
                  className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Previous
                </button>
                <span className="text-xs text-slate-600">
                  Page {page} of {Math.ceil(state.data.total / PAGE_SIZE)}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => p + 1)}
                  disabled={state.data.offset + state.data.items.length >= state.data.total}
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

function ResultTable({
  rows,
  filter,
  clearFilter,
  hasOtherPages,
}: {
  rows: InstitutionOut[];
  filter: string;
  clearFilter: () => void;
  hasOtherPages: boolean;
}) {
  const visible = rows.filter((row) => matchesFilter(row, filter));

  if (visible.length === 0) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-4" role="status">
        <p className="text-sm text-slate-700">
          No institutions on this page match “{filter.trim()}”.
          {hasOtherPages && " Other pages are not searched."}
        </p>
        <RetryButton onClick={clearFilter} label="Clear filter" />
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">Institutions</caption>
        <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" className="py-2 pr-4 font-medium">Code</th>
            <th scope="col" className="py-2 pr-4 font-medium">Name</th>
            <th scope="col" className="py-2 pr-4 font-medium">State</th>
            <th scope="col" className="py-2 pr-4 font-medium">Type</th>
            <th scope="col" className="py-2 pr-4 font-medium">Status</th>
            <th scope="col" className="py-2 pr-4 font-medium">Contact email</th>
            <th scope="col" className="py-2 font-medium">Added to portal</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {visible.map((row) => (
            <tr key={row.id}>
              <td className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{row.code}</td>
              <th scope="row" className="py-2 pr-4 font-normal text-slate-900">{row.name}</th>
              <td className="py-2 pr-4 text-slate-700">{row.state}</td>
              <td className="py-2 pr-4 text-slate-700">{row.institution_type}</td>
              <td className="py-2 pr-4">
                <span className="rounded border border-slate-300 bg-slate-50 px-2 py-0.5 text-xs capitalize text-slate-700">
                  {row.status}
                </span>
              </td>
              <td className="py-2 pr-4 text-slate-700">
                {row.contact_email ?? <span className="text-slate-400">Not provided</span>}
              </td>
              <td className="whitespace-nowrap py-2 text-slate-700">{formatDate(row.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {filter.trim() && (
        <p className="mt-3 text-xs text-slate-500">
          Showing {visible.length} of {rows.length} rows on this page.
        </p>
      )}
    </div>
  );
}
