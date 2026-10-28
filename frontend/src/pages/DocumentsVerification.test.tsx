import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { NOT_VERIFICATION } from "./Verification";
import type { DocumentOut, FdrOut } from "../lib/api";
import { installFakeApi, jsonReply, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeSummary, makeUser } from "../test/fixtures";
import { makeDocument } from "../test/reviewBackend";
import { renderApp } from "../test/render";

const ID = "f0000000-0000-4000-8000-0000000000cc";
const INSTITUTION = { id: "a0000000-0000-4000-8000-000000000001", code: "PICT", name: "PICT Test Institute" };

const fdr = (overrides: Partial<FdrOut> = {}): FdrOut =>
  makeFdr({ id: ID, fdr_number: "DOC-1", status: "DRAFT", institution: INSTITUTION, bank: { id: "b1", code: "SBI", name: "State Bank of India" },
    principal_amount: "1500000.00", issue_date: "2026-01-15", maturity_date: "2027-01-15", ...overrides });

const page = (items: unknown[], total = items.length) => ({ items, page: 1, page_size: 25, total, total_pages: total ? Math.ceil(total / 25) : 0 });

function open(path: string, role: string, routes: Record<string, Handler | Handler[]> = {}) {
  const api = installFakeApi(
    {
      "GET /auth/me": jsonReply(200, makeSession(makeUser({ role, institution_id: role === "INSTITUTION_USER" ? INSTITUTION.id : null }))),
      ...routes,
    },
    { dashboardDefaults: false },
  );
  renderApp(path);
  return api;
}

const docs = (items: DocumentOut[]) => ({ [`GET /fdrs/${ID}/documents`]: jsonReply(200, { items }) });

describe("documents list", () => {
  it("lists FDRs with a link to each one's documents and searches by FDR number", async () => {
    const api = open("/documents", "INSTITUTION_USER", { "GET /fdrs": jsonReply(200, page([fdr()])) });
    const row = (await screen.findByText("DOC-1")).closest("tr")!;
    expect(within(row).getByRole("link", { name: "Documents for DOC-1" })).toHaveAttribute("href", `/documents/${ID}`);
    expect(within(row).getByText("Draft")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("FDR number"), "doc-");
    await screen.findByText("DOC-1");
    await new Promise((r) => setTimeout(r, 400));
    expect(api.callsTo("GET /fdrs").at(-1)!.query).toMatchObject({ q: "doc-", page: "1" });
  });

  it("states an empty result and shows a failed load as an error", async () => {
    open("/documents", "AICTE_REVIEWER", { "GET /fdrs": jsonReply(200, page([])) });
    expect(await screen.findByText("There are no FDRs to show.")).toBeInTheDocument();
  });

  it("shows a load failure as an error with a retry", async () => {
    open("/documents", "AICTE_APPROVER", { "GET /fdrs": textReply(500) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load fdrs/i);
  });

  it.each(["ADMIN", "AUDITOR"])("%s is told the page is not for their role and nothing is requested", async (role) => {
    const api = open(`/documents`, role, { "GET /fdrs": jsonReply(200, page([fdr()])) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(api.callsTo("GET /fdrs")).toHaveLength(0);
    renderApp(`/documents/${ID}`);
  });
});

describe("documents of one FDR", () => {
  it("lets an institution upload and remove while the FDR is a draft, and says analysis is for reviewers", async () => {
    open(`/documents/${ID}`, "INSTITUTION_USER", { [`GET /fdrs/${ID}`]: jsonReply(200, fdr()), ...docs([makeDocument({ fdr_id: ID })]) });
    expect(await screen.findByText("advice.pdf")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Upload a document" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove advice.pdf" })).toBeInTheDocument();
    expect(screen.getByText(/analysis results are visible to AICTE reviewers only/i)).toBeInTheDocument();
    expect(screen.getByText(/does not verify the FDR/i)).toBeInTheDocument();
    expect(screen.queryByText(/document check/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the FDR record" })).toHaveAttribute("href", `/fdrs/${ID}/edit`);
  });

  it("makes the documents read-only once the FDR is submitted, with the reason", async () => {
    open(`/documents/${ID}`, "INSTITUTION_USER", { [`GET /fdrs/${ID}`]: jsonReply(200, fdr({ status: "SUBMITTED" })), ...docs([makeDocument({ fdr_id: ID })]) });
    expect(await screen.findByText("advice.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload a document" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(screen.getByText(/only be added or removed while the FDR is a draft or has been returned for correction/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download advice.pdf" })).toBeInTheDocument();
  });

  it("gives a reviewer the documents read-only with the document check and links to review and verification", async () => {
    open(`/documents/${ID}`, "AICTE_REVIEWER", { [`GET /fdrs/${ID}`]: jsonReply(200, fdr({ status: "SUBMITTED" })), ...docs([makeDocument({ fdr_id: ID })]) });
    expect(await screen.findByText("advice.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload a document" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show document check for advice.pdf" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open for review" })).toHaveAttribute("href", `/review/${ID}`);
    expect(screen.getByRole("link", { name: "Open verification" })).toHaveAttribute("href", `/verification/${ID}`);
  });

  it("shows an unreadable document list as an error, not as 'no documents'", async () => {
    open(`/documents/${ID}`, "INSTITUTION_USER", { [`GET /fdrs/${ID}`]: jsonReply(200, fdr()), [`GET /fdrs/${ID}/documents`]: textReply(500) });
    expect(await screen.findByText(/could not load the documents/i)).toBeInTheDocument();
    expect(screen.queryByText("No documents have been uploaded yet.")).not.toBeInTheDocument();
  });

  it.each(["ADMIN", "AUDITOR"])("refuses %s on the detail page without requesting documents", async (role) => {
    const api = open(`/documents/${ID}`, role, { [`GET /fdrs/${ID}`]: jsonReply(200, fdr()), ...docs([]) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(api.callsTo(`GET /fdrs/${ID}/documents`)).toHaveLength(0);
  });
});

const queueItem = (n: number, summary = makeSummary()) => ({ ...fdr({ id: `f0000000-0000-4000-8000-0000000001${n}`, fdr_number: `Q-${n}`, status: "SUBMITTED" }), document_check: summary });

describe("verification list", () => {
  it("shows each submitted FDR's document-check state in neutral words and says this is not verification", async () => {
    open("/verification", "AICTE_REVIEWER", {
      "GET /review/queue": jsonReply(200, page([
        queueItem(1, makeSummary({ status: "ANALYZED", documents_analyzed: 1, mismatch_count: 2, mismatch_fields: ["fdr_number"] })),
        queueItem(2),
        queueItem(3, makeSummary({ status: "PENDING", documents_pending: 1 })),
      ])),
    });
    await screen.findByText("Q-1");
    expect(screen.getByText(NOT_VERIFICATION)).toBeInTheDocument();
    expect(NOT_VERIFICATION).toMatch(/not independent verification/i);
    expect(NOT_VERIFICATION).toMatch(/does not show that a document is genuine or that the bank issued it/i);
    const cell = (n: number) => screen.getByText(`Q-${n}`).closest("tr")!;
    expect(within(cell(1)).getByText(/2 mismatches/)).toBeInTheDocument();
    expect(within(cell(2)).getByText("Not analyzed")).toBeInTheDocument();
    expect(within(cell(3)).getByText("Check in progress")).toBeInTheDocument();
    expect(within(cell(1)).getByRole("link", { name: "Open verification for Q-1" })).toHaveAttribute("href", expect.stringMatching(/^\/verification\/f0000000/));
  });

  it("says when the list is only the oldest part of a longer queue, and points to the full queue", async () => {
    open("/verification", "AICTE_APPROVER", { "GET /review/queue": jsonReply(200, { ...page([queueItem(1)]), total: 60 }) });
    expect(await screen.findByText(/Showing the oldest 1 of 60/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "review queue" })).toHaveAttribute("href", "/review");
  });

  it("finds an FDR that is no longer in the queue by number", async () => {
    const api = open("/verification", "AICTE_REVIEWER", {
      "GET /review/queue": jsonReply(200, page([])),
      "GET /fdrs": jsonReply(200, page([fdr({ fdr_number: "OLD-1", status: "ACCEPTED" })])),
    });
    expect(await screen.findByText("No FDRs are awaiting review.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("FDR number"), "OLD");
    expect(await screen.findByText("OLD-1")).toBeInTheDocument();
    expect(api.callsTo("GET /fdrs").at(-1)!.query).toMatchObject({ q: "OLD", page_size: "10" });
    expect(screen.getByRole("link", { name: "Open verification for OLD-1" })).toHaveAttribute("href", `/verification/${ID}`);
  });

  it("starts no extraction by looking", async () => {
    const api = open("/verification", "AICTE_REVIEWER", { "GET /review/queue": jsonReply(200, page([queueItem(1)])) });
    await screen.findByText("Q-1");
    expect(api.calls.filter((c) => c.method !== "GET")).toEqual([]);
  });

  it.each(["INSTITUTION_USER", "ADMIN", "AUDITOR"])("is not available to %s and requests nothing", async (role) => {
    const api = open("/verification", role, { "GET /review/queue": jsonReply(200, page([])) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(api.callsTo("GET /review/queue")).toHaveLength(0);
  });
});

describe("verification of one FDR", () => {
  it("shows the submitted values, the documents with their checks, and keeps decisions on the review page", async () => {
    const api = open(`/verification/${ID}`, "AICTE_REVIEWER", { [`GET /fdrs/${ID}`]: jsonReply(200, fdr({ status: "SUBMITTED" })), ...docs([makeDocument({ fdr_id: ID }), makeDocument({ id: "d2", fdr_id: ID, original_filename: "scan.png", content_type: "image/png" })]) });
    expect(await screen.findByText("DOC-1")).toBeInTheDocument();
    const values = screen.getByText("The submitted values the documents are compared with.").closest("section")!;
    expect(within(values).getByText("State Bank of India")).toBeInTheDocument();
    expect(within(values).getByText("₹15,00,000.00")).toBeInTheDocument();
    expect(within(values).getByText("15 Jan 2026")).toBeInTheDocument();
    expect(screen.getByText(NOT_VERIFICATION)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Show document check for advice.pdf" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show document check for scan.png" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload a document" })).not.toBeInTheDocument();
    expect(screen.getByText(/never approves, rejects or returns an FDR/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "review page" })).toHaveAttribute("href", `/review/${ID}`);
    expect(api.callsTo(`GET /fdrs/${ID}/documents/d2/comparison`)).toHaveLength(0); // nothing is analysed until asked
  });

  it("shows an FDR it cannot load as an error", async () => {
    open(`/verification/${ID}`, "AICTE_REVIEWER", { [`GET /fdrs/${ID}`]: jsonReply(404, { detail: "FDR not found" }) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load this fdr/i);
  });

  it.each(["INSTITUTION_USER", "ADMIN", "AUDITOR"])("refuses %s on the detail page and requests nothing", async (role) => {
    const api = open(`/verification/${ID}`, role, { [`GET /fdrs/${ID}`]: jsonReply(200, fdr()) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(api.callsTo(`GET /fdrs/${ID}`)).toHaveLength(0);
  });
});
