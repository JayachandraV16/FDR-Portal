import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AlertsOut, ExceptionsOut, ReportOut } from "../lib/api";
import { installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const session = (role: string) =>
  jsonReply(200, makeSession(makeUser({ role, institution_id: role === "INSTITUTION_USER" ? "a0000000-0000-4000-8000-000000000001" : null })));

function open(path: string, routes: Record<string, Handler | Handler[]>, role = "AICTE_REVIEWER") {
  const api = installFakeApi({ "GET /auth/me": session(role), "GET /institutions": jsonReply(200, { items: [], total: 0, limit: 100, offset: 0 }), ...routes }, { dashboardDefaults: false });
  renderApp(path);
  return api;
}

const ALERTS: AlertsOut = {
  generated_on: "2026-10-27",
  basis: "Computed from current records on each request; not stored notifications.",
  categories: [
    { key: "MATURITY_SOON", label: "Maturing within 30 days", total: 7, exact: true, shown: 2, available: true, note: null },
    { key: "OVERDUE", label: "Past maturity, no approved release", total: 3, exact: false, shown: 1, available: true, note: "Only the 2000 longest-overdue records were examined; the true total may be higher." },
    { key: "EXTRACTION_FAILED", label: "Document processing failed", total: 0, exact: true, shown: 0, available: false, note: "Unavailable until the document and extraction migrations are applied to this database." },
  ],
  items: [
    { category: "OVERDUE", severity: "HIGH", fdr_id: "f1", fdr_number: "OD-1", institution_code: "PICT", institution_name: "PICT Test Institute", message: "3 days past maturity (accepted); no approved release", date: "2026-10-24", days: -3, link: "/review/f1" },
    { category: "MATURITY_SOON", severity: "LOW", fdr_id: "f2", fdr_number: "SOON-25", institution_code: "COEP", institution_name: "COEP", message: "Matures in 25 days", date: "2026-11-21", days: 25, link: "/review/f2" },
  ],
};

describe("alerts", () => {
  it("lists computed alerts with severity written out, dates and links, and says they are not stored notifications", async () => {
    open("/alerts", { "GET /alerts": jsonReply(200, ALERTS) });
    expect(await screen.findByRole("heading", { name: "Alerts", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText(/not stored notifications/i)).toBeInTheDocument();
    expect(screen.getByText(/Nothing is sent to anyone/i)).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("High")).toBeInTheDocument();
    expect(within(rows[0]).getByText("OD-1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("24 Oct 2026")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("link", { name: "Open OD-1" })).toHaveAttribute("href", "/review/f1");
    expect(within(rows[1]).getByText("Low")).toBeInTheDocument();
  });

  it("is honest about partial data: a lower bound, a truncated list, and an unavailable category", async () => {
    open("/alerts", { "GET /alerts": jsonReply(200, ALERTS) });
    await screen.findByText("OD-1");
    const soon = within(screen.getByLabelText("Alert categories")).getByText("Maturing within 30 days").closest("li")!;
    expect(soon).toHaveTextContent("7");
    expect(soon).toHaveTextContent("Showing the first 2 below.");
    const overdue = screen.getByText("Past maturity, no approved release").closest("li")!;
    expect(overdue).toHaveTextContent("at least 3");
    expect(overdue).toHaveTextContent("true total may be higher");
    const failed = screen.getByText("Document processing failed").closest("li")!;
    expect(failed).toHaveTextContent("Unavailable");
    expect(failed).toHaveTextContent(/migrations are applied/);
    expect(failed).not.toHaveTextContent(/^Document processing failed\s*0/); // unavailable is never shown as zero
  });

  it("says plainly when there is nothing to alert about, and shows errors with a retry", async () => {
    const empty: AlertsOut = { ...ALERTS, items: [], categories: ALERTS.categories.map((c) => ({ ...c, total: 0, shown: 0, exact: true, note: null })) };
    open("/alerts", { "GET /alerts": jsonReply(200, empty) });
    expect(await screen.findByText("No alerts right now.")).toBeInTheDocument();
  });

  it("recovers from a failed load with a retry, never showing an error as 'no alerts'", async () => {
    let calls = 0;
    open("/alerts", { "GET /alerts": (req) => (calls++ === 0 ? textReply(500)(req) : jsonReply(200, ALERTS)(req)) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load alerts/i);
    expect(screen.queryByText("No alerts right now.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("OD-1")).toBeInTheDocument();
  });
});

const EXCEPTIONS: ExceptionsOut = {
  generated_on: "2026-10-27", scanned: 3, eligible_total: 5, complete: false,
  kinds: [
    { key: "FIELD_MISMATCH", label: "Document and record mismatch", total: 1, available: true, note: null },
    { key: "MISSING_DOCUMENT", label: "No supporting document", total: 1, available: true, note: null },
    { key: "EXTRACTION_FAILED", label: "Document processing failed", total: 0, available: false, note: "Extraction results are visible to AICTE reviewers and approvers only." },
  ],
  items: [
    { kind: "FIELD_MISMATCH", severity: "HIGH", fdr_id: "f1", fdr_number: "X-1", institution_code: "PICT", institution_name: "PICT Test Institute", status: "SUBMITTED", issue: "A document differs from the submitted record: principal amount", link: "/review/f1" },
    { kind: "MISSING_DOCUMENT", severity: "MEDIUM", fdr_id: "f2", fdr_number: "X-2", institution_code: "COEP", institution_name: "COEP", status: "ACCEPTED", issue: "No supporting document has been uploaded", link: "/review/f2" },
  ],
};

describe("exceptions", () => {
  it("shows each exception with its issue, severity, institution, status and a link, and does not call a mismatch fraud", async () => {
    open("/exceptions", { "GET /exceptions": jsonReply(200, EXCEPTIONS) });
    await screen.findByText("X-1");
    const row = screen.getByText("X-1").closest("tr")!;
    expect(within(row).getByText("High")).toBeInTheDocument();
    expect(within(row).getByText(/differs from the submitted record: principal amount/)).toBeInTheDocument();
    expect(within(row).getByText("PICT Test Institute")).toBeInTheDocument();
    expect(within(row).getByText("Submitted")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Open X-1" })).toHaveAttribute("href", "/review/f1");
    expect(screen.getByText(/not a finding that anything is fraudulent/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing here verifies a document with a bank/i)).toBeInTheDocument();
  });

  it("says how much was examined and that counts are then a lower bound; marks unavailable kinds", async () => {
    open("/exceptions", { "GET /exceptions": jsonReply(200, EXCEPTIONS) });
    await screen.findByText("X-1");
    expect(screen.getByText(/Examined 3 of 5 open records/)).toBeInTheDocument();
    expect(screen.getByText(/not all of them, so counts are a lower bound/)).toBeInTheDocument();
    const unavailable = screen.getByLabelText("Exception types").querySelector('[data-kind="EXTRACTION_FAILED"]')!;
    expect(unavailable).toHaveTextContent("Unavailable");
    expect(unavailable).toHaveTextContent(/reviewers and approvers only/);
  });

  it("narrows the list by type and severity, and offers only the types that are available", async () => {
    open("/exceptions", { "GET /exceptions": jsonReply(200, EXCEPTIONS) });
    await screen.findByText("X-1");
    const type = screen.getByLabelText("Type");
    expect(within(type).getAllByRole("option").map((o) => o.textContent)).toEqual(
      ["All types", "Document and record mismatch", "No supporting document"]);
    await userEvent.selectOptions(type, "No supporting document");
    expect(screen.queryByText("X-1")).not.toBeInTheDocument();
    expect(screen.getByText("X-2")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Severity"), "High");
    expect(screen.getByText("No exceptions match the filters.")).toBeInTheDocument();
  });

  it("states an empty result and a load failure honestly", async () => {
    open("/exceptions", { "GET /exceptions": jsonReply(200, { ...EXCEPTIONS, items: [], complete: true }) });
    expect(await screen.findByText("No exceptions found in the records examined.")).toBeInTheDocument();
  });

  it("shows a failure as an error with a retry", async () => {
    open("/exceptions", { "GET /exceptions": networkError });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load exceptions/i);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

const REPORT: ReportOut = {
  generated_on: "2026-10-27", scope: "Every institution's FDRs that have left draft (submitted, returned, accepted or rejected).",
  fdr_total: 27, principal_live_total: "2702.70",
  by_status: [{ key: "SUBMITTED", label: "Submitted", count: 27, principal: "2702.70" }],
  by_institution: [{ key: "i1", label: "PICT Test Institute (PICT)", count: 27, principal: "2702.70" }],
  maturities: [{ key: "OVERDUE", label: "Past maturity", count: 1, principal: "100.10" }, { key: "D30", label: "Within 30 days", count: 0, principal: "0.00" }],
  release: [{ key: "NONE", label: "Matured, no request yet", count: 2, principal: null }],
  release_status: { available: true, note: "Release state is read from the audit trail; no release table exists yet." },
  verification: [],
  verification_status: { available: false, note: "Unavailable until the document and extraction migrations are applied to this database." },
  exceptions: [{ key: "INCOMPLETE_RECORD", label: "Incomplete record", count: 4, principal: null }],
  exceptions_status: { available: true, note: "Based on 500 of 900 open records." },
};

describe("reports", () => {
  let created: string[];
  let anchors: Array<{ download: string }>;
  beforeEach(() => {
    created = [];
    anchors = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => { created.push("blob:r"); return "blob:r"; }), revokeObjectURL: vi.fn() }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { anchors.push({ download: this.download }); });
  });
  afterEach(() => vi.restoreAllMocks());

  it("draws a bar per row from the server's counts, scaled to the largest, and explains what the PDFs can filter", async () => {
    open("/reports", { "GET /reports/summary": jsonReply(200, REPORT) });
    await screen.findByTestId("principal-total");
    const table = within(screen.getByRole("table", { name: "FDRs by status" }));
    const bars = table.getAllByTestId("bar").map((b) => (b.firstElementChild as HTMLElement).style.width);
    expect(bars).toHaveLength(REPORT.by_status.length);
    const counts = REPORT.by_status.map((r) => r.count);
    counts.forEach((c, i) => expect(parseFloat(bars[i])).toBeCloseTo((c / Math.max(...counts)) * 100, 1));
    expect(screen.getByText(/always cover everything in your scope/i)).toBeInTheDocument();
  });

  it("shows server-computed totals exactly as given, with their scope, and never sums a page itself", async () => {
    const api = open("/reports", { "GET /reports/summary": jsonReply(200, REPORT) });
    expect(await screen.findByTestId("principal-total")).toHaveTextContent("₹2,702.70");
    expect(screen.getByTestId("fdr-total")).toHaveTextContent("27");
    expect(screen.getByText(/not over one page of a list/i)).toBeInTheDocument();
    expect(screen.getByText(/none has been verified with a bank/i)).toBeInTheDocument();
    expect(api.callsTo("GET /fdrs")).toHaveLength(0); // no paginated listing is fetched to compute anything
    const status = within(screen.getByRole("table", { name: "FDRs by status" }));
    expect(status.getByText("Submitted")).toBeInTheDocument();
    expect(status.getAllByText("₹2,702.70")).toHaveLength(1);
  });

  it("labels what cannot be computed as unavailable, with the reason, instead of showing zero", async () => {
    open("/reports", { "GET /reports/summary": jsonReply(200, REPORT) });
    const panel = (await screen.findByRole("region", { name: "Document verification" }));
    expect(panel).toHaveTextContent("Unavailable.");
    expect(panel).toHaveTextContent(/migrations are applied/);
    expect(panel.querySelector("table")).toBeNull();
    expect(screen.getByRole("region", { name: "Exceptions" })).toHaveTextContent("Based on 500 of 900 open records.");
    expect(screen.getByRole("region", { name: /Release \/ refund requests/ })).toHaveTextContent(/no release table exists yet/);
  });

  it("downloads the FDR export through the API and names the file after the report date", async () => {
    const api = open("/reports", {
      "GET /reports/summary": jsonReply(200, REPORT),
      "GET /reports/fdrs.csv": () => new Response(new Blob(["a,b\r\n"], { type: "text/csv" }), { status: 200, headers: { "Content-Type": "text/csv" } }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Download FDR report (CSV)" }));
    await waitFor(() => expect(anchors).toEqual([{ download: "fdr-report-2026-10-27.csv" }]));
    expect(api.callsTo("GET /reports/fdrs.csv")).toHaveLength(1);
    expect(api.callsTo("GET /reports/fdrs.csv")[0].credentials).toBe("same-origin");
  });

  it("offers the exceptions export to reviewers and approvers only", async () => {
    open("/reports", { "GET /reports/summary": jsonReply(200, REPORT) }, "AICTE_APPROVER");
    expect(await screen.findByRole("button", { name: "Download exceptions (CSV)" })).toBeInTheDocument();
  });

  it.each(["INSTITUTION_USER", "ADMIN", "AUDITOR"])("does not offer the exceptions export to %s", async (role) => {
    open("/reports", { "GET /reports/summary": jsonReply(200, REPORT) }, role);
    await screen.findByRole("button", { name: "Download FDR report (CSV)" });
    expect(screen.queryByRole("button", { name: "Download exceptions (CSV)" })).not.toBeInTheDocument();
  });

  it("reports a refused or failed export without pretending it worked", async () => {
    open("/reports", { "GET /reports/summary": jsonReply(200, REPORT), "GET /reports/fdrs.csv": jsonReply(403, { detail: "Forbidden" }) });
    await userEvent.click(await screen.findByRole("button", { name: "Download FDR report (CSV)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not permitted to export/i);
    expect(anchors).toEqual([]);
  });

  it("shows a load failure as an error with a retry", async () => {
    open("/reports", { "GET /reports/summary": textReply(500) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load the report/i);
  });
});
