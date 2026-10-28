import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { Alert } from "../components/FormParts";
import StatusBadge from "../components/StatusBadge";
import { RetryButton } from "../dashboard/Panel";
import DocumentsPanel from "../fdr/DocumentsPanel";
import FdrForm, { type FieldErrors } from "../fdr/FdrForm";
import { describeWriteFailure } from "../fdr/writeErrors";
import { isDirty, toInput, valuesFromFdr, type FdrFormValues } from "../fdr/validation";
import { ApiError, getFdr, getHistory, submitFdr, updateFdr, type FdrOut } from "../lib/api";
import { useUnsavedChanges } from "../lib/useUnsavedChanges";
import { latestCorrection } from "../fdr/HistoryPanel";
import { formatDateTime } from "../lib/format";
import { roleLabel } from "../lib/roles";
import { describeError, useAsync } from "../lib/useAsync";
import { NotPermitted } from "./FdrCreate";

const EDITABLE_STATUSES = ["DRAFT", "CORRECTION_REQUESTED"];

/** The reviewer's reason for sending the record back: what the institution needs to act on. */
function ReturnedNotice({ id }: { id: string }) {
  const { state, reload } = useAsync((signal) => getHistory(id, signal), id);
  const entry = state.status === "success" ? latestCorrection(state.data.history) : null;
  return (
    <section aria-labelledby="returned-title" className="rounded border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
      <h2 id="returned-title" className="font-semibold">
        Corrections requested
      </h2>
      {state.status === "loading" && (
        <p role="status" className="mt-1">
          Loading the reviewer&apos;s comment…
        </p>
      )}
      {state.status === "error" && (
        <div role="alert" className="mt-1 flex flex-wrap items-center gap-3">
          <span>Could not load the reviewer&apos;s comment. {describeError(state.error)}.</span>
          <RetryButton onClick={reload} />
        </div>
      )}
      {state.status === "success" && entry && (
        <>
          <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-amber-500 pl-3">
            {entry.comment ?? "No comment was recorded."}
          </blockquote>
          <p className="mt-1 text-xs">
            {roleLabel(entry.actor_role)} · {formatDateTime(entry.occurred_at)}
          </p>
        </>
      )}
      <p className="mt-2">Make the corrections below, save them, then resubmit the FDR for review.</p>
    </section>
  );
}

export default function FdrEdit() {
  const { id = "" } = useParams();
  const { state: auth } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  // A one-off message handed over by the page that sent us here; cleared from history so a refresh
  // does not show it again.
  const [flash, setFlash] = useState<string | null>((location.state as { flash?: string } | null)?.flash ?? null);
  useEffect(() => {
    if ((location.state as { flash?: string } | null)?.flash) navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  const loaded = useAsync((signal) => getFdr(id, signal), id);
  const [saved, setSaved] = useState<FdrOut | null>(null); // the latest server copy after our own save
  const [edits, setEdits] = useState<FdrFormValues | null>(null); // null = untouched
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [message, setMessage] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useUnsavedChanges(edits !== null && !saving && !submitting); // nothing is kept in browser storage

  if (auth.status !== "authenticated" || auth.user.role !== "INSTITUTION_USER") return <NotPermitted what="Edit FDR" />;

  const reload = () => {
    setSaved(null);
    setEdits(null);
    setMessage(null);
    setStale(false);
    setConfirming(false);
    loaded.reload();
  };

  const header = (title: string) => (
    <div>
      <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
    </div>
  );

  if (loaded.state.status === "loading") {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        {header("Edit FDR")}
        <p role="status" className="text-sm text-slate-500">
          Loading FDR…
        </p>
      </div>
    );
  }
  if (loaded.state.status === "error") {
    const notFound = loaded.state.error instanceof ApiError && loaded.state.error.status === 404;
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        {header("Edit FDR")}
        <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded border border-slate-200 bg-white p-4">
          <p className="text-sm text-red-800">
            {notFound ? "This FDR was not found." : `Could not load this FDR. ${describeError(loaded.state.error)}.`}
          </p>
          {!notFound && <RetryButton onClick={loaded.reload} />}
        </div>
        <Link to="/fdrs" className="text-sm font-medium text-slate-900 underline">
          Back to the FDR Registry
        </Link>
      </div>
    );
  }

  const fdr = saved ?? loaded.state.data;

  if (!EDITABLE_STATUSES.includes(fdr.status)) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        {header("Edit FDR")}
        <div role="status" className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-700">
          <p className="font-medium">{fdr.fdr_number}</p>
          <p className="mt-2 flex items-center gap-2">
            Status: <StatusBadge status={fdr.status} />
          </p>
          <p className="mt-2">Only drafts and FDRs returned for correction can be edited. This FDR can no longer be changed here.</p>
        </div>
        <DocumentsPanel fdrId={fdr.id} mode="view" />
        <Link to="/fdrs" className="text-sm font-medium text-slate-900 underline">
          Back to the FDR Registry
        </Link>
      </div>
    );
  }

  const returned = fdr.status === "CORRECTION_REQUESTED";
  const savedValues = valuesFromFdr(fdr);
  const values = edits ?? savedValues;
  const dirty = edits !== null && isDirty(edits, savedValues);

  const fail = (err: unknown) => {
    const failure = describeWriteFailure(err);
    setFieldErrors(failure.fields);
    setMessage(failure.message);
    setStale(failure.stale);
  };

  async function save() {
    setSaving(true);
    setFieldErrors({});
    setMessage(null);
    setNotice(null);
    setFlash(null);
    setStale(false);
    try {
      const updated = await updateFdr(fdr.id, fdr.row_version, toInput(values));
      setSaved(updated);
      setEdits(null);
      setNotice("Changes saved.");
    } catch (err) {
      fail(err);
    } finally {
      setSaving(false);
    }
  }

  async function submit() {
    setSubmitting(true);
    setMessage(null);
    setStale(false);
    try {
      await submitFdr(fdr.id, fdr.row_version);
      navigate("/fdrs", {
        state: {
          flash: returned
            ? `${fdr.fdr_number} was resubmitted for review. It can no longer be edited.`
            : `${fdr.fdr_number} was submitted for review. It can no longer be edited.`,
        },
      });
    } catch (err) {
      fail(err);
      setConfirming(false);
      setSubmitting(false);
    }
  }

  const busy = saving || submitting;
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Edit FDR</h1>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-600">
          <span className="font-medium text-slate-900">{fdr.fdr_number}</span>
          <StatusBadge status={fdr.status} />
          <span>{returned ? "Returned by AICTE for correction." : "Only you and your institution can see this draft."}</span>
        </p>
      </div>
      <DemoNotice />

      {returned && <ReturnedNotice id={fdr.id} />}

      {flash && <Alert tone="info">{flash}</Alert>}
      {notice && <Alert tone="info">{notice}</Alert>}
      {message && (
        <div role="alert" className="space-y-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          <p>{message}</p>
          {stale && <RetryButton onClick={reload} label="Reload the latest version" />}
        </div>
      )}

      <section className="rounded border border-slate-200 bg-white p-4 sm:p-6" aria-label="FDR details">
        <FdrForm
          values={values}
          onChange={setEdits}
          onSave={save}
          saving={saving}
          disabled={submitting}
          serverErrors={fieldErrors}
          saveLabel="Save changes"
        >
          <button
            type="button"
            onClick={() => setConfirming(true)}
            disabled={busy || dirty || confirming}
            className="rounded border border-slate-900 px-4 py-2.5 text-sm font-medium text-slate-900 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {returned ? "Resubmit for review" : "Submit for review"}
          </button>
          <Link to="/fdrs" className="text-sm font-medium text-slate-700 underline">
            Back to the registry
          </Link>
        </FdrForm>
        {dirty && <p className="mt-3 text-xs text-slate-600">You have unsaved changes. Save them before submitting.</p>}
      </section>

      {confirming && (
        <section role="alertdialog" aria-labelledby="submit-title" aria-describedby="submit-text" className="rounded border border-slate-900 bg-white p-4 sm:p-6">
          <h2 id="submit-title" className="text-base font-semibold text-slate-900">
            {returned ? "Resubmit this FDR for review?" : "Submit this FDR for review?"}
          </h2>
          <p id="submit-text" className="mt-2 text-sm text-slate-700">
            {returned
              ? `Once resubmitted, ${fdr.fdr_number} returns to the AICTE review queue and can no longer be edited. Your earlier submission is kept. This does not verify the FDR with the bank.`
              : `Once submitted, ${fdr.fdr_number} becomes visible to AICTE reviewers and can no longer be edited. This does not verify the FDR with the bank.`}
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-500"
            >
              {submitting ? (returned ? "Resubmitting…" : "Submitting…") : returned ? "Confirm resubmission" : "Confirm submission"}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={submitting}
              className="rounded border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      <DocumentsPanel fdrId={fdr.id} mode="manage" />
    </div>
  );
}
