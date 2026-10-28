import { useState } from "react";
import { Link } from "react-router-dom";

import DemoNotice from "../components/DemoNotice";
import StatusBadge from "../components/StatusBadge";
import { AsyncView, Note, PageHeader, SeverityBadge } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import { getExceptions, type ExceptionKind, type ExceptionsOut } from "../lib/api";
import { formatDate } from "../lib/format";
import { useAsync } from "../lib/useAsync";

function KindList({ kinds }: { kinds: ExceptionKind[] }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Exception types">
      {kinds.map((k) => (
        <li key={k.key} className="rounded border border-slate-200 bg-white p-3" data-kind={k.key}>
          <p className="text-xs font-medium text-slate-600">{k.label}</p>
          {k.available ? (
            <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{k.total.toLocaleString("en-IN")}</p>
          ) : (
            <p className="mt-1 text-sm font-medium text-slate-700">Unavailable</p>
          )}
          {k.note && <p className="mt-1 text-xs text-slate-600">{k.note}</p>}
        </li>
      ))}
    </ul>
  );
}

function Results({ data }: { data: ExceptionsOut }) {
  const [kind, setKind] = useState("");
  const [severity, setSeverity] = useState("");
  const shown = data.items.filter((e) => (!kind || e.kind === kind) && (!severity || e.severity === severity));
  const labelOf = (key: string) => data.kinds.find((k) => k.key === key)?.label ?? key;
  return (
    <>
      <Note>
        Derived from the records on each request; there is no separate exception list to maintain. Examined{" "}
        {data.scanned.toLocaleString("en-IN")} of {data.eligible_total.toLocaleString("en-IN")} open records on{" "}
        {formatDate(data.generated_on)}
        {data.complete ? "." : ". That is not all of them, so counts are a lower bound."} A document mismatch means a
        document differs from the submitted record and needs a person to look. It is not a finding that anything is
        fraudulent, and nothing here verifies a document with a bank.
      </Note>
      <KindList kinds={data.kinds} />
      <Panel title="Exceptions" description="Most serious first. The filters narrow the exceptions listed below.">
        <div className="mb-4 flex flex-wrap gap-4">
          <div>
            <label htmlFor="exc-kind" className="block text-xs font-medium text-slate-700">Type</label>
            <select id="exc-kind" value={kind} onChange={(e) => setKind(e.target.value)} className="mt-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm">
              <option value="">All types</option>
              {data.kinds.filter((k) => k.available).map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="exc-severity" className="block text-xs font-medium text-slate-700">Severity</label>
            <select id="exc-severity" value={severity} onChange={(e) => setSeverity(e.target.value)} className="mt-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm">
              <option value="">All severities</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </select>
          </div>
        </div>
        {data.items.length === 0 ? (
          <p className="text-sm text-slate-700">No exceptions found in the records examined.</p>
        ) : shown.length === 0 ? (
          <p className="text-sm text-slate-700">No exceptions match the filters.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <caption className="sr-only">Exceptions</caption>
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Severity</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Issue</th>
                  <th scope="col" className="py-2 pr-4 font-medium">FDR</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                  <th scope="col" className="py-2 font-medium"><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {shown.map((e, index) => (
                  <tr key={`${e.kind}-${e.fdr_id}-${index}`} data-kind={e.kind}>
                    <td className="py-2 pr-4"><SeverityBadge severity={e.severity} /></td>
                    <td className="py-2 pr-4 text-slate-800">
                      <span className="block text-xs font-medium text-slate-600">{labelOf(e.kind)}</span>
                      {e.issue}
                    </td>
                    <th scope="row" className="whitespace-nowrap py-2 pr-4 font-medium text-slate-900">{e.fdr_number}</th>
                    <td className="py-2 pr-4 text-slate-700">{e.institution_name}<span className="block text-xs text-slate-500">{e.institution_code}</span></td>
                    <td className="py-2 pr-4"><StatusBadge status={e.status} /></td>
                    <td className="whitespace-nowrap py-2">
                      <Link to={e.link} aria-label={`Open ${e.fdr_number}`} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-800 hover:bg-slate-100">Open</Link>
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
}

export default function Exceptions() {
  const { state, reload } = useAsync((signal) => getExceptions(signal));
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <PageHeader title="Exceptions">Problems that need a person to act, found in the records you are allowed to see.</PageHeader>
      <DemoNotice />
      <AsyncView state={state} reload={reload} what="exceptions">{(data) => <Results data={data} />}</AsyncView>
    </div>
  );
}
