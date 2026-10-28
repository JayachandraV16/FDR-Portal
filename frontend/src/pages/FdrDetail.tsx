import { Link, useParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import StatusBadge from "../components/StatusBadge";
import { AsyncView, Note, PageHeader } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import { getFdr, getHistory, type FdrOut } from "../lib/api";
import { fdrStatusLabel, formatDate, formatDateTime, formatInr } from "../lib/format";
import { canReview, roleLabel } from "../lib/roles";
import { useAsync } from "../lib/useAsync";

const EDITABLE = ["DRAFT", "CORRECTION_REQUESTED"];
const missing = <span className="text-slate-400">Not provided</span>;

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-900">{children}</dd>
    </div>
  );
}

/** Only links to pages the role can use; each destination still enforces access on the server. */
function Actions({ fdr, role }: { fdr: FdrOut; role: string }) {
  const link = "rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-100";
  const isInstitution = role === "INSTITUTION_USER";
  return (
    <div className="flex flex-wrap gap-2">
      {isInstitution && EDITABLE.includes(fdr.status) && (
        <Link to={`/fdrs/${fdr.id}/edit`} className={link}>{fdr.status === "DRAFT" ? "Edit draft" : "Make corrections"}</Link>
      )}
      {(isInstitution || canReview(role)) && <Link to={`/documents/${fdr.id}`} className={link}>Documents</Link>}
      {canReview(role) && <Link to={`/review/${fdr.id}`} className={link}>Open for review</Link>}
      {canReview(role) && <Link to={`/verification/${fdr.id}`} className={link}>Verification</Link>}
      <Link to={`/release/${fdr.id}`} className={link}>Release / refund</Link>
    </div>
  );
}

function History({ id }: { id: string }) {
  const { state, reload } = useAsync((signal) => getHistory(id, signal), id);
  return (
    <Panel title="Status history" description="Every change of status, oldest first. People are identified by role only.">
      <AsyncView state={state} reload={reload} what="the status history">
        {(data) =>
          data.history.length === 0 ? (
            <p className="text-sm text-slate-700">No status changes are recorded yet.</p>
          ) : (
            <ol className="space-y-3">
              {data.history.map((h, i) => (
                <li key={`${h.occurred_at}-${i}`} className="text-sm">
                  <p className="text-slate-900">
                    {h.from_status ? `${fdrStatusLabel(h.from_status)} → ${fdrStatusLabel(h.to_status)}` : `Created as ${fdrStatusLabel(h.to_status)}`}
                  </p>
                  <p className="text-xs text-slate-500">{roleLabel(h.actor_role)} · {formatDateTime(h.occurred_at)}</p>
                  {h.comment && <p className="mt-1 whitespace-pre-wrap break-words text-xs text-slate-700">{h.comment}</p>}
                </li>
              ))}
            </ol>
          )
        }
      </AsyncView>
    </Panel>
  );
}

export default function FdrDetail() {
  const { id = "" } = useParams();
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  const { state, reload } = useAsync((signal) => getFdr(id, signal), id);
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title="FDR details" />
      <DemoNotice />
      <AsyncView state={state} reload={reload} what="this FDR">
        {(fdr) => (
          <>
            <Panel title={fdr.fdr_number} description={fdr.institution.name}>
              <dl className="grid gap-4 sm:grid-cols-2">
                <Field label="Status"><StatusBadge status={fdr.status} /></Field>
                <Field label="Bank">{fdr.bank.name}</Field>
                <Field label="Branch">{fdr.branch_name ?? missing}</Field>
                <Field label="Depositor">{fdr.depositor_name ?? missing}</Field>
                <Field label="Principal (as submitted)">{formatInr(fdr.principal_amount)}</Field>
                <Field label="Interest rate">{fdr.interest_rate_pct === null ? missing : `${fdr.interest_rate_pct}%`}</Field>
                <Field label="Issue date">{formatDate(fdr.issue_date)}</Field>
                <Field label="Maturity date">{formatDate(fdr.maturity_date)}</Field>
                <Field label="Purpose">{fdr.purpose ?? missing}</Field>
                <Field label="Submitted">{fdr.submitted_at ? formatDateTime(fdr.submitted_at) : "Not submitted"}</Field>
                <Field label="Decided">{fdr.decided_at ? formatDateTime(fdr.decided_at) : "Not decided"}</Field>
                <Field label="Last updated">{formatDateTime(fdr.updated_at)}</Field>
              </dl>
              <div className="mt-5"><Actions fdr={fdr} role={role} /></div>
            </Panel>
            <Note>
              These figures are what the institution entered. “Accepted” means accepted after human review of the
              submitted information; it does not mean the FDR has been verified with the bank.
            </Note>
            <History id={fdr.id} />
          </>
        )}
      </AsyncView>
      <Link to="/fdrs" className="text-sm font-medium text-slate-900 underline">Back to the registry</Link>
    </div>
  );
}
