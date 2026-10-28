import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ReleaseList, ReleaseOut } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const FDR = "f0000000-0000-4000-8000-0000000000aa";

const release = (overrides: Partial<ReleaseOut> = {}): ReleaseOut => ({
  fdr_id: FDR, fdr_number: "REL-1", institution_code: "PICT", institution_name: "PICT Test Institute", bank_name: "Bank of Maharashtra",
  principal_amount: "1500000.00", maturity_date: "2026-10-01", fdr_status: "ACCEPTED", matured: true, eligible: true,
  eligibility_note: "Matured and accepted: eligible for a release request.", state: "NONE", can_request: false, can_decide: false,
  history: [], ...overrides,
});

const list = (items: ReleaseOut[]): ReleaseList => ({
  items, page: 1, page_size: 25, total: items.length, total_pages: items.length ? 1 : 0,
  note: "A release request is an authorisation workflow recorded in the audit trail. Approval is not a payment: this system does not transfer funds.",
});

function open(path: string, role: string, routes: Record<string, Handler | Handler[]>) {
  const api = installFakeApi(
    {
      "GET /auth/me": jsonReply(200, makeSession(makeUser({ role, full_name: "Meera Kulkarni", institution_id: role === "INSTITUTION_USER" ? "a0000000-0000-4000-8000-000000000001" : null }))),
      ...routes,
    },
    { dashboardDefaults: false },
  );
  renderApp(path);
  return api;
}

describe("release / refund list", () => {
  it("lists matured accepted FDRs with their release state and states that approval is not a payment", async () => {
    const items = [release(), release({ fdr_id: "f2", fdr_number: "REL-2", state: "REQUESTED" }), release({ fdr_id: "f3", fdr_number: "REL-3", state: "APPROVED" }),
      release({ fdr_id: "f4", fdr_number: "REL-4", state: "REJECTED" })];
    open("/release", "AICTE_REVIEWER", { "GET /release": jsonReply(200, list(items)) });
    expect(await screen.findByRole("heading", { name: "Release / Refund", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText("REL-1")).toBeInTheDocument();
    expect(screen.getAllByText(/does not transfer funds/i).length).toBeGreaterThan(0);
    const states = Object.fromEntries(screen.getAllByRole("row").slice(1).map((r) => [r.querySelector("th")!.textContent, r.textContent]));
    expect(states["REL-1"]).toContain("No request yet");
    expect(states["REL-2"]).toContain("Release requested");
    expect(states["REL-3"]).toContain("Release approved");
    expect(states["REL-4"]).toContain("Release request rejected");
    expect(within(screen.getByText("REL-2").closest("tr")!).getByRole("link", { name: "Open REL-2" })).toHaveAttribute("href", "/release/f2");
  });

  it("filters by state through the server and pages through the results", async () => {
    const api = open("/release", "AICTE_APPROVER", { "GET /release": jsonReply(200, { ...list([release()]), total: 60, total_pages: 3 }) });
    await screen.findByText("REL-1");
    await userEvent.selectOptions(screen.getByLabelText("Release state"), "Release requested");
    await screen.findByText("REL-1");
    expect(api.callsTo("GET /release").at(-1)!.query).toMatchObject({ state: "REQUESTED", page: "1" });
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("REL-1");
    expect(api.callsTo("GET /release").at(-1)!.query).toMatchObject({ state: "REQUESTED", page: "2" });
  });

  it("has honest empty states and an error state with a retry", async () => {
    open("/release", "AICTE_REVIEWER", { "GET /release": jsonReply(200, list([])) });
    expect(await screen.findByText("No accepted FDR has reached its maturity date yet.")).toBeInTheDocument();
  });

  it("shows a failed load as an error, not as an empty list", async () => {
    let calls = 0;
    open("/release", "AICTE_REVIEWER", { "GET /release": (req) => (calls++ === 0 ? textReply(500)(req) : jsonReply(200, list([release()]))(req)) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load release requests/i);
    expect(screen.queryByText(/No accepted FDR/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("REL-1")).toBeInTheDocument();
  });
});

describe("release request (institution)", () => {
  const url = `/release/${FDR}`;
  const detailRoute = (r: ReleaseOut) => ({ [`GET /fdrs/${FDR}/release`]: jsonReply(200, r) });

  it("asks for confirmation that says what a request is and is not, and sends nothing until confirmed", async () => {
    const api = open(url, "INSTITUTION_USER", { ...detailRoute(release({ can_request: true })) });
    await userEvent.click(await screen.findByRole("button", { name: "Request release" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/asks AICTE to authorise the release of REL-1/);
    expect(dialog).toHaveTextContent(/does not transfer funds/i);
    expect(api.callsTo(`POST /fdrs/${FDR}/release/request`)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api.callsTo(`POST /fdrs/${FDR}/release/request`)).toHaveLength(0);
  });

  it("records the request, shows the new state and the attributed history, and offers no second request", async () => {
    const requested = release({ state: "REQUESTED", can_request: false, history: [{ action: "release.requested", occurred_at: "2026-10-27T10:30:00Z", actor_role: "INSTITUTION_USER", actor_name: "Meera Kulkarni", reason: null }] });
    const api = open(url, "INSTITUTION_USER", { ...detailRoute(release({ can_request: true })), [`POST /fdrs/${FDR}/release/request`]: jsonReply(201, requested) });
    await userEvent.click(await screen.findByRole("button", { name: "Request release" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm request" }));
    expect(await screen.findByText("Recorded: release requested.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request release" })).not.toBeInTheDocument();
    const history = within(screen.getByRole("region", { name: "History" }));
    expect(history.getByText("Release requested")).toBeInTheDocument();
    expect(history.getByText(/Meera Kulkarni · Institution user · 27 Oct 2026, 10:30 UTC/)).toBeInTheDocument();
    expect(api.callsTo(`POST /fdrs/${FDR}/release/request`)[0].body).toBeUndefined();
    expect(screen.getAllByText(/does not transfer funds/i).length).toBeGreaterThan(0);
  });

  it("sends a double-clicked confirmation only once", async () => {
    const gate = deferred();
    const api = open(url, "INSTITUTION_USER", {
      ...detailRoute(release({ can_request: true })),
      [`POST /fdrs/${FDR}/release/request`]: async (req) => { await gate.promise; return jsonReply(201, release({ state: "REQUESTED" }))(req) as Response; },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Request release" }));
    const confirm = within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm request" });
    await userEvent.dblClick(confirm);
    expect(await screen.findByRole("button", { name: "Recording…" })).toBeDisabled();
    expect(api.callsTo(`POST /fdrs/${FDR}/release/request`)).toHaveLength(1);
    gate.resolve();
    await screen.findByText("Recorded: release requested.");
    expect(api.callsTo(`POST /fdrs/${FDR}/release/request`)).toHaveLength(1);
  });

  it.each([
    ["a conflict", jsonReply(409, { detail: "A release request is already open, or the release has been approved" }), /already open/],
    ["a refusal", jsonReply(403, { detail: "Forbidden" }), /not permitted/i],
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
  ])("reports %s and changes nothing on screen", async (_label, failing, message) => {
    open(url, "INSTITUTION_USER", { ...detailRoute(release({ can_request: true })), [`POST /fdrs/${FDR}/release/request`]: failing });
    await userEvent.click(await screen.findByRole("button", { name: "Request release" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm request" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Request release" })).toBeEnabled();
    expect(screen.queryByText(/Recorded:/)).not.toBeInTheDocument();
  });

  it("shows an FDR that is not eligible with the reason and no action", async () => {
    open(url, "INSTITUTION_USER", { ...detailRoute(release({ eligible: false, matured: false, eligibility_note: "Not yet matured: matures on 2027-01-01." })) });
    expect(await screen.findByText("Not yet matured: matures on 2027-01-01.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /request release/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Action" })).not.toBeInTheDocument();
    expect(screen.getByText("No release request has been made for this FDR.")).toBeInTheDocument();
  });
});

describe("release decision (approver)", () => {
  const url = `/release/${FDR}`;
  const open$ = (routes: Record<string, Handler | Handler[]> = {}, role = "AICTE_APPROVER", r = release({ state: "REQUESTED", can_decide: true })) =>
    open(url, role, { [`GET /fdrs/${FDR}/release`]: jsonReply(200, r), ...routes });

  it("approves in the approver's name after a confirmation that says no funds are transferred", async () => {
    const approved = release({ state: "APPROVED", history: [
      { action: "release.requested", occurred_at: "2026-10-27T09:00:00Z", actor_role: "INSTITUTION_USER", actor_name: "Asha", reason: null },
      { action: "release.approved", occurred_at: "2026-10-27T10:00:00Z", actor_role: "AICTE_APPROVER", actor_name: "Meera Kulkarni", reason: null }] });
    const api = open$({ [`POST /fdrs/${FDR}/release/approve`]: jsonReply(200, approved) });
    await userEvent.click(await screen.findByRole("button", { name: "Approve release" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/authorising the release of REL-1, in your name/);
    expect(dialog).toHaveTextContent(/does not transfer funds/i);
    expect(api.callsTo(`POST /fdrs/${FDR}/release/approve`)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm approval" }));
    expect(await screen.findByText("Recorded: release approved.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve release" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "History" })).getByText("Release approved by an approver")).toBeInTheDocument();
    expect(screen.getByText(/Meera Kulkarni · AICTE approver/)).toBeInTheDocument();
  });

  it("requires a reason to reject and sends only that fixed code", async () => {
    const rejected = release({ state: "REJECTED", history: [{ action: "release.rejected", occurred_at: "2026-10-27T10:00:00Z", actor_role: "AICTE_APPROVER", actor_name: "Meera Kulkarni", reason: "NOT_YET_DUE" }] });
    const api = open$({ [`POST /fdrs/${FDR}/release/reject`]: jsonReply(200, rejected) });
    await userEvent.click(await screen.findByRole("button", { name: "Reject request" }));
    const dialog = await screen.findByRole("alertdialog");
    const confirm = within(dialog).getByRole("button", { name: "Confirm rejection" });
    expect(confirm).toBeDisabled();
    await userEvent.selectOptions(within(dialog).getByLabelText("Reason (required)"), "Not yet due");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(await screen.findByText("Recorded: release request rejected.")).toBeInTheDocument();
    expect(api.callsTo(`POST /fdrs/${FDR}/release/reject`)[0].body).toEqual({ reason: "NOT_YET_DUE" });
    expect(screen.getByText("Reason: Not yet due")).toBeInTheDocument();
  });

  it.each(["AICTE_REVIEWER", "ADMIN", "AUDITOR"])("shows %s the request and its history but no decision buttons", async (role) => {
    open$({}, role, release({ state: "REQUESTED", can_decide: false }));
    expect(await screen.findByText("REL-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve release|reject request/i })).not.toBeInTheDocument();
  });

  it("reports a decision that the server refused, such as a request that was already decided", async () => {
    open$({ [`POST /fdrs/${FDR}/release/approve`]: jsonReply(409, { detail: "There is no open release request to decide" }) });
    await userEvent.click(await screen.findByRole("button", { name: "Approve release" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm approval" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("There is no open release request to decide");
    expect(screen.queryByText(/Recorded:/)).not.toBeInTheDocument();
  });

  it("links to the FDR's documents and shows a not-found FDR as an error", async () => {
    open$();
    expect(await screen.findByRole("link", { name: "View this FDR's documents" })).toHaveAttribute("href", `/documents/${FDR}`);
  });

  it("explains an FDR the user cannot see as an error (the server answers 404)", async () => {
    open(url, "INSTITUTION_USER", { [`GET /fdrs/${FDR}/release`]: jsonReply(404, { detail: "FDR not found" }) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load this release request/i);
  });
});
