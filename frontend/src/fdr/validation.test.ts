import { describe, expect, it } from "vitest";

import { ApiError, fieldErrorsFrom } from "../lib/api";
import { makeFdr } from "../test/fixtures";
import { EMPTY_VALUES, FORM_FIELDS, isDirty, toInput, validateFdrForm, valuesFromFdr, type FdrFormValues } from "./validation";

const valid: FdrFormValues = {
  bank_id: "b0000000-0000-4000-8000-000000000001",
  fdr_number: "FDR-1",
  branch_name: "Main",
  depositor_name: "Depositor",
  purpose: "",
  principal_amount: "1500000.00",
  interest_rate_pct: "7.10",
  issue_date: "2026-01-15",
  maturity_date: "2027-01-15",
};

describe("validateFdrForm", () => {
  it("accepts a complete, valid form (optional fields may be blank)", () => {
    expect(validateFdrForm(valid)).toEqual({});
    expect(validateFdrForm({ ...valid, interest_rate_pct: "", branch_name: "", depositor_name: "" })).toEqual({});
  });

  it("flags every missing required field on an empty form", () => {
    expect(Object.keys(validateFdrForm(EMPTY_VALUES)).sort()).toEqual(
      ["bank_id", "fdr_number", "issue_date", "maturity_date", "principal_amount"],
    );
  });

  it.each([
    ["0", true], ["0.00", true], ["-5", true], ["1.234", true], ["abc", true], ["1e6", true], ["1,000", true],
    ["1000000000000", true], // 13 integer digits
    ["0.01", false], ["999999999999.99", false], ["1500000", false], ["7.5", false],
  ])("principal %s is invalid: %s", (amount, invalid) => {
    expect(Boolean(validateFdrForm({ ...valid, principal_amount: amount }).principal_amount)).toBe(invalid);
  });

  it.each([["100", false], ["100.00", false], ["0", false], ["100.01", true], ["-1", true], ["7.123", true], ["x", true]])(
    "interest rate %s is invalid: %s",
    (rate, invalid) => {
      expect(Boolean(validateFdrForm({ ...valid, interest_rate_pct: rate }).interest_rate_pct)).toBe(invalid);
    },
  );

  it("requires the maturity date to be after the issue date", () => {
    expect(validateFdrForm({ ...valid, maturity_date: "2026-01-15" }).maturity_date).toMatch(/after the issue date/);
    expect(validateFdrForm({ ...valid, maturity_date: "2025-01-01" }).maturity_date).toMatch(/after the issue date/);
    expect(validateFdrForm({ ...valid, maturity_date: "2026-01-16" })).toEqual({});
  });

  it("enforces the backend's length limits", () => {
    expect(validateFdrForm({ ...valid, fdr_number: "x".repeat(65) }).fdr_number).toBeDefined();
    expect(validateFdrForm({ ...valid, branch_name: "x".repeat(201) }).branch_name).toBeDefined();
    expect(validateFdrForm({ ...valid, depositor_name: "x".repeat(301) }).depositor_name).toBeDefined();
    expect(validateFdrForm({ ...valid, purpose: "x".repeat(201) }).purpose).toBeDefined();
    expect(validateFdrForm({ ...valid, fdr_number: "   " }).fdr_number).toBeDefined();
  });
});

describe("toInput", () => {
  it("trims, turns blank optional fields into null, and keeps money as an exact string", () => {
    expect(toInput({ ...valid, fdr_number: "  FDR-1 ", purpose: "   ", interest_rate_pct: "", principal_amount: " 12.50 " })).toEqual({
      bank_id: valid.bank_id, fdr_number: "FDR-1", branch_name: "Main", depositor_name: "Depositor", purpose: null,
      principal_amount: "12.50", interest_rate_pct: null, issue_date: "2026-01-15", maturity_date: "2027-01-15",
    });
  });

  it("never includes fields the server decides", () => {
    const keys = Object.keys(toInput(valid));
    for (const forbidden of ["institution_id", "status", "created_by", "row_version", "id"]) expect(keys).not.toContain(forbidden);
    expect(keys.sort()).toEqual([...FORM_FIELDS].sort());
  });
});

describe("form state helpers", () => {
  it("builds form values from the server copy, with nulls shown as empty", () => {
    const values = valuesFromFdr(makeFdr({ purpose: null, interest_rate_pct: null, branch_name: "B" }));
    expect(values).toMatchObject({ purpose: "", interest_rate_pct: "", branch_name: "B", principal_amount: "1500000.00" });
  });

  it("detects unsaved changes but ignores surrounding whitespace", () => {
    expect(isDirty(valid, valid)).toBe(false);
    expect(isDirty({ ...valid, purpose: "  " }, valid)).toBe(false);
    expect(isDirty({ ...valid, purpose: "x" }, valid)).toBe(true);
  });
});

describe("fieldErrorsFrom", () => {
  const err = (detail: unknown) => new ApiError("validation", 422, "The request was not valid", detail);

  it("maps FastAPI validation items to their fields", () => {
    const result = fieldErrorsFrom(
      err([{ loc: ["body", "principal_amount"], msg: "Input should be greater than 0" }, { loc: ["body", "fdr_number"], msg: "Field required" }]),
      FORM_FIELDS,
    );
    expect(result).toEqual({ principal_amount: "Input should be greater than 0", fdr_number: "Field required" });
  });

  it("attaches a model-level error to the field it names, and keeps the rest as a general message", () => {
    const result = fieldErrorsFrom(
      err([{ loc: ["body"], msg: "Value error, maturity_date must be after issue_date" }, { loc: ["body", "surprise"], msg: "Extra inputs are not permitted" }]),
      FORM_FIELDS,
    );
    expect(result.maturity_date).toBe("maturity_date must be after issue_date");
    expect(result._form).toBe("Extra inputs are not permitted");
  });

  it("returns nothing for string details and non-API errors", () => {
    expect(fieldErrorsFrom(err("Unknown bank"), FORM_FIELDS)).toEqual({});
    expect(fieldErrorsFrom(new Error("x"), FORM_FIELDS)).toEqual({});
  });
});
