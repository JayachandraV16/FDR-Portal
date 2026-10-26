export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
}

// Mirrors backend/app/schemas/auth.py (UserOut, SessionResponse, MessageResponse).
export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  institution_id: string | null;
  must_change_password: boolean;
}

export interface SessionResponse {
  user: AuthUser;
  csrf_token: string;
}

export interface MessageResponse {
  detail: string;
}

export type ApiErrorKind = "network" | "unauthorized" | "forbidden" | "validation" | "client" | "server";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const API_PREFIX = "/api/v1";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

// The CSRF token is session-bound and handed to us in the login / me response body. It lives in
// memory only (never in localStorage/sessionStorage); the session itself is an HttpOnly cookie that
// JavaScript cannot read. The browser adds the Origin header to unsafe requests by itself.
let csrfToken: string | null = null;
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

// Called when an authenticated request is rejected with 401 (expired or revoked session).
let unauthorizedHandler: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

interface RequestOptions {
  body?: unknown;
  signal?: AbortSignal;
  // Auth endpoints interpret 401 themselves (bad credentials, "not signed in").
  handleUnauthorized?: boolean;
}

function errorFromStatus(status: number, detail: unknown): ApiError {
  // FastAPI sends `detail` as a string for HTTPException and as a list for 422 validation errors.
  const text = typeof detail === "string" ? detail : "";
  if (status === 401) return new ApiError("unauthorized", status, text || "Not authenticated");
  if (status === 403) return new ApiError("forbidden", status, text || "Forbidden");
  if (status === 422) return new ApiError("validation", status, text || "The request was not valid");
  if (status >= 500) return new ApiError("server", status, text || `Server error (HTTP ${status})`);
  return new ApiError("client", status, text || `Request failed (HTTP ${status})`);
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let res: Response;
  try {
    res = await fetch(`${API_PREFIX}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("network", 0, "Cannot reach the server");
  }

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail;
    } catch {
      detail = undefined; // empty or non-JSON body (for example the dev proxy's bare 500)
    }
    if (res.status === 401 && options.handleUnauthorized !== false) unauthorizedHandler?.();
    throw errorFromStatus(res.status, detail);
  }
  return (await res.json()) as T;
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("GET", "/health", { signal, handleUnauthorized: false }).catch((err) => {
    if (err instanceof ApiError && err.kind !== "network") {
      throw new Error(`Health check failed with HTTP ${err.status}`);
    }
    throw err;
  });
}

export function login(email: string, password: string, signal?: AbortSignal): Promise<SessionResponse> {
  return request("POST", "/auth/login", { body: { email, password }, signal, handleUnauthorized: false });
}

export function fetchSession(signal?: AbortSignal): Promise<SessionResponse> {
  return request("GET", "/auth/me", { signal, handleUnauthorized: false });
}

export function logout(): Promise<MessageResponse> {
  return request("POST", "/auth/logout", { handleUnauthorized: false });
}

export function changePassword(currentPassword: string, newPassword: string): Promise<MessageResponse> {
  return request("POST", "/auth/change-password", {
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

// --- Read-only data endpoints used by the dashboard ----------------------------------------------
// Types mirror backend/app/schemas/fdrs.py, institutions.py and users.py.

export type FdrStatus = "DRAFT" | "SUBMITTED" | "CORRECTION_REQUESTED" | "ACCEPTED" | "REJECTED" | "WITHDRAWN";

export interface BankOut {
  id: string;
  code: string;
  name: string;
}

export interface InstitutionSummary {
  id: string;
  code: string;
  name: string;
}

export interface FdrOut {
  id: string;
  institution: InstitutionSummary;
  bank: BankOut;
  fdr_number: string;
  branch_name: string | null;
  depositor_name: string | null;
  purpose: string | null;
  currency: "INR";
  principal_amount: string; // exact decimal string with two places, never a JSON float
  interest_rate_pct: string | null;
  issue_date: string;
  maturity_date: string;
  status: string; // FdrStatus today; kept open so an unknown future status still renders
  row_version: number;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
  decided_at: string | null;
}

export interface FdrList {
  items: FdrOut[];
  page: number;
  page_size: number;
  total: number; // matches visible to the caller, counted before pagination
  total_pages: number;
}

export interface BankList {
  items: BankOut[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface InstitutionOut {
  id: string;
  code: string;
  name: string;
  state: string;
  institution_type: string;
  contact_email: string | null;
  status: string;
  created_at: string;
}

export interface InstitutionList {
  items: InstitutionOut[];
  total: number;
  limit: number;
  offset: number;
}

// ADMIN only. Only the total is used by the UI; the rest is typed for completeness.
export interface UserList {
  items: Array<{ id: string; email: string; full_name: string; role: string; is_active: boolean }>;
  total: number;
  limit: number;
  offset: number;
}

export interface ReadyResponse {
  status: string;
  database: string;
}

function withQuery(path: string, params: object): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params) as Array<[string, string | number | undefined]>) {
    if (value !== undefined) query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

export interface FdrListParams {
  page?: number; // 1-based, at most 100000
  page_size?: number; // 1-100
  status?: FdrStatus;
  bank_id?: string;
  /** Honoured for cross-institution roles only; the backend ignores it for institution users. */
  institution_id?: string;
  /** FDR-number prefix. Case, spaces and tabs are ignored by the server; no other field is searched. */
  q?: string;
}

export function listFdrs(params: FdrListParams = {}, signal?: AbortSignal): Promise<FdrList> {
  return request("GET", withQuery("/fdrs", params), { signal });
}

export function listBanks(params: { page?: number; page_size?: number } = {}, signal?: AbortSignal): Promise<BankList> {
  return request("GET", withQuery("/banks", params), { signal });
}

export function listInstitutions(params: { limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<InstitutionList> {
  return request("GET", withQuery("/institutions", params), { signal });
}

export function listUsers(params: { limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<UserList> {
  return request("GET", withQuery("/users", params), { signal });
}

/** Readiness: answers 503 when the database cannot be queried. Distinct from fetchHealth (liveness). */
export function fetchReady(signal?: AbortSignal): Promise<ReadyResponse> {
  return request("GET", "/health/ready", { signal, handleUnauthorized: false });
}
