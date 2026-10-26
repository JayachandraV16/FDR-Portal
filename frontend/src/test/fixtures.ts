import type { AuthUser, FdrOut, SessionResponse } from "../lib/api";

// Shape of backend/app/schemas/auth.py: UserOut and SessionResponse.
export function makeUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: "6f1c1b8e-2d1e-4c52-9a3e-0d2f6c7a9b10",
    email: "officer@example.invalid",
    full_name: "Test Officer",
    role: "AICTE_REVIEWER",
    institution_id: null,
    must_change_password: false,
    ...overrides,
  };
}

export function makeSession(user: AuthUser = makeUser(), csrfToken = "csrf-token-for-tests"): SessionResponse {
  return { user, csrf_token: csrfToken };
}

export const TEMPORARY_USER = makeUser({
  full_name: "New Administrator",
  email: "admin@example.invalid",
  role: "ADMIN",
  must_change_password: true,
});

export const PASSWORD = "correct horse battery staple";

// Shape of backend/app/schemas/fdrs.py: FdrOut.
export function makeFdr(overrides: Partial<FdrOut> = {}): FdrOut {
  return {
    id: "a1b2c3d4-0000-4000-8000-000000000001",
    institution: { id: "11111111-2222-4333-8444-555555555555", code: "TST", name: "Test Institution" },
    bank: { id: "99999999-8888-4777-8666-555555555555", code: "TBK", name: "Test Bank" },
    fdr_number: "TST-FDR-0001",
    branch_name: null,
    depositor_name: null,
    purpose: null,
    currency: "INR",
    principal_amount: "1500000.00",
    interest_rate_pct: "7.10",
    issue_date: "2026-01-15",
    maturity_date: "2027-01-15",
    status: "SUBMITTED",
    row_version: 1,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    submitted_at: "2026-10-01T10:00:00Z",
    decided_at: null,
    ...overrides,
  };
}
