import type { DocumentCategory, DocumentOut, FdrOut, HistoryEntry } from "../lib/api";
import { installFakeApi, jsonReply, type Handler } from "./fakeApi";
import { makeFdr, makeSession, makeSummary, makeUser } from "./fixtures";

export const FDR_ID = "f0000000-0000-4000-8000-0000000000bb";
export const INSTITUTION = { id: "a0000000-0000-4000-8000-000000000001", code: "PICT", name: "PICT Test Institute" };
export const BANKS = [
  { id: "b0000000-0000-4000-8000-000000000001", code: "BOM", name: "Bank of Maharashtra" },
  { id: "b0000000-0000-4000-8000-000000000002", code: "SBI", name: "State Bank of India" },
];
export const COMMENT = "Please re-enter the maturity date from the bank's advice.";

export const submittedFdr = (overrides: Partial<FdrOut> = {}): FdrOut =>
  makeFdr({
    id: FDR_ID, status: "SUBMITTED", row_version: 2, institution: INSTITUTION, bank: BANKS[0], fdr_number: "RV-FDR-1",
    branch_name: "Main Branch", depositor_name: "Test Depositor", purpose: "Security deposit",
    submitted_at: "2026-10-26T09:00:00Z", decided_at: null, ...overrides,
  });

export const entry = (to: string, from: string | null, role: string, comment: string | null = null, at = "2026-10-26T09:00:00Z"): HistoryEntry => ({
  from_status: from, to_status: to, actor_role: role, comment, occurred_at: at,
});

export const submittedHistory = (): HistoryEntry[] => [
  entry("DRAFT", null, "INSTITUTION_USER", null, "2026-10-25T09:00:00Z"),
  entry("SUBMITTED", "DRAFT", "INSTITUTION_USER", null, "2026-10-26T09:00:00Z"),
];

export const makeDocument = (overrides: Partial<DocumentOut> = {}): DocumentOut => ({
  id: "d0000000-0000-4000-8000-000000000001", fdr_id: FDR_ID, category: "FDR_CERTIFICATE", original_filename: "advice.pdf",
  content_type: "application/pdf", size_bytes: 2048, sha256: "0".repeat(64), uploaded_by_name: "Meera Kulkarni",
  uploaded_at: "2026-10-26T08:30:00Z", ...overrides,
});

const ALLOWED: Record<string, string[]> = {
  "request-correction": ["AICTE_REVIEWER", "AICTE_APPROVER"],
  accept: ["AICTE_APPROVER"],
  reject: ["AICTE_APPROVER"],
};
const TARGET: Record<string, string> = { "request-correction": "CORRECTION_REQUESTED", accept: "ACCEPTED", reject: "REJECTED" };
const REASON_REQUIRED = new Set(["request-correction", "reject"]);

/** A stateful stand-in for the review endpoints that follows the backend's rules. `store.role` can be
 * switched between renders to act as a different user against the same record. */
export function reviewBackend(
  initial: FdrOut | null = submittedFdr(),
  options: { role?: string; history?: HistoryEntry[]; documents?: DocumentOut[]; extra?: Record<string, Handler | Handler[]> } = {},
) {
  const store = {
    fdr: initial,
    history: options.history ?? (initial ? submittedHistory() : []),
    role: options.role ?? "AICTE_APPROVER",
    documents: options.documents ?? ([] as DocumentOut[]),
    nextDocument: 100,
  };
  const documentsReadable = () => ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER"].includes(store.role);
  const editable = () => !!store.fdr && ["DRAFT", "CORRECTION_REQUESTED"].includes(store.fdr.status);
  const session: Handler = (req) =>
    jsonReply(200, makeSession(makeUser({
      role: store.role, institution_id: store.role === "INSTITUTION_USER" ? INSTITUTION.id : null, full_name: "Meera Kulkarni",
    })))(req);
  const list = (items: object[]) => ({ items, page: 1, page_size: 25, total: items.length, total_pages: items.length ? 1 : 0 });
  const stale = jsonReply(409, { detail: "This FDR was changed since you loaded it. Reload it and try again." });

  const decision = (action: string): Handler => (req) => {
    const body = req.body as { row_version: number; comment?: string };
    if (!ALLOWED[action].includes(store.role)) return jsonReply(403, { detail: "Forbidden" })(req);
    if (REASON_REQUIRED.has(action) && !body.comment?.trim()) {
      return jsonReply(422, { detail: [{ loc: ["body", "comment"], msg: "String should have at least 1 character" }] })(req);
    }
    if (!store.fdr || store.fdr.status !== "SUBMITTED") return jsonReply(409, { detail: "This FDR is not awaiting review" })(req);
    if (body.row_version !== store.fdr.row_version) return stale(req);
    store.history.push(entry(TARGET[action], "SUBMITTED", store.role, body.comment ?? null, "2026-10-27T10:30:00Z"));
    store.fdr = {
      ...store.fdr, status: TARGET[action], row_version: store.fdr.row_version + 1,
      decided_at: action === "request-correction" ? null : "2026-10-27T10:30:00Z",
    };
    return jsonReply(200, store.fdr)(req);
  };

  const api = installFakeApi(
    {
      "GET /auth/me": session,
      "GET /banks": jsonReply(200, { items: BANKS, page: 1, page_size: 500, total: 2, total_pages: 1 }),
      "GET /institutions": jsonReply(200, { items: [INSTITUTION], total: 1, limit: 200, offset: 0 }),
      "GET /review/queue": (req) =>
        jsonReply(200, list(store.fdr && store.fdr.status === "SUBMITTED" ? [{ ...store.fdr, document_check: makeSummary({ documents_total: store.documents.length }) }] : []))(req),
      "GET /fdrs": (req) => jsonReply(200, list(store.fdr ? [store.fdr] : []))(req),
      [`GET /fdrs/${FDR_ID}`]: (req) =>
        store.fdr ? jsonReply(200, store.fdr)(req) : jsonReply(404, { detail: "FDR not found" })(req),
      [`GET /fdrs/${FDR_ID}/history`]: (req) =>
        jsonReply(200, { history: store.history, revisions: store.history.filter((h) => h.to_status === "SUBMITTED").map((h, i) => ({ revision_no: i + 1, submitted_at: h.occurred_at })) })(req),
      [`POST /fdrs/${FDR_ID}/request-correction`]: decision("request-correction"),
      [`POST /fdrs/${FDR_ID}/accept`]: decision("accept"),
      [`POST /fdrs/${FDR_ID}/reject`]: decision("reject"),
      [`PATCH /fdrs/${FDR_ID}`]: (req) => {
        const { row_version, ...fields } = req.body as { row_version: number } & Record<string, string | null>;
        if (!store.fdr || !["DRAFT", "CORRECTION_REQUESTED"].includes(store.fdr.status)) return jsonReply(409, { detail: "Only draft FDRs, or FDRs returned for correction, can be edited" })(req);
        if (row_version !== store.fdr.row_version) return stale(req);
        store.fdr = { ...store.fdr, ...(fields as Partial<FdrOut>), row_version: store.fdr.row_version + 1 };
        return jsonReply(200, store.fdr)(req);
      },
      [`POST /fdrs/${FDR_ID}/submit`]: (req) => {
        const { row_version } = req.body as { row_version: number };
        if (!store.fdr || !["DRAFT", "CORRECTION_REQUESTED"].includes(store.fdr.status)) return jsonReply(409, { detail: "Only draft FDRs, or FDRs returned for correction, can be submitted" })(req);
        if (row_version !== store.fdr.row_version) return stale(req);
        store.history.push(entry("SUBMITTED", store.fdr.status, "INSTITUTION_USER", null, "2026-10-28T08:00:00Z"));
        store.fdr = { ...store.fdr, status: "SUBMITTED", row_version: row_version + 1, submitted_at: "2026-10-28T08:00:00Z" };
        return jsonReply(200, store.fdr)(req);
      },
      [`GET /fdrs/${FDR_ID}/documents`]: (req) =>
        documentsReadable() ? jsonReply(200, { items: store.documents })(req) : jsonReply(403, { detail: "Forbidden" })(req),
      [`POST /fdrs/${FDR_ID}/documents`]: (req) => {
        if (store.role !== "INSTITUTION_USER") return jsonReply(403, { detail: "Forbidden" })(req);
        if (!editable()) return jsonReply(409, { detail: "Documents can only be changed while the FDR is a draft or has been returned for correction" })(req);
        const file = req.rawBody as File;
        const name = decodeURIComponent(req.headers["x-filename"] ?? "");
        const type = /\.pdf$/i.test(name) ? "application/pdf" : /\.jpe?g$/i.test(name) ? "image/jpeg" : /\.png$/i.test(name) ? "image/png" : null;
        if (!type) return jsonReply(415, { detail: "Only PDF, JPEG and PNG files are accepted" })(req);
        const doc = makeDocument({
          id: `d0000000-0000-4000-8000-${String(store.nextDocument++).padStart(12, "0")}`, original_filename: name,
          category: req.query.category as DocumentCategory, content_type: type, size_bytes: file.size,
        });
        store.documents.push(doc);
        return jsonReply(201, doc)(req);
      },
      "DELETE /fdrs/:id/documents/:doc": () => new Response(null, { status: 204 }),
      ...options.extra,
    },
    { dashboardDefaults: false },
  );
  return { api, store };
}
