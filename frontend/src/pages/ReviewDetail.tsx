import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import StatusBadge from "../components/StatusBadge";
import { RetryButton } from "../dashboard/Panel";
import DocumentsPanel from "../fdr/DocumentsPanel";
import HistoryPanel from "../fdr/HistoryPanel";
import { describeWriteFailure } from "../fdr/writeErrors";
import { ApiError, decideFdr, getFdr, getHistory, type FdrOut, type ReviewAction } from "../lib/api";
import { formatDate, formatDateTime, formatInr } from "../lib/format";
import { canDecide, canReview } from "../lib/roles";
import { describeError, useAsync } from "../lib/useAsync";
import { NotPermitted } from "./FdrCreate";

interface ActionSpec {
  action: ReviewAction;
  label: string;
  commentLabel: string;
  required: boolean;
  confirmLabel: string;
  approverOnly: boolean;
  notice: string;
  success: (number: string) => string;
}

const ACTIONS: ActionSpec[] = [
  {
    action: "request-correction",
    label: "Request corrections",
    commentLabel: "What needs to be corrected? (required)",
    required: true,
    confirmLabel: "Send back to the institution",
    approverOnly: false,
    notice: "The institution will see your comment and can edit and resubmit this FDR.",
    success: (n) => `Corrections requested for ${n}. The institution has been given your comment.`,
  },
  {
    action: "accept",
    label: "Accept",
    commentLabel: "Comment (optional)",
    required: false,
    confirmLabel: "Confirm acceptance",
    approverOnly: true,
    notice:
      "Accepting records that the submitted information was accepted after human review. It does not verify the FDR with the bank.",
    success: (n) => `${n} was accepted.`,
  },
  {
    action: "reject",
    label: "Reject",
    commentLabel: "Reason for rejection (required)",
    required: true,
    confirmLabel: "Confirm rejection",
    approverOnly: true,
    notice: "The institution will see your reason. A rejected FDR cannot be changed or resubmitted.",
    success: (n) => `${n} was rejected.`,
  },
];

/** Route entry: data hooks live in ReviewContent, so roles that may not review never request anything. */
export default function ReviewDetail() {
  const { state: auth } = useAuth();
  const role = auth.status === "authenticated" ? auth.user.role : "";
  return canReview(role) ? <ReviewContent role={role} /> : <NotPermitted what="Review FDR" />;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-900">{children}</dd>
    </div>
  );
}

function ReviewContent({ role }: { role: string }) {
  const { id = "" } = useParams();
  const fdrState = useAsync((signal) => getFdr(id, signal), id);
  const historyState = useAsync((signal) => getHistory(id, signal), id);
  const reloadAll = () => {
    fdrState.reload();
    historyState.reload();
  };

  const back = (
    <Link to="/review" className="text-sm font-medium text-slate-900 underline">
      Back to the review queue
    </Link>
  );

  if (fdrState.state.status === "loading") {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <h1 className="text-2xl font-semibold text-slate-900">Review FDR</h1>
        <p role="status" className="text-sm text-slate-500">
          Loading FDR…
        </p>
      </div>
    );
  }
  if (fdrState.state.status === "error") {
    const notFound = fdrState.state.error instanceof ApiError && fdrState.state.error.status === 404;
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <h1 className="text-2xl font-semibold text-slate-900">Review FDR</h1>
        <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded border border-slate-200 bg-white p-4">
          <p className="text-sm text-red-800">
            {notFound ? "This FDR was not found." : `Could not load this FDR. ${describeError(fdrState.state.error)}.`}
          </p>
          {!notFound && <RetryButton onClick={fdrState.reload} />}
        </div>
        {back}
      </div>
    );
  }

  const fdr = fdrState.state.data;
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Review FDR</h1>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-600">
          <span className="font-medium text-slate-900">{fdr.fdr_number}</span>
          <StatusBadge status={fdr.status} />
          <span>Version {fdr.row_version}</span>
        </p>
      </div>

      <DemoNotice />

      <div role="note" className="rounded border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-800">
        <strong className="font-semibold">Submitted information, not verified information.</strong> These details are
        exactly what the institution entered. They have not been checked with the bank or against any document. A
        discrepancy found on review means human follow-up is needed, nothing more.
      </div>

      <section aria-labelledby="details-heading" className="rounded border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 id="details-heading" className="text-sm font-semibold text-slate-900">
            Submitted details
          </h2>
        </div>
        <dl className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Institution">
            {fdr.institution.name} <span className="text-slate-500">({fdr.institution.code})</span>
          </Field>
          <Field label="Bank">{fdr.bank.name}</Field>
          <Field label="FDR number">{fdr.fdr_number}</Field>
          <Field label="Branch">{fdr.branch_name ?? <span className="text-slate-400">Not provided</span>}</Field>
          <Field label="Depositor">{fdr.depositor_name ?? <span className="text-slate-400">Not provided</span>}</Field>
          <Field label="Purpose">{fdr.purpose ?? <span className="text-slate-400">Not provided</span>}</Field>
          <Field label="Principal amount">{formatInr(fdr.principal_amount)}</Field>
          <Field label="Interest rate">
            {fdr.interest_rate_pct === null ? <span className="text-slate-400">Not provided</span> : `${fdr.interest_rate_pct}%`}
          </Field>
          <Field label="Issue date">{formatDate(fdr.issue_date)}</Field>
          <Field label="Maturity date">{formatDate(fdr.maturity_date)}</Field>
          <Field label="Last submitted">{fdr.submitted_at ? formatDateTime(fdr.submitted_at) : "Not submitted"}</Field>
          <Field label="Decided">{fdr.decided_at ? formatDateTime(fdr.decided_at) : "No decision yet"}</Field>
        </dl>
      </section>

      <DocumentsPanel fdrId={fdr.id} mode="view" checks />

      <DecisionPanel key={`${fdr.id}-${fdr.row_version}`} fdr={fdr} role={role} onReload={reloadAll} />

      <HistoryPanel state={historyState.state} reload={historyState.reload} />
      {back}
    </div>
  );
}

function DecisionPanel({ fdr, role, onReload }: { fdr: FdrOut; role: string; onReload: () => void }) {
  const navigate = useNavigate();
  const [selected, setSelected] = useState<ActionSpec | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  if (fdr.status !== "SUBMITTED") {
    return (
      <section aria-labelledby="decision-heading" className="rounded border border-slate-200 bg-white p-4">
        <h2 id="decision-heading" className="text-sm font-semibold text-slate-900">
          Decision
        </h2>
        <p className="mt-2 text-sm text-slate-700">
          {fdr.status === "CORRECTION_REQUESTED"
            ? "Corrections have been requested. This FDR returns to the queue when the institution resubmits it."
            : `This FDR is ${fdr.status.toLowerCase().replace("_", " ")}. No further review action is available.`}
        </p>
      </section>
    );
  }

  const available = ACTIONS.filter((a) => !a.approverOnly || canDecide(role));

  async function confirm() {
    if (!selected || busy) return;
    const text = comment.trim();
    if (selected.required && !text) {
      setError("A comment is required.");
      return;
    }
    setBusy(true);
    setError(null);
    setConflict(false);
    try {
      await decideFdr(fdr.id, selected.action, fdr.row_version, text || null);
      navigate("/review", { state: { flash: selected.success(fdr.fdr_number) } });
    } catch (err) {
      const failure = describeWriteFailure(err);
      setError(failure.fields._form ?? failure.message ?? "The decision could not be recorded.");
      setConflict(err instanceof ApiError && err.status === 409);
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="decision-heading" className="rounded border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <h2 id="decision-heading" className="text-sm font-semibold text-slate-900">
          Decision
        </h2>
        <p className="mt-0.5 text-xs text-slate-500">
          {canDecide(role)
            ? "As an approver you can send this FDR back, accept it, or reject it."
            : "As a reviewer you can send this FDR back for correction. Accepting and rejecting are done by approvers."}
        </p>
      </div>
      <div className="space-y-4 p-4">
        <div className="flex flex-wrap gap-3">
          {available.map((spec) => (
            <button
              key={spec.action}
              type="button"
              disabled={busy}
              aria-pressed={selected?.action === spec.action}
              onClick={() => {
                setSelected(spec);
                setError(null);
                setConflict(false);
              }}
              className={`rounded border px-4 py-2 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-50 ${
                selected?.action === spec.action
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-300 text-slate-800 hover:bg-slate-100"
              }`}
            >
              {spec.label}
            </button>
          ))}
        </div>

        {error && (
          <div role="alert" className="space-y-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            <p>{error}</p>
            {conflict && <RetryButton onClick={onReload} label="Reload the latest version" />}
          </div>
        )}

        {selected && (
          <div role="group" aria-labelledby="confirm-title" className="space-y-3 rounded border border-slate-900 p-4">
            <h3 id="confirm-title" className="text-sm font-semibold text-slate-900">
              {selected.label}: {fdr.fdr_number}
            </h3>
            <p className="text-sm text-slate-700">{selected.notice}</p>
            <div>
              <label htmlFor="decision-comment" className="block text-sm font-medium text-slate-700">
                {selected.commentLabel}
              </label>
              <textarea
                id="decision-comment"
                rows={4}
                maxLength={2000}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                disabled={busy}
                aria-required={selected.required}
                className="mt-1 block w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-300 disabled:bg-slate-100"
              />
            </div>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={confirm}
                disabled={busy}
                className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-500"
              >
                {busy ? "Recording…" : selected.confirmLabel}
              </button>
              <button
                type="button"
                onClick={() => {
                  setSelected(null);
                  setComment("");
                  setError(null);
                }}
                disabled={busy}
                className="rounded border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
