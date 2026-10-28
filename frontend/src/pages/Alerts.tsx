import { Link } from "react-router-dom";

import DemoNotice from "../components/DemoNotice";
import { AsyncView, Note, PageHeader, SeverityBadge } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import { getAlerts, type AlertCategory } from "../lib/api";
import { formatDate } from "../lib/format";
import { useAsync } from "../lib/useAsync";

function CategoryCard({ category }: { category: AlertCategory }) {
  return (
    <li className="rounded border border-slate-200 bg-white p-3" data-category={category.key}>
      <p className="text-xs font-medium text-slate-600">{category.label}</p>
      {category.available ? (
        <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
          {!category.exact && <span className="text-sm font-normal text-slate-600">at least </span>}
          {category.total.toLocaleString("en-IN")}
        </p>
      ) : (
        <p className="mt-1 text-sm font-medium text-slate-700">Unavailable</p>
      )}
      {category.available && category.shown < category.total && (
        <p className="mt-1 text-xs text-slate-600">Showing the first {category.shown} below.</p>
      )}
      {category.note && <p className="mt-1 text-xs text-slate-600">{category.note}</p>}
    </li>
  );
}

export default function Alerts() {
  const { state, reload } = useAsync((signal) => getAlerts(signal));
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <PageHeader title="Alerts">
        Maturities, items waiting for action and problems, for the records you are allowed to see.
      </PageHeader>
      <DemoNotice />
      <AsyncView state={state} reload={reload} what="alerts">
        {(data) => {
          const available = data.categories.filter((c) => c.available);
          const none = available.length > 0 && available.every((c) => c.total === 0);
          return (
            <>
              <Note>
                {data.basis} Computed on {formatDate(data.generated_on)}. Nothing is sent to anyone, and an alert disappears
                when its cause does.
              </Note>
              <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Alert categories">
                {data.categories.map((c) => <CategoryCard key={c.key} category={c} />)}
              </ul>
              <Panel title="Alerts" description="Most urgent first.">
                {data.items.length === 0 ? (
                  <p className="text-sm text-slate-700">
                    {none ? "No alerts right now." : "No alerts to show. Some categories may be unavailable; see the notes above."}
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-left text-sm">
                      <caption className="sr-only">Alerts</caption>
                      <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                        <tr>
                          <th scope="col" className="py-2 pr-4 font-medium">Severity</th>
                          <th scope="col" className="py-2 pr-4 font-medium">FDR</th>
                          <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                          <th scope="col" className="py-2 pr-4 font-medium">Alert</th>
                          <th scope="col" className="py-2 pr-4 font-medium">Date</th>
                          <th scope="col" className="py-2 font-medium"><span className="sr-only">Open</span></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {data.items.map((a, index) => (
                          <tr key={`${a.category}-${a.fdr_id}-${index}`} data-category={a.category}>
                            <td className="py-2 pr-4"><SeverityBadge severity={a.severity} /></td>
                            <th scope="row" className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{a.fdr_number}</th>
                            <td className="py-2 pr-4 text-slate-700">{a.institution_name}<span className="block text-xs text-slate-500">{a.institution_code}</span></td>
                            <td className="py-2 pr-4 text-slate-800">{a.message}</td>
                            <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{a.date ? formatDate(a.date) : "—"}</td>
                            <td className="whitespace-nowrap py-2">
                              <Link to={a.link} aria-label={`Open ${a.fdr_number}`} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-800 hover:bg-slate-100">Open</Link>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
            </>
          );
        }}
      </AsyncView>
    </div>
  );
}
