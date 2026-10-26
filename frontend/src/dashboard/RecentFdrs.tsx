import StatusBadge from "../components/StatusBadge";
import { listFdrs } from "../lib/api";
import { formatDate, formatInr } from "../lib/format";
import { describeError, useAsync } from "../lib/useAsync";
import { Panel, RetryButton } from "./Panel";

const LIMIT = 5;

/** The newest FDR records the signed-in user may see (GET /fdrs is newest-first and role-scoped).
 * This is a list of records, not an activity log. */
export default function RecentFdrs() {
  const { state, reload } = useAsync((signal) => listFdrs({ page_size: LIMIT }, signal));
  return (
    <Panel title="Recently added FDRs" description="Newest records visible to you. This is not an activity log.">
      {state.status === "loading" && (
        <p role="status" className="text-sm text-slate-500">
          Loading records…
        </p>
      )}
      {state.status === "error" && (
        <div role="alert" className="flex items-center justify-between gap-4">
          <p className="text-sm text-red-800">Could not load FDR records. {describeError(state.error)}.</p>
          <RetryButton onClick={reload} />
        </div>
      )}
      {state.status === "success" && state.data.items.length === 0 && (
        <p className="text-sm text-slate-600">
          {state.data.total === 0 ? "No FDR records exist yet." : "No records to show."}
        </p>
      )}
      {state.status === "success" && state.data.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">FDR number</th>
                <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                <th scope="col" className="py-2 pr-4 font-medium">Bank</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Principal</th>
                <th scope="col" className="py-2 pr-4 font-medium">Maturity</th>
                <th scope="col" className="py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {state.data.items.map((fdr) => (
                <tr key={fdr.id}>
                  <td className="py-2 pr-4 font-medium text-slate-900">{fdr.fdr_number}</td>
                  <td className="py-2 pr-4 text-slate-700">{fdr.institution.name}</td>
                  <td className="py-2 pr-4 text-slate-700">{fdr.bank.name}</td>
                  <td className="py-2 pr-4 text-right tabular-nums text-slate-900">{formatInr(fdr.principal_amount)}</td>
                  <td className="py-2 pr-4 text-slate-700">{formatDate(fdr.maturity_date)}</td>
                  <td className="py-2">
                    <StatusBadge status={fdr.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {state.data.total > state.data.items.length && (
            <p className="mt-3 text-xs text-slate-500">
              Showing the newest {state.data.items.length} of {state.data.total.toLocaleString("en-IN")} records.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
