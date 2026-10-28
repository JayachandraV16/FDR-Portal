import { useState, type FormEvent, type ReactNode } from "react";

import { Alert, SelectField, SubmitButton, TextField } from "../components/FormParts";
import { RetryButton } from "../dashboard/Panel";
import { listBanks } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { FORM_FIELDS, validateFdrForm, type FdrFormValues } from "./validation";

export type FieldErrors = Partial<Record<keyof FdrFormValues | "_form", string>>;

/** The shared create/edit form. It only collects and validates input; the page decides what saving
 * does. The institution is never a field: the server uses the signed-in user's own. */
export default function FdrForm({
  values,
  onChange,
  onSave,
  saving,
  serverErrors,
  saveLabel,
  disabled = false,
  children,
}: {
  values: FdrFormValues;
  onChange: (next: FdrFormValues) => void;
  onSave: () => void;
  saving: boolean;
  serverErrors: FieldErrors;
  saveLabel: string;
  disabled?: boolean;
  children?: ReactNode; // extra actions rendered beside the save button
}) {
  const banks = useAsync((signal) => listBanks({ page_size: 500 }, signal));
  const [showClientErrors, setShowClientErrors] = useState(false);
  const clientErrors = showClientErrors ? validateFdrForm(values) : {};
  const errors: FieldErrors = { ...serverErrors, ...clientErrors };
  const set = (field: keyof FdrFormValues) => (value: string) => onChange({ ...values, [field]: value });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || disabled) return;
    if (Object.keys(validateFdrForm(values)).length > 0) {
      setShowClientErrors(true);
      return;
    }
    onSave();
  }

  const text = (field: keyof FdrFormValues, label: string, extra: Record<string, unknown> = {}) => (
    <TextField
      label={label}
      name={field}
      value={values[field]}
      onChange={(e) => set(field)(e.target.value)}
      error={errors[field]}
      disabled={saving || disabled}
      {...extra}
    />
  );

  return (
    <form onSubmit={submit} noValidate className="space-y-5">
      {errors._form && <Alert tone="error">{errors._form}</Alert>}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <SelectField
            label="Bank"
            name="bank_id"
            required
            value={values.bank_id}
            onChange={(e) => set("bank_id")(e.target.value)}
            error={errors.bank_id}
            disabled={saving || disabled || banks.state.status !== "success"}
          >
            <option value="">{banks.state.status === "loading" ? "Loading banks…" : "Choose a bank"}</option>
            {banks.state.status === "success" &&
              banks.state.data.items.map((bank) => (
                <option key={bank.id} value={bank.id}>
                  {bank.name}
                </option>
              ))}
          </SelectField>
          {banks.state.status === "error" && (
            <div role="alert" className="mt-1 flex items-center gap-2 text-xs text-red-800">
              Banks could not be loaded. <RetryButton onClick={banks.reload} />
            </div>
          )}
        </div>
        {text("fdr_number", "FDR number", { required: true, maxLength: 64 })}
        {text("principal_amount", "Principal amount (INR)", {
          required: true, inputMode: "decimal", hint: "Digits only, up to two decimal places. For example 1500000.00.",
        })}
        {text("interest_rate_pct", "Interest rate (% per year)", { inputMode: "decimal", hint: "Optional." })}
        {text("issue_date", "Issue date", { type: "date", required: true })}
        {text("maturity_date", "Maturity date", { type: "date", required: true })}
        {text("branch_name", "Branch name", { maxLength: 200, hint: "Required before the FDR can be submitted." })}
        {text("depositor_name", "Depositor name", { maxLength: 300, hint: "Required before the FDR can be submitted." })}
        <div className="sm:col-span-2">{text("purpose", "Purpose", { maxLength: 200, hint: "Optional." })}</div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="w-full sm:w-auto sm:min-w-48">
          <SubmitButton busy={saving} busyLabel="Saving…">
            {saveLabel}
          </SubmitButton>
        </div>
        {children}
      </div>
    </form>
  );
}

export { FORM_FIELDS };
