import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

import type { ReportOut, SessionItem } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const session = (role: string, id = "u-self") =>
  jsonReply(200, makeSession(makeUser({ role, id, institution_id: role === "INSTITUTION_USER" ? "a0000000-0000-4000-8000-000000000001" : null }), "csrf-acct"));

// ---- Security page ------------------------------------------------------------------------------------------------
const mine = (over: Partial<SessionItem> = {}): SessionItem => ({
  handle: "h-current", current: true, created_at: "2026-10-28T08:00:00Z", last_seen_at: "2026-10-28T09:00:00Z", expires_at: "2026-10-28T20:00:00Z",
  browser: "Chrome", system: "Windows", network: "203.0.113.x", ...over,
});
const other = (over: Partial<SessionItem> = {}): SessionItem => mine({ handle: "h-other", current: false, browser: "Firefox", system: "Linux", network: null, ...over });
const list = (items: SessionItem[]) => ({ items, idle_timeout_minutes: 30, absolute_timeout_minutes: 720 });
const ACTIVITY = {
  items: [
    { occurred_at: "2026-10-28T08:00:00Z", event: "Sign-in", outcome: "success", network: "203.0.113.x" },
    { occurred_at: "2026-10-27T22:10:00Z", event: "Failed sign-in attempt", outcome: "failure", network: null },
    { occurred_at: "2026-10-27T09:00:00Z", event: "Sign-in", outcome: "success", network: "203.0.113.x" },
  ],
  previous_sign_in_at: "2026-10-27T09:00:00Z",
};

function openSecurity(routes: Record<string, Handler | Handler[]> = {}, role = "AICTE_REVIEWER") {
  const api = installFakeApi({
    "GET /auth/me": session(role),
    "GET /account/sessions": jsonReply(200, list([mine(), other()])),
    "GET /account/activity": jsonReply(200, ACTIVITY),
    ...routes,
  });
  const view = renderApp("/security");
  return { api, view };
}

describe("account security page", () => {
  it("lists the server's sessions, marks this one, and shows only coarse details", async () => {
    openSecurity();
    const sessions = await screen.findByRole("list", { name: "Active sessions" });
    const items = within(sessions).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Chrome on Windows");
    expect(items[0]).toHaveTextContent("This session");
    expect(items[0]).toHaveTextContent("network 203.0.113.x");
    expect(items[1]).toHaveTextContent("Firefox on Linux");
    expect(items[0]).toHaveTextContent("after 30 minutes without use");
    expect(within(items[0]).queryByRole("button", { name: /End/ })).not.toBeInTheDocument(); // the current session has no end button
    expect(document.body.textContent).not.toContain("h-current"); // the handle is never displayed
  });

  it("ends one session only after a second, explicit confirmation, then refreshes", async () => {
    const afterwards = jsonReply(200, list([mine()]));
    const { api } = openSecurity({
      "GET /account/sessions": [jsonReply(200, list([mine(), other()])), afterwards],
      "POST /account/sessions/:handle/revoke": jsonReply(200, { revoked: 1 }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "End the Firefox on Linux session" }));
    expect(api.callsTo("POST /account/sessions/h-other/revoke")).toHaveLength(0); // one click is not enough
    await userEvent.click(screen.getByRole("button", { name: "Confirm: end this session" }));
    expect(await screen.findByRole("status", { name: "" })).toBeInTheDocument();
    expect(await screen.findByText(/1 session was ended/)).toBeInTheDocument();
    const call = api.callsTo("POST /account/sessions/h-other/revoke")[0];
    expect(call.headers["x-csrf-token"]).toBe("csrf-acct");
    await waitFor(() => expect(screen.queryByText("Firefox on Linux")).not.toBeInTheDocument());
  });

  it("cancelling a confirmation changes nothing", async () => {
    const { api } = openSecurity();
    await userEvent.click(await screen.findByRole("button", { name: "End the Firefox on Linux session" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "End the Firefox on Linux session" })).toBeInTheDocument();
    expect(api.callsTo("POST /account/sessions/h-other/revoke")).toHaveLength(0);
  });

  it("signs out all other sessions after confirmation and says how many ended", async () => {
    const { api } = openSecurity({
      "GET /account/sessions": [jsonReply(200, list([mine(), other(), other({ handle: "h-3" })])), jsonReply(200, list([mine()]))],
      "POST /account/sessions/revoke-others": jsonReply(200, { revoked: 2 }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Sign out all other sessions" }));
    expect(screen.getByText(/End all 2 other sessions\?/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm: sign out all others" }));
    expect(await screen.findByText(/2 sessions were ended. They can no longer be used./)).toBeInTheDocument();
    expect(api.callsTo("POST /account/sessions/revoke-others")).toHaveLength(1);
    expect(await screen.findByRole("button", { name: "Sign out all other sessions" })).toBeDisabled();
    expect(screen.getByText("You have no other active sessions.")).toBeInTheDocument();
  });

  it("disables 'sign out all others' when there are none", async () => {
    openSecurity({ "GET /account/sessions": jsonReply(200, list([mine()])) });
    expect(await screen.findByRole("button", { name: "Sign out all other sessions" })).toBeDisabled();
  });

  it.each([
    ["the session has already ended", jsonReply(404, { detail: "x" }), /not found. It may already have ended/],
    ["the server fails", textReply(500), /could not be made/],
    ["the network fails", networkError, /could not be made/],
  ])("explains a failure when %s, and nothing is claimed to have ended", async (_name, reply, message) => {
    openSecurity({ "POST /account/sessions/:handle/revoke": reply });
    await userEvent.click(await screen.findByRole("button", { name: "End the Firefox on Linux session" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm: end this session" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText(/session was ended/)).not.toBeInTheDocument();
  });

  it("shows recent sign-in activity, the previous successful sign-in and failed attempts in words", async () => {
    openSecurity();
    expect(await screen.findByTestId("previous")).toHaveTextContent("Your previous successful sign-in was 27 Oct 2026, 09:00 UTC");
    const table = screen.getByRole("table", { name: "Recent sign-in activity" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("Failed sign-in attempt");
    expect(rows[1]).toHaveTextContent("Not successful");
    expect(rows[1]).toHaveTextContent("Not recorded");
  });

  it("handles no previous sign-in and no activity honestly", async () => {
    openSecurity({ "GET /account/activity": jsonReply(200, { items: [], previous_sign_in_at: null }) });
    expect(await screen.findByTestId("previous")).toHaveTextContent("No earlier successful sign-in is on record.");
    expect(screen.getByText("No activity is on record yet.")).toBeInTheDocument();
  });

  it("shows each panel's failure separately, with its own retry", async () => {
    openSecurity({ "GET /account/sessions": [textReply(500), jsonReply(200, list([mine()]))] });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not load your sessions/i);
    expect(await screen.findByTestId("previous")).toBeInTheDocument(); // the activity panel is unaffected
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Chrome on Windows")).toBeInTheDocument();
  });

  it("is linked from the sidebar and has no automated accessibility violations", async () => {
    const { view } = openSecurity();
    await screen.findByRole("list", { name: "Active sessions" });
    await screen.findByTestId("previous");
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Security" })).toHaveAttribute("href", "/security");
    expect(await axe(view.container)).toHaveNoViolations();
  });
});

// ---- administrator: end a user's sessions -------------------------------------------------------------------------
const PICT = { id: "a0000000-0000-4000-8000-000000000001", code: "PICT", name: "PICT Test Institute", state: null, institution_type: null, contact_email: null, status: "active", created_at: "2026-01-01T00:00:00Z" };
const account = (id: string, email: string, name: string) => ({
  id, email, full_name: name, role: "AICTE_REVIEWER", institution_id: null, is_active: true, must_change_password: false, is_locked: false,
  last_login_at: null, created_at: "2026-01-01T00:00:00Z",
});

function openAdmin(routes: Record<string, Handler | Handler[]> = {}) {
  const api = installFakeApi({
    "GET /auth/me": session("ADMIN", "u-self"),
    "GET /institutions": jsonReply(200, { items: [PICT], total: 1, limit: 200, offset: 0 }),
    "GET /users": jsonReply(200, { items: [account("u-1", "asha@example.invalid", "Asha Patil"), account("u-self", "root@example.invalid", "Root Admin")], total: 2, limit: 50, offset: 0 }),
    "GET /banks": jsonReply(200, { items: [], page: 1, page_size: 500, total: 0, total_pages: 0 }),
    ...routes,
  }, { dashboardDefaults: false });
  renderApp("/admin");
  return api;
}

describe("administrator ends a user's sessions", () => {
  it("asks for confirmation, then ends them through the API and reports the count", async () => {
    const api = openAdmin({ "POST /users/u-1/sessions/revoke": jsonReply(200, { revoked: 3 }) });
    await userEvent.click(await screen.findByRole("button", { name: "End all sessions of asha@example.invalid" }));
    expect(api.callsTo("POST /users/u-1/sessions/revoke")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Confirm: end all sessions" }));
    expect(await screen.findByText("3 sessions ended.")).toBeInTheDocument();
    expect(api.callsTo("POST /users/u-1/sessions/revoke")[0].headers["x-csrf-token"]).toBe("csrf-acct");
  });

  it("warns the administrator when the target is themselves", async () => {
    openAdmin({ "POST /users/u-self/sessions/revoke": jsonReply(200, { revoked: 1 }) });
    await userEvent.click(await screen.findByRole("button", { name: "End all sessions of root@example.invalid" }));
    expect(screen.getByRole("button", { name: "Confirm: sign me out everywhere" })).toBeInTheDocument();
  });

  it("says plainly when there was nothing to end, and when the request fails", async () => {
    openAdmin({ "POST /users/u-1/sessions/revoke": [jsonReply(200, { revoked: 0 }), textReply(500)] });
    const open = () => screen.findByRole("button", { name: "End all sessions of asha@example.invalid" });
    await userEvent.click(await open());
    await userEvent.click(screen.getByRole("button", { name: "Confirm: end all sessions" }));
    expect(await screen.findByText("They had no active sessions.")).toBeInTheDocument();
    await userEvent.click(await open());
    await userEvent.click(screen.getByRole("button", { name: "Confirm: end all sessions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be ended/);
  });

  it("does not let a second click happen while the request is running", async () => {
    const gate = deferred();
    const api = openAdmin({ "POST /users/u-1/sessions/revoke": async () => { await gate.promise; return jsonReply(200, { revoked: 1 })({} as never) as Response; } });
    await userEvent.click(await screen.findByRole("button", { name: "End all sessions of asha@example.invalid" }));
    const confirm = screen.getByRole("button", { name: "Confirm: end all sessions" });
    await userEvent.click(confirm);
    expect(confirm).toBeDisabled();
    gate.resolve();
    await screen.findByText("1 session ended.");
    expect(api.callsTo("POST /users/u-1/sessions/revoke")).toHaveLength(1);
  });
});

// ---- PDF reports --------------------------------------------------------------------------------------------------
const REPORT: ReportOut = {
  generated_on: "2026-10-28", scope: "Every institution's FDRs that have left draft.", fdr_total: 1, principal_live_total: "100.00",
  by_status: [{ key: "SUBMITTED", label: "Submitted", count: 1, principal: "100.00" }], by_institution: [], maturities: [], release: [],
  release_status: { available: true, note: null }, verification: [], verification_status: { available: true, note: null },
  exceptions: [], exceptions_status: { available: true, note: null },
};
const pdfReply = (): Handler => () => new Response(new Blob(["%PDF-1.4"], { type: "application/pdf" }), { status: 200, headers: { "Content-Type": "application/pdf" } });

describe("PDF downloads on the reports page", () => {
  let anchors: Array<{ download: string }>;
  beforeEach(() => {
    anchors = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:pdf"), revokeObjectURL: vi.fn() }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { anchors.push({ download: this.download }); });
  });
  afterEach(() => vi.restoreAllMocks());

  function openReports(role: string, routes: Record<string, Handler | Handler[]> = {}) {
    const api = installFakeApi({
      "GET /auth/me": session(role),
      "GET /reports/summary": jsonReply(200, REPORT),
      "GET /institutions": jsonReply(200, { items: [PICT], total: 1, limit: 100, offset: 0 }),
      ...routes,
    }, { dashboardDefaults: false });
    renderApp("/reports");
    return api;
  }
  const names = async () => (await screen.findAllByRole("button", { name: /\(PDF\)$/ })).map((b) => b.getAttribute("aria-label"));

  it.each([
    ["INSTITUTION_USER", ["FDR summary", "Institution-wise summary", "Maturity report"]],
    ["AICTE_REVIEWER", ["FDR summary", "Institution-wise summary", "Maturity report", "Pending review report", "Exceptions and discrepancies report"]],
    ["ADMIN", ["FDR summary", "Institution-wise summary", "Maturity report", "Audit trail report"]],
    ["AUDITOR", ["FDR summary", "Institution-wise summary", "Maturity report", "Audit trail report"]],
  ])("offers %s only the PDFs its role may download", async (role, expected) => {
    openReports(role);
    expect(await names()).toEqual(expected.map((n) => `Download ${n} (PDF)`));
  });

  it("downloads through the API with the chosen period and institution, and names the file after the report date", async () => {
    const api = openReports("AICTE_REVIEWER", { "GET /reports/pdf/maturity": pdfReply() });
    await screen.findByRole("option", { name: /PICT/ });
    await userEvent.selectOptions(screen.getByLabelText("Maturity period"), "60");
    await userEvent.selectOptions(screen.getByLabelText("Institution"), screen.getByRole("option", { name: /PICT/ }));
    await userEvent.click(screen.getByRole("button", { name: "Download Maturity report (PDF)" }));
    await waitFor(() => expect(anchors).toEqual([{ download: "fdr-maturity-2026-10-28.pdf" }]));
    expect(api.callsTo("GET /reports/pdf/maturity")[0].query).toEqual({ days: "60", institution_id: PICT.id });
    expect(await screen.findByText("The PDF was downloaded.")).toBeInTheDocument();
  });

  it("sends no institution id for an institution user, and the audit window for an administrator", async () => {
    const own = openReports("INSTITUTION_USER", { "GET /reports/pdf/maturity": pdfReply() });
    await userEvent.click(await screen.findByRole("button", { name: "Download Maturity report (PDF)" }));
    await waitFor(() => expect(anchors).toHaveLength(1));
    expect(screen.queryByLabelText("Institution")).not.toBeInTheDocument();
    expect(own.callsTo("GET /reports/pdf/maturity")[0].query).toEqual({ days: "90" });
  });

  it("passes the audit window", async () => {
    const api = openReports("ADMIN", { "GET /reports/pdf/audit": pdfReply() });
    await userEvent.selectOptions(await screen.findByLabelText("Audit window"), "7");
    await userEvent.click(screen.getByRole("button", { name: "Download Audit trail report (PDF)" }));
    await waitFor(() => expect(anchors).toEqual([{ download: "fdr-audit-2026-10-28.pdf" }]));
    expect(api.callsTo("GET /reports/pdf/audit")[0].query).toEqual({ days: "7" });
  });

  it.each([
    ["is forbidden", jsonReply(403, { detail: "Forbidden" }), "You are not permitted to export this report."],
    ["fails on the server", textReply(500), "The PDF could not be generated. Please try again."],
    ["cannot reach the server", networkError, "Cannot reach the server. Check your connection and try again."],
  ])("shows a plain message when the PDF %s, and never claims a download", async (_name, reply, message) => {
    openReports("AICTE_REVIEWER", { "GET /reports/pdf/summary": reply });
    await userEvent.click(await screen.findByRole("button", { name: "Download FDR summary (PDF)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(anchors).toEqual([]);
    expect(screen.queryByText("The PDF was downloaded.")).not.toBeInTheDocument();
  });

  it("shows that it is working and prevents a double download", async () => {
    const gate = deferred();
    const api = openReports("AICTE_REVIEWER", { "GET /reports/pdf/summary": async () => { await gate.promise; return pdfReply()({} as never) as Response; } });
    const button = await screen.findByRole("button", { name: "Download FDR summary (PDF)" });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Preparing…");
    gate.resolve();
    await waitFor(() => expect(anchors).toHaveLength(1));
    expect(api.callsTo("GET /reports/pdf/summary")).toHaveLength(1);
  });

  it("states that PDFs are not digitally signed and show English text only", async () => {
    openReports("AICTE_REVIEWER");
    expect(await screen.findByText(/not digitally signed and shows English text only/)).toBeInTheDocument();
  });
});
