import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { Alert } from "../components/FormParts";
import FdrForm, { type FieldErrors } from "../fdr/FdrForm";
import { describeWriteFailure } from "../fdr/writeErrors";
import { EMPTY_VALUES, toInput, type FdrFormValues } from "../fdr/validation";
import { createFdr } from "../lib/api";
import { useUnsavedChanges } from "../lib/useUnsavedChanges";

export function NotPermitted({ what }: { what: string }) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold text-slate-900">{what}</h1>
      <div role="alert" className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-700">
        Only institution users can do this. Your account can view FDR records but not create or change them.
      </div>
      <Link to="/fdrs" className="text-sm font-medium text-slate-900 underline">
        Back to the FDR Registry
      </Link>
    </div>
  );
}

export default function FdrCreate() {
  const { state: auth } = useAuth();
  const navigate = useNavigate();
  const [values, setValues] = useState<FdrFormValues>(EMPTY_VALUES);
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [message, setMessage] = useState<string | null>(null);
  useUnsavedChanges(JSON.stringify(values) !== JSON.stringify(EMPTY_VALUES) && !saving); // nothing is kept in browser storage

  if (auth.status !== "authenticated" || auth.user.role !== "INSTITUTION_USER") return <NotPermitted what="Create FDR" />;

  async function save() {
    setSaving(true);
    setFieldErrors({});
    setMessage(null);
    try {
      const fdr = await createFdr(toInput(values));
      navigate(`/fdrs/${fdr.id}/edit`, {
        replace: true,
        state: { flash: "Draft saved. You can keep editing it, or submit it for review when it is complete." },
      });
    } catch (err) {
      const failure = describeWriteFailure(err);
      setFieldErrors(failure.fields);
      setMessage(failure.message);
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Create FDR</h1>
        <p className="mt-1 text-sm text-slate-600">
          The FDR is saved as a draft in your institution. Reviewers cannot see it until you submit it. You can attach supporting documents once the draft is saved.
        </p>
      </div>
      <DemoNotice />
      {message && <Alert tone="error">{message}</Alert>}
      <section className="rounded border border-slate-200 bg-white p-4 sm:p-6" aria-label="FDR details">
        <FdrForm
          values={values}
          onChange={setValues}
          onSave={save}
          saving={saving}
          serverErrors={fieldErrors}
          saveLabel="Save as draft"
        >
          <Link to="/fdrs" className="text-sm font-medium text-slate-700 underline">
            Cancel
          </Link>
        </FdrForm>
      </section>
    </div>
  );
}
