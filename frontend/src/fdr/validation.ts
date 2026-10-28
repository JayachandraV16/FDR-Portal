import type { FdrInput, FdrOut } from "../lib/api";

/** Text-box state of the FDR form. Everything is a string so nothing is rounded on the way in. */
export interface FdrFormValues {
  bank_id: string;
  fdr_number: string;
  branch_name: string;
  depositor_name: string;
  purpose: string;
  principal_amount: string;
  interest_rate_pct: string;
  issue_date: string;
  maturity_date: string;
}

export const FORM_FIELDS = [
  "bank_id", "fdr_number", "branch_name", "depositor_name", "purpose",
  "principal_amount", "interest_rate_pct", "issue_date", "maturity_date",
] as const satisfies ReadonlyArray<keyof FdrFormValues>;

export const EMPTY_VALUES: FdrFormValues = {
  bank_id: "", fdr_number: "", branch_name: "", depositor_name: "", purpose: "",
  principal_amount: "", interest_rate_pct: "", issue_date: "", maturity_date: "",
};

export const valuesFromFdr = (fdr: FdrOut): FdrFormValues => ({
  bank_id: fdr.bank.id,
  fdr_number: fdr.fdr_number,
  branch_name: fdr.branch_name ?? "",
  depositor_name: fdr.depositor_name ?? "",
  purpose: fdr.purpose ?? "",
  principal_amount: fdr.principal_amount,
  interest_rate_pct: fdr.interest_rate_pct ?? "",
  issue_date: fdr.issue_date,
  maturity_date: fdr.maturity_date,
});

const MAX = { fdr_number: 64, branch_name: 200, depositor_name: 300, purpose: 200 } as const;
const MONEY = /^\d{1,12}(\.\d{1,2})?$/; // Numeric(14, 2)
const RATE = /^\d{1,3}(\.\d{1,2})?$/; // Numeric(5, 2)
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Convenience checks only; the backend remains the authority and its messages are shown too. */
export function validateFdrForm(v: FdrFormValues): Partial<Record<keyof FdrFormValues, string>> {
  const errors: Partial<Record<keyof FdrFormValues, string>> = {};
  if (!v.bank_id) errors.bank_id = "Choose a bank.";
  const number = v.fdr_number.trim();
  if (!number) errors.fdr_number = "Enter the FDR number.";
  else if (number.length > MAX.fdr_number) errors.fdr_number = `At most ${MAX.fdr_number} characters.`;
  for (const field of ["branch_name", "depositor_name", "purpose"] as const) {
    if (v[field].trim().length > MAX[field]) errors[field] = `At most ${MAX[field]} characters.`;
  }
  const amount = v.principal_amount.trim();
  if (!amount) errors.principal_amount = "Enter the principal amount.";
  else if (!MONEY.test(amount)) errors.principal_amount = "Use digits with at most two decimal places, up to 12 digits before the point.";
  else if (/^0+(\.0+)?$/.test(amount)) errors.principal_amount = "The amount must be greater than zero.";
  const rate = v.interest_rate_pct.trim();
  if (rate) {
    if (!RATE.test(rate)) errors.interest_rate_pct = "Use digits with at most two decimal places.";
    else if (Number(rate) > 100) errors.interest_rate_pct = "The rate cannot exceed 100.";
  }
  if (!DATE.test(v.issue_date)) errors.issue_date = "Enter the issue date.";
  if (!DATE.test(v.maturity_date)) errors.maturity_date = "Enter the maturity date.";
  if (!errors.issue_date && !errors.maturity_date && v.maturity_date <= v.issue_date) {
    errors.maturity_date = "The maturity date must be after the issue date.";
  }
  return errors;
}

const orNull = (text: string): string | null => text.trim() || null;

export function toInput(v: FdrFormValues): FdrInput {
  return {
    bank_id: v.bank_id,
    fdr_number: v.fdr_number.trim(),
    branch_name: orNull(v.branch_name),
    depositor_name: orNull(v.depositor_name),
    purpose: orNull(v.purpose),
    principal_amount: v.principal_amount.trim(),
    interest_rate_pct: orNull(v.interest_rate_pct),
    issue_date: v.issue_date,
    maturity_date: v.maturity_date,
  };
}

export const isDirty = (current: FdrFormValues, saved: FdrFormValues): boolean =>
  FORM_FIELDS.some((f) => current[f].trim() !== saved[f].trim());
