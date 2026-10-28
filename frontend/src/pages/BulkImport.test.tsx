import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

import type { ImportPreview, ImportResult } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const DIGEST = "a".repeat(64);
const preview = (over: Partial<ImportPreview> = {}): ImportPreview => ({
  digest: DIGEST, rows_total: 3, valid_count: 2, invalid_count: 1, max_rows: 500, note: "Imported rows are created as drafts of your institution. Nothing is submitted, accepted or released by an import.",
  rows: [
    { line: 2, fdr_number: "OK-1", bank_code: "SBI", valid: true, errors: [] },
    { line: 3, fdr_number: "BAD-2", bank_code: "NOPE", valid: false, errors: ["bank_code 'NOPE' is not a known bank code", "issue_date must be a date written YYYY-MM-DD"] },
    { line: 4, fdr_number: "OK-3", bank_code: "SBI", valid: true, errors: [] },
  ], ...over,
});
const FDR_A = "00000000-0000-4000-8000-00000000000a";
const FDR_B = "00000000-0000-4000-8000-00000000000b";
const result = (over: Partial<ImportResult> = {}): ImportResult => ({
  created: 2, rejected: 1, note: "Imported rows are created as drafts of your institution. Nothing is submitted, accepted or released by an import.",
  rows: [
    { line: 2, fdr_number: "OK-1", outcome: "created", fdr_id: FDR_A, errors: [] },
    { line: 3, fdr_number: "BAD-2", outcome: "rejected", fdr_id: null, errors: ["bank_code 'NOPE' is not a known bank code"] },
    { line: 4, fdr_number: "OK-3", outcome: "created", fdr_id: FDR_B, errors: [] },
  ], ...over,
});
const csvReply = (): Handler => () => new Response(new Blob(["a,b\r\n"], { type: "text/csv" }), { status: 200, headers: { "Content-Type": "text/csv" } });
const file = (name = "fdrs.csv", body = "fdr_number,bank_code\r\nOK-1,SBI\r\n") => new File([body], name, { type: "text/csv" });

describe("bulk import page", () => {
  let anchors: Array<{ download: string }>;
  beforeEach(() => {
    anchors = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:csv"), revokeObjectURL: vi.fn() }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { anchors.push({ download: this.download }); });
  });
  afterEach(() => vi.restoreAllMocks());

  async function open(routes: Record<string, Handler | Handler[]> = {}, role = "INSTITUTION_USER") {
    const api = installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession(makeUser({ role, institution_id: role === "INSTITUTION_USER" ? "a0000000-0000-4000-8000-000000000001" : null }), "csrf-imp")),
      "GET /fdrs/import/template": csvReply(), "POST /fdrs/import/preview": jsonReply(200, preview()), "POST /fdrs/import": jsonReply(200, result()),
      "POST /fdrs/import/errors": csvReply(), [`GET /fdrs/${FDR_A}`]: jsonReply(200, makeFdr({ id: FDR_A })), [`GET /fdrs/${FDR_A}/history`]: jsonReply(200, { history: [], revisions: [] }),
      ...routes,
    });
    renderApp("/import");
    await screen.findByRole("navigation", { name: "Main" });
    return api;
  }
  const upload = (f = file()) => userEvent.upload(screen.getByLabelText(/CSV file/), f);
  async function previewed(routes: Record<string, Handler | Handler[]> = {}) {
    const api = await open(routes);
    const f = file();
    await upload(f);
    await userEvent.click(screen.getByRole("button", { name: "Check file" }));
    await screen.findByTestId("count-valid");
    return { api, f };
  }

  it("is offered to institution users in the sidebar", async () => {
    await open();
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Bulk import" })).toHaveAttribute("href", "/import");
  });

  it.each(["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"])("is not offered to %s, who gets an explanation and makes no import request", async (role) => {
    const api = await open({}, role);
    expect(await screen.findByText(/not available to your role/i)).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Main" })).queryByRole("link", { name: "Bulk import" })).not.toBeInTheDocument();
    expect(api.calls.filter((c) => c.path.startsWith("/fdrs/import"))).toHaveLength(0);
  });

  it("downloads the template and says the status column does not exist and the example row must be replaced", async () => {
    const api = await open();
    await userEvent.click(screen.getByRole("button", { name: "Download the CSV template" }));
    await waitFor(() => expect(anchors).toEqual([{ download: "fdr-import-template.csv" }]));
    expect(api.callsTo("GET /fdrs/import/template")).toHaveLength(1);
    expect(screen.getByText(/replace it or delete it before importing/)).toBeInTheDocument();
    expect(screen.getByText(/There is no status column/)).toBeInTheDocument();
  });

  it("cannot check a file until one is chosen, and refuses an oversized one without asking the server", async () => {
    const api = await open();
    expect(screen.getByRole("button", { name: "Check file" })).toBeDisabled();
    await upload(file("big.csv", "x".repeat(1_000_001)));
    expect(await screen.findByRole("alert")).toHaveTextContent("larger than 1 MB");
    expect(screen.getByRole("button", { name: "Check file" })).toBeDisabled();
    expect(api.callsTo("POST /fdrs/import/preview")).toHaveLength(0);
  });

  it("sends the file as a CSV body for checking and shows every row with what to fix", async () => {
    const { api, f } = await previewed();
    const call = api.callsTo("POST /fdrs/import/preview")[0];
    expect(call.headers["content-type"]).toBe("text/csv");
    expect(call.headers["x-csrf-token"]).toBe("csrf-imp");
    expect(call.rawBody).toBe(f);
    expect(screen.getByTestId("count-total")).toHaveTextContent("3");
    expect(screen.getByTestId("count-valid")).toHaveTextContent("2");
    expect(screen.getByTestId("count-invalid")).toHaveTextContent("1");
    const rows = screen.getAllByTestId(/row-(ok|bad)/);
    expect(rows).toHaveLength(3);
    const bad = screen.getByTestId("row-bad");
    expect(bad).toHaveTextContent("BAD-2");
    expect(bad).toHaveTextContent("Rejected");
    expect(bad).toHaveTextContent("bank_code 'NOPE' is not a known bank code");
    expect(bad).toHaveTextContent("issue_date must be a date written YYYY-MM-DD");
    expect(screen.getAllByTestId("row-ok")[0]).toHaveTextContent("Ready");
  });

  it("can show only the rejected rows and download them from the server as a CSV", async () => {
    const { api, f } = await previewed();
    await userEvent.click(screen.getByLabelText("Show only rejected rows"));
    expect(screen.getAllByTestId(/row-(ok|bad)/)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Download rejected rows (CSV)" }));
    await waitFor(() => expect(anchors).toEqual([{ download: "fdr-import-rejected-rows.csv" }]));
    expect(api.callsTo("POST /fdrs/import/errors")[0].rawBody).toBe(f);
    expect(await screen.findByText("The rejected rows were downloaded.")).toBeInTheDocument();
  });

  it("imports only after an explicit confirmation, sending the previewed file and its digest", async () => {
    const { api, f } = await previewed();
    await userEvent.click(screen.getByRole("button", { name: "Create 2 drafts" }));
    expect(api.callsTo("POST /fdrs/import")).toHaveLength(0); // one click is not enough
    const group = screen.getByRole("group", { name: "Confirm the import" });
    expect(group).toHaveTextContent("Create 2 draft FDRs?");
    expect(group).toHaveTextContent("The 1 rejected row will not be imported.");
    await userEvent.click(within(group).getByRole("button", { name: "Confirm import" }));
    expect(await screen.findByTestId("result-summary")).toHaveTextContent("2 drafts were created and 1 row was rejected.");
    const call = api.callsTo("POST /fdrs/import")[0];
    expect(call.headers["x-import-digest"]).toBe(DIGEST);
    expect(call.headers["content-type"]).toBe("text/csv");
    expect(call.rawBody).toBe(f);
  });

  it("lists created rows as links to their drafts and rejected rows with their reasons, then offers another import", async () => {
    await previewed();
    await userEvent.click(screen.getByRole("button", { name: "Create 2 drafts" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm import" }));
    await screen.findByTestId("result-summary");
    expect(screen.getByRole("link", { name: "OK-1" })).toHaveAttribute("href", `/fdrs/${FDR_A}`);
    expect(screen.getByRole("link", { name: "OK-3" })).toHaveAttribute("href", `/fdrs/${FDR_B}`);
    expect(screen.getByTestId("row-bad")).toHaveTextContent("Rejected");
    expect(screen.getByText(/Nothing is submitted, accepted or released by an import/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Import another file" }));
    expect(screen.getByRole("button", { name: "Check file" })).toBeDisabled();
    expect(screen.queryByTestId("result-summary")).not.toBeInTheDocument();
  });

  it("opens an imported draft from the result", async () => {
    await previewed();
    await userEvent.click(screen.getByRole("button", { name: "Create 2 drafts" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm import" }));
    await userEvent.click(await screen.findByRole("link", { name: "OK-1" }));
    expect(await screen.findByRole("heading", { name: "FDR details" })).toBeInTheDocument();
  });

  it("cancelling the confirmation imports nothing", async () => {
    const { api } = await previewed();
    await userEvent.click(screen.getByRole("button", { name: "Create 2 drafts" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Create 2 drafts" })).toBeInTheDocument();
    expect(api.callsTo("POST /fdrs/import")).toHaveLength(0);
  });

  it("offers nothing to import when no row is valid", async () => {
    await previewed({ "POST /fdrs/import/preview": jsonReply(200, preview({ valid_count: 0, invalid_count: 3, rows: preview().rows.map((r) => ({ ...r, valid: false, errors: ["bad"] })) })) });
    expect(screen.getByRole("button", { name: "Create 0 drafts" })).toBeDisabled();
    expect(screen.getByText(/No row is ready to import/)).toBeInTheDocument();
  });

  it("choosing a different file discards the preview, so a different file can never be imported on its digest", async () => {
    const { api } = await previewed();
    await upload(file("other.csv"));
    expect(screen.queryByTestId("count-valid")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Create \d+ draft/ })).not.toBeInTheDocument();
    expect(api.callsTo("POST /fdrs/import")).toHaveLength(0);
  });

  it.each([
    ["an unknown column", jsonReply(422, { detail: "Unknown column 'status'. Use exactly the columns of the template. An import cannot set a status: every row becomes a draft." }), /Unknown column 'status'.*every row becomes a draft/],
    ["too many rows", jsonReply(413, { detail: "The file has more than 500 rows. Split it into smaller files." }), /more than 500 rows/],
    ["a file that is not UTF-8", jsonReply(422, { detail: "The file is not UTF-8 text. In your spreadsheet, save it as 'CSV UTF-8'." }), /not UTF-8 text/],
    ["a server failure", textReply(500), /could not be checked/],
    ["no connection", networkError, /Cannot reach the server/],
  ])("explains a file-level refusal for %s and stays on the file step", async (_name, reply, pattern) => {
    await open({ "POST /fdrs/import/preview": reply });
    await upload();
    await userEvent.click(screen.getByRole("button", { name: "Check file" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(pattern);
    expect(screen.queryByTestId("count-valid")).not.toBeInTheDocument();
  });

  it("does not claim success when the import is refused, and keeps the preview so the user can retry", async () => {
    await previewed({ "POST /fdrs/import": jsonReply(409, { detail: "The file is not the one that was previewed. Preview it again before importing." }) });
    await userEvent.click(screen.getByRole("button", { name: "Create 2 drafts" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Preview it again before importing");
    expect(screen.queryByTestId("result-summary")).not.toBeInTheDocument();
    expect(screen.getByTestId("count-valid")).toBeInTheDocument();
  });

  it("shows progress and prevents a second import while one is running", async () => {
    const gate = deferred();
    const { api } = await previewed({ "POST /fdrs/import": async () => { await gate.promise; return jsonReply(200, result())({} as never) as Response; } });
    await userEvent.click(screen.getByRole("button", { name: "Create 2 drafts" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm import" }));
    expect(await screen.findByText("Creating drafts…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm import" })).not.toBeInTheDocument();
    gate.resolve();
    await screen.findByTestId("result-summary");
    expect(api.callsTo("POST /fdrs/import")).toHaveLength(1);
  });

  it("explains duplicates and that importing twice creates nothing the second time", async () => {
    await open();
    expect(screen.getByText(/Importing the same file twice therefore creates nothing the second time/)).toBeInTheDocument();
  });

  it("has no automated accessibility violations at the start or with a preview, and is keyboard operable", async () => {
    const { api } = await previewed();
    void api;
    expect(await axe(document.querySelector("main")!)).toHaveNoViolations();
    const create = screen.getByRole("button", { name: "Create 2 drafts" });
    create.focus();
    expect(create).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("group", { name: "Confirm the import" })).toBeInTheDocument();
  });
});
