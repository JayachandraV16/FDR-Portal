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
    /** The backend's `detail` as sent: a string, or FastAPI's list of validation errors. */
    readonly detail?: unknown,
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
  /** A non-JSON request body (a file upload) and the headers that describe it. */
  raw?: { body: BodyInit; headers: Record<string, string> };
  /** What a successful response holds. Defaults to JSON. */
  response?: "json" | "empty" | "blob";
}

function errorFromStatus(status: number, detail: unknown): ApiError {
  // FastAPI sends `detail` as a string for HTTPException and as a list for 422 validation errors.
  const text = typeof detail === "string" ? detail : "";
  if (status === 401) return new ApiError("unauthorized", status, text || "Not authenticated", detail);
  if (status === 403) return new ApiError("forbidden", status, text || "Forbidden", detail);
  if (status === 422) return new ApiError("validation", status, text || "The request was not valid", detail);
  if (status >= 500) return new ApiError("server", status, text || `Server error (HTTP ${status})`, detail);
  return new ApiError("client", status, text || `Request failed (HTTP ${status})`, detail);
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: options.response === "blob" ? "*/*" : "application/json" };
  if (options.raw) Object.assign(headers, options.raw.headers);
  else if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let res: Response;
  try {
    res = await fetch(`${API_PREFIX}${path}`, {
      method,
      headers,
      body: options.raw ? options.raw.body : options.body === undefined ? undefined : JSON.stringify(options.body),
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
  if (options.response === "empty") return undefined as T;
  if (options.response === "blob") return (await res.blob()) as T;
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
  /** Server-side ordering; the default is newest first. */
  sort?: "created" | "number" | "principal" | "maturity" | "issue" | "status";
  order?: "asc" | "desc";
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

// --- FDR workflow (institution users) --------------------------------------------------------------
// Request bodies mirror backend/app/schemas/fdrs.py (FdrCreate, FdrUpdate, FdrSubmit). The server
// decides the institution, status and owner; none of them can be sent.

export interface FdrInput {
  bank_id: string;
  fdr_number: string;
  branch_name: string | null;
  depositor_name: string | null;
  purpose: string | null;
  principal_amount: string; // exact decimal string
  interest_rate_pct: string | null;
  issue_date: string; // YYYY-MM-DD
  maturity_date: string;
}

export function getFdr(id: string, signal?: AbortSignal): Promise<FdrOut> {
  return request("GET", `/fdrs/${id}`, { signal });
}

export function createFdr(input: FdrInput): Promise<FdrOut> {
  return request("POST", "/fdrs", { body: input });
}

export function updateFdr(id: string, rowVersion: number, input: FdrInput): Promise<FdrOut> {
  return request("PATCH", `/fdrs/${id}`, { body: { ...input, row_version: rowVersion } });
}

export function submitFdr(id: string, rowVersion: number): Promise<FdrOut> {
  return request("POST", `/fdrs/${id}/submit`, { body: { row_version: rowVersion } });
}

/** Field-level messages from a FastAPI 422 list, keyed by field name. Anything that cannot be tied
 * to a field is returned under "_form". */
export function fieldErrorsFrom(err: unknown, knownFields: readonly string[]): Record<string, string> {
  const result: Record<string, string> = {};
  if (!(err instanceof ApiError) || !Array.isArray(err.detail)) return result;
  for (const item of err.detail as Array<{ loc?: unknown[]; msg?: string }>) {
    const message = String(item.msg ?? "Invalid value").replace(/^Value error, /, "");
    const last = Array.isArray(item.loc) ? String(item.loc[item.loc.length - 1]) : "";
    const field = knownFields.includes(last) ? last : knownFields.find((f) => message.startsWith(f));
    const key = field ?? "_form";
    result[key] = result[key] ? `${result[key]}; ${message}` : message;
  }
  return result;
}

// --- Review workflow --------------------------------------------------------------------------------
// Mirrors backend/app/schemas/fdrs.py (HistoryEntryOut, RevisionInfo, FdrHistory, the review requests).

export interface HistoryEntry {
  from_status: string | null;
  to_status: string;
  actor_role: string; // the actor is identified by role only
  comment: string | null;
  occurred_at: string;
}

export interface RevisionInfo {
  revision_no: number;
  submitted_at: string;
}

export interface FdrHistory {
  history: HistoryEntry[]; // oldest first
  revisions: RevisionInfo[];
}

export function getHistory(id: string, signal?: AbortSignal): Promise<FdrHistory> {
  return request("GET", `/fdrs/${id}/history`, { signal });
}

export type DocumentCheckStatus = "NO_DOCUMENTS" | "NOT_ANALYZED" | "PENDING" | "FAILED" | "ANALYZED" | "UNAVAILABLE";

/** What stored extraction results say about an FDR's documents. A reading aid for prioritising review: it
 * never says a document is genuine or fraudulent, and "not analyzed" is neutral, not a discrepancy. */
export interface DocumentCheckSummary {
  status: DocumentCheckStatus;
  documents_total: number;
  documents_analyzed: number;
  documents_pending: number;
  documents_failed: number;
  mismatch_count: number; // actual MISMATCH outcomes only
  ambiguous_count: number;
  not_found_count: number;
  not_comparable_count: number;
  match_count: number;
  mismatch_fields: string[];
}

export interface ReviewQueueItem extends FdrOut {
  document_check: DocumentCheckSummary;
}

export interface ReviewQueueList {
  items: ReviewQueueItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

/** Submitted FDRs awaiting a decision, oldest first. AICTE reviewers and approvers only. */
export function listReviewQueue(
  params: { page?: number; page_size?: number; bank_id?: string; institution_id?: string; q?: string } = {},
  signal?: AbortSignal,
): Promise<ReviewQueueList> {
  return request("GET", withQuery("/review/queue", params), { signal });
}

export type ReviewAction = "request-correction" | "accept" | "reject";

export function decideFdr(id: string, action: ReviewAction, rowVersion: number, comment: string | null): Promise<FdrOut> {
  const body = comment === null ? { row_version: rowVersion } : { row_version: rowVersion, comment };
  return request("POST", `/fdrs/${id}/${action}`, { body });
}

// --- Documents ---------------------------------------------------------------------------------------
// Mirrors backend/app/schemas/documents.py. The storage key is never sent to the browser.

export type DocumentCategory = "FDR_CERTIFICATE" | "SECURITY_DEPOSIT_PROOF" | "SUPPORTING_LETTER" | "OTHER";

export interface DocumentOut {
  id: string;
  fdr_id: string;
  category: DocumentCategory;
  original_filename: string;
  content_type: string; // verified by the server from the file's contents
  size_bytes: number;
  sha256: string;
  uploaded_by_name: string;
  uploaded_at: string;
}

export interface DocumentList {
  items: DocumentOut[];
}

export function listDocuments(fdrId: string, signal?: AbortSignal): Promise<DocumentList> {
  return request("GET", `/fdrs/${fdrId}/documents`, { signal });
}

/** Sends the file as the raw request body. The name travels percent-encoded in a header (not in the
 * URL, so it stays out of access logs); the server re-validates everything and derives the type from
 * the file's bytes, not from anything sent here. */
export function uploadDocument(fdrId: string, file: File, category: DocumentCategory): Promise<DocumentOut> {
  return request("POST", withQuery(`/fdrs/${fdrId}/documents`, { category }), {
    raw: {
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
    },
  });
}

export function deleteDocument(fdrId: string, documentId: string): Promise<void> {
  return request("DELETE", `/fdrs/${fdrId}/documents/${documentId}`, { response: "empty" });
}

export function downloadDocument(fdrId: string, documentId: string): Promise<Blob> {
  return request("GET", `/fdrs/${fdrId}/documents/${documentId}/content`, { response: "blob" });
}

// --- Document extraction and comparison (reviewers and approvers) ---------------------------------------
// Mirrors backend/app/schemas/extractions.py. Extraction is a reading aid for a human reviewer: it is
// not verification, and nothing here changes an FDR.

export type FieldStatus = "EXTRACTED" | "AMBIGUOUS" | "NOT_FOUND";
export type Confidence = "HIGH" | "MEDIUM" | "LOW";
export type ComparisonOutcome = "MATCH" | "MISMATCH" | "NOT_FOUND" | "AMBIGUOUS" | "NOT_COMPARABLE";

export interface ExtractedFieldOut {
  name: string;
  label: string;
  status: FieldStatus;
  value: string | null; // exact decimal string for amounts and rates, ISO date for dates
  currency: string | null;
  confidence: Confidence | null;
  page: number | null;
  source: "EMBEDDED_TEXT" | "OCR" | null;
  candidates: Array<{ value: string; page: number }>;
  note: string | null;
  /** The value exactly as printed (for example in Devanagari digits), present only when it differs from `value`. */
  raw: string | null;
}

export interface ExtractionOut {
  id: string;
  document_id: string;
  status: "COMPLETED" | "FAILED";
  error_code: string | null;
  error_message: string | null;
  extractor: string;
  requested_at: string;
  pages_total: number;
  pages_processed: number;
  ocr_pages: number;
  truncated: boolean;
  warnings: string[];
  fields: ExtractedFieldOut[];
}

export interface ComparisonSide {
  submitted_value: string | null;
  outcome: ComparisonOutcome;
  explanation: string;
}

export interface FieldComparison {
  field: string;
  label: string;
  extracted: ExtractedFieldOut;
  current: ComparisonSide; // against the FDR as it is now
  revision: ComparisonSide | null; // against the frozen snapshot of the submission
}

export type JobStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

/** An extraction request moving through the server's background queue. */
export interface ExtractionJob {
  id: string;
  document_id: string;
  status: JobStatus;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
  error_code: string | null;
  error_message: string | null; // fixed, sanitized wording from the server
}

export interface DocumentComparison {
  document_id: string;
  extraction: ExtractionOut | null;
  revision_no: number | null;
  revision_submitted_at: string | null;
  fields: FieldComparison[];
  job: ExtractionJob | null; // the latest request; PENDING or RUNNING means a check is in progress
}

export function getComparison(fdrId: string, documentId: string, signal?: AbortSignal): Promise<DocumentComparison> {
  return request("GET", `/fdrs/${fdrId}/documents/${documentId}/comparison`, { signal });
}

/** Queue a check (202), or get the job already pending or running (200). Also used to retry a failed one. */
export function startExtraction(fdrId: string, documentId: string): Promise<ExtractionJob> {
  return request("POST", `/fdrs/${fdrId}/documents/${documentId}/extractions`, {});
}

// --- Alerts, exceptions, reports, release / refund, administration ---------------------------------------
// Mirrors backend/app/schemas/insights.py and users.py. Alerts, exceptions and reports are COMPUTED from
// current records on every request: nothing is stored or sent, and a section that cannot be computed
// reliably says so (`available: false`) instead of showing zero.

export type Severity = "HIGH" | "MEDIUM" | "LOW";

export interface AlertItem {
  category: string;
  severity: Severity;
  fdr_id: string;
  fdr_number: string;
  institution_code: string;
  institution_name: string;
  message: string;
  date: string | null;
  days: number | null;
  link: string;
}

export interface AlertCategory {
  key: string;
  label: string;
  total: number;
  exact: boolean; // false: `total` is a lower bound (only a bounded number of records was examined)
  shown: number;
  available: boolean;
  note: string | null;
}

export interface AlertsOut {
  generated_on: string;
  basis: string;
  categories: AlertCategory[];
  items: AlertItem[];
}

export interface ExceptionItem {
  kind: string;
  severity: Severity;
  fdr_id: string;
  fdr_number: string;
  institution_code: string;
  institution_name: string;
  status: string;
  issue: string;
  link: string;
}

export interface ExceptionKind {
  key: string;
  label: string;
  total: number;
  available: boolean;
  note: string | null;
}

export interface ExceptionsOut {
  generated_on: string;
  scanned: number;
  eligible_total: number;
  complete: boolean;
  kinds: ExceptionKind[];
  items: ExceptionItem[];
}

export const getAlerts = (signal?: AbortSignal): Promise<AlertsOut> => request("GET", "/alerts", { signal });
export const getExceptions = (signal?: AbortSignal): Promise<ExceptionsOut> => request("GET", "/exceptions", { signal });

export interface RegisterRow {
  key: string;
  label: string;
  count: number;
  principal: string | null;
}

export interface MetricStatus {
  available: boolean;
  note: string | null;
}

export interface ReportOut {
  generated_on: string;
  scope: string;
  fdr_total: number;
  by_status: RegisterRow[];
  by_institution: RegisterRow[];
  principal_live_total: string; // exact decimal string
  maturities: RegisterRow[];
  release: RegisterRow[];
  release_status: MetricStatus;
  verification: RegisterRow[];
  verification_status: MetricStatus;
  exceptions: RegisterRow[];
  exceptions_status: MetricStatus;
}

export const getReport = (signal?: AbortSignal): Promise<ReportOut> => request("GET", "/reports/summary", { signal });

/** A CSV export of everything the caller may see (not one page). */
export const downloadReportCsv = (kind: "fdrs" | "exceptions"): Promise<Blob> =>
  request("GET", `/reports/${kind}.csv`, { response: "blob" });

/** PDF reports. The server decides what the caller may see and prints the scope and filters on the document. */
export type PdfKind = "summary" | "institutions" | "maturity" | "pending-review" | "exceptions" | "audit";
export const downloadReportPdf = (kind: PdfKind, params: { days?: number; institution_id?: string } = {}): Promise<Blob> =>
  request("GET", withQuery(`/reports/pdf/${kind}`, params), { response: "blob" });

// --- Account security: your own sign-in sessions (server-side; revoking one stops it working at once) --------------
export interface SessionItem {
  handle: string; // an opaque handle; never the session token or id
  current: boolean;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  browser: string;
  system: string;
  network: string | null; // masked address
}
export interface SessionList { items: SessionItem[]; idle_timeout_minutes: number; absolute_timeout_minutes: number }
export interface SignInEvent { occurred_at: string; event: string; outcome: string; network: string | null }
export interface SignInActivity { items: SignInEvent[]; previous_sign_in_at: string | null }
export const getSessions = (signal?: AbortSignal): Promise<SessionList> => request("GET", "/account/sessions", { signal });
export const revokeSession = (handle: string): Promise<{ revoked: number }> => request("POST", `/account/sessions/${handle}/revoke`, {});
export const revokeOtherSessions = (): Promise<{ revoked: number }> => request("POST", "/account/sessions/revoke-others", {});
export const getSignInActivity = (signal?: AbortSignal): Promise<SignInActivity> => request("GET", "/account/activity", { signal });
export const adminRevokeSessions = (userId: string): Promise<{ revoked: number }> => request("POST", `/users/${userId}/sessions/revoke`, {});

export type ReleaseState = "NONE" | "REQUESTED" | "APPROVED" | "REJECTED";
export type RejectionReason = "INCOMPLETE_SUPPORTING_INFORMATION" | "NOT_YET_DUE" | "DETAILS_NEED_CORRECTION" | "OTHER";

export interface ReleaseEvent {
  action: "release.requested" | "release.approved" | "release.rejected";
  occurred_at: string;
  actor_role: string | null;
  actor_name: string | null;
  reason: string | null;
}

/** A release/refund REQUEST and its decision. It is an authorisation workflow: it does not move funds. */
export interface ReleaseOut {
  fdr_id: string;
  fdr_number: string;
  institution_code: string;
  institution_name: string;
  bank_name: string;
  principal_amount: string;
  maturity_date: string;
  fdr_status: string;
  matured: boolean;
  eligible: boolean;
  eligibility_note: string;
  state: ReleaseState;
  can_request: boolean; // for the signed-in user
  can_decide: boolean; // for the signed-in user
  history: ReleaseEvent[];
}

export interface ReleaseList {
  items: ReleaseOut[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  note: string;
}

export const listRelease = (
  params: { state?: ReleaseState; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<ReleaseList> => request("GET", withQuery("/release", params), { signal });
export const getRelease = (fdrId: string, signal?: AbortSignal): Promise<ReleaseOut> =>
  request("GET", `/fdrs/${fdrId}/release`, { signal });
export const requestRelease = (fdrId: string): Promise<ReleaseOut> => request("POST", `/fdrs/${fdrId}/release/request`, {});
export const approveRelease = (fdrId: string): Promise<ReleaseOut> => request("POST", `/fdrs/${fdrId}/release/approve`, {});
export const rejectRelease = (fdrId: string, reason: RejectionReason): Promise<ReleaseOut> =>
  request("POST", `/fdrs/${fdrId}/release/reject`, { body: { reason } });

// Administrator view of accounts (ADMIN only). Never includes password hashes or session data.
export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  institution_id: string | null;
  is_active: boolean;
  must_change_password: boolean;
  is_locked: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface AdminUserList {
  items: AdminUser[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserCreateInput {
  email: string;
  full_name: string;
  role: string;
  institution_id: string | null;
}

export interface UserUpdateInput {
  role?: string;
  institution_id?: string | null;
  is_active?: boolean;
  full_name?: string;
}

export const listAdminUsers = (params: { limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<AdminUserList> =>
  request("GET", withQuery("/users", params), { signal });
/** The temporary password is returned once, here, and nowhere else. */
export const createUser = (input: UserCreateInput): Promise<{ user: AdminUser; temporary_password: string }> =>
  request("POST", "/users", { body: input });
export const updateUser = (id: string, input: UserUpdateInput): Promise<AdminUser> =>
  request("PATCH", `/users/${id}`, { body: input });

// --- FDR Copilot (read-only assistant) and recent activity -----------------------------------------------
// Mirrors backend/app/api/chat.py. The assistant answers from built-in help and from records the signed-in
// user may already see; it cannot change anything. `source` says who worded the reply: "built-in" text, or
// an optional language model. The facts and links always come from the server.

export type ChatPage =
  | "overview" | "fdrs" | "fdr_new" | "fdr_edit" | "fdr_detail" | "documents" | "documents_detail" | "verification" | "verification_detail"
  | "review" | "review_detail" | "alerts" | "exceptions" | "release" | "release_detail" | "reports" | "calendar" | "workload"
  | "security" | "import" | "admin" | "default";
export type ChatLanguage = "en" | "hi" | "mr";

export interface ChatReply {
  reply: string;
  source: "built-in" | "llm";
  kind: "help" | "records" | "unknown";
  notice: string | null;
  language?: ChatLanguage; // the language the reply is actually in (English when no translation exists)
  provider?: string | null; // which AI service worded a source "llm" reply; absent or null for built-in answers
  facts: Array<{ label: string; value: string }>;
  links: Array<{ label: string; path: string }>;
  suggestions: string[];
}

export const sendChat = (
  message: string,
  context: { page?: ChatPage; fdr_id?: string; language?: ChatLanguage; intent?: "message" | "explain_page" } = {},
): Promise<ChatReply> => request("POST", "/chat", { body: { message, ...context } });

export const getChatSuggestions = (page: ChatPage, language: ChatLanguage = "en", signal?: AbortSignal): Promise<{ suggestions: string[]; capabilities: string }> =>
  request("GET", withQuery("/chat/suggestions", { page, language: language === "en" ? undefined : language }), { signal });

// --- Maturity calendar and reviewer workload (read-only, derived on the server) ---------------------------------
export interface CalendarEvent {
  fdr_id: string;
  fdr_number: string;
  institution_code: string;
  institution_name: string;
  maturity_date: string;
  status: string;
  days_until: number;
  marker: "OVERDUE" | "SOON" | "UPCOMING" | "NONE";
  link: string;
}
export interface CalendarOut {
  today: string;
  start: string;
  end: string;
  soon_days: number;
  total: number;
  truncated: boolean;
  events: CalendarEvent[];
  summary: { overdue: number; next_7: number; next_30: number; next_90: number };
  note: string;
}
export const getCalendar = (
  params: { start: string; end: string; institution_id?: string; status?: string },
  signal?: AbortSignal,
): Promise<CalendarOut> => request("GET", withQuery("/calendar", params), { signal });

export interface WorkloadOut {
  generated_at: string;
  period_days: number;
  window_start: string;
  pending: number;
  oldest_pending_at: string | null;
  oldest_pending_days: number | null;
  pending_missing_timestamp: number;
  waiting: Array<{ key: string; label: string; count: number }>;
  sla: { configured: boolean; threshold_days: number | null; beyond: number | null; approaching: number | null; note: string };
  completed_total: number;
  completed: Array<{ key: string; label: string; count: number }>;
  turnaround: { measured: number; excluded: number; average_hours: number | null; median_hours: number | null; note: string };
  by_role: Array<{ role: string; count: number }>;
  by_person: Array<{ name: string; role: string; count: number }> | null;
  mine: number | null;
  distribution: Array<{ key: string; label: string; count: number }>;
  assignment_tracked: boolean;
  assignment_note: string;
  queue_link: string;
}
export const getWorkload = (days: 7 | 30 | 90, signal?: AbortSignal): Promise<WorkloadOut> =>
  request("GET", withQuery("/review/workload", { days }), { signal });

// --- Bulk FDR import (CSV). The server validates everything; every imported row becomes a draft. ----------------
export interface ImportRowReport { line: number; fdr_number: string; bank_code: string; valid: boolean; errors: string[] }
export interface ImportPreview { digest: string; rows_total: number; valid_count: number; invalid_count: number; rows: ImportRowReport[]; max_rows: number; note: string }
export interface ImportRowOutcome { line: number; fdr_number: string; outcome: "created" | "rejected"; fdr_id: string | null; errors: string[] }
export interface ImportResult { created: number; rejected: number; rows: ImportRowOutcome[]; note: string }
const csvBody = (file: File, extra: Record<string, string> = {}) => ({ body: file as BodyInit, headers: { "Content-Type": "text/csv", ...extra } });
export const downloadImportTemplate = (): Promise<Blob> => request("GET", "/fdrs/import/template", { response: "blob" });
export const previewImport = (file: File): Promise<ImportPreview> => request("POST", "/fdrs/import/preview", { raw: csvBody(file) });
export const runImport = (file: File, digest: string): Promise<ImportResult> => request("POST", "/fdrs/import", { raw: csvBody(file, { "X-Import-Digest": digest }) });
export const downloadImportErrors = (file: File): Promise<Blob> => request("POST", "/fdrs/import/errors", { raw: csvBody(file), response: "blob" });

export type FeedbackReason = "incorrect" | "unclear" | "incomplete" | "not_relevant" | "other";
export interface FeedbackInput {
  reply_id: string; // generated by the browser for each answer, so a repeat submission is harmless
  rating: "helpful" | "not_helpful";
  reason?: FeedbackReason;
  kind: ChatReply["kind"];
  source: ChatReply["source"];
  provider?: "gemini" | "openai_compatible";
  language: ChatLanguage;
  page?: ChatPage;
}
/** Only facts about the answer and a fixed reason are sent: never the question, the answer or any text. */
export const sendFeedback = (input: FeedbackInput): Promise<{ recorded: boolean; duplicate: boolean; rating: "helpful" | "not_helpful" }> =>
  request("POST", "/chat/feedback", { body: input });

export interface ActivityItem {
  occurred_at: string;
  fdr_id: string;
  fdr_number: string;
  institution_name: string;
  from_status: string | null;
  to_status: string;
  actor_role: string;
}

/** Recent status changes for FDRs the signed-in user may see (not the audit log; no comments or names). */
export const getActivity = (limit = 10, signal?: AbortSignal): Promise<{ items: ActivityItem[] }> =>
  request("GET", withQuery("/activity", { limit }), { signal });
