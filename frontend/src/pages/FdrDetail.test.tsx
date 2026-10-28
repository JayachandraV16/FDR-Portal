import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installFakeApi, jsonReply, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const ID = "3f2b8c1e-5a4d-4e6f-9a7b-1c2d3e4f5a6b";

const HISTORY = {
  history: [
    { from_status: null, to_status: "DRAFT", actor_role: "INSTITUTION_USER", comment: null, occurred_at: "2026-10-01T09:00:00Z" },
    { from_status: "DRAFT", to_status: "SUBMITTED", actor_role: "INSTITUTION_USER", comment: null, occurred_at: "2026-10-02T09:00:00Z" },
    { from_status: "SUBMITTED", to_status: "CORRECTION_REQUESTED", actor_role: "AICTE_REVIEWER", comment: "Please attach the receipt.", occurred_at: "2026-10-03T09:00:00Z" },
  ],
  revisions: [],
};

async function open(role: string, routes: Record<string, Handler | Handler[]> = {}, fdr = {}) {
  const api = installFakeApi({
    "GET /auth/me": jsonReply(200, makeSession(makeUser({ role }), "csrf-detail")),
    [`GET /fdrs/${ID}`]: jsonReply(200, makeFdr({
      id: ID, fdr_number: "DET-100", principal_amount: "12345678.90", interest_rate_pct: "7.25", status: "CORRECTION_REQUESTED",
      branch_name: null, depositor_name: "Pune Institute", ...fdr,
    })),
    [`GET /fdrs/${ID}/history`]: jsonReply(200, HISTORY),
    ...routes,
  });
  renderApp(`/fdrs/${ID}`);
  await screen.findByRole("heading", { name: "FDR details" });
  return api;
}

describe("FDR details page", () => {
  it("shows the record with exact money formatting, 'Not provided' for blanks, and no verification claim", async () => {
    await open("AICTE_REVIEWER");
    const panel = await screen.findByRole("region", { name: "DET-100" });
    expect(panel).toHaveTextContent("₹1,23,45,678.90");
    expect(panel).toHaveTextContent("7.25%");
    expect(panel).toHaveTextContent("Correction requested");
    expect(within(panel).getByText("Branch").nextElementSibling).toHaveTextContent("Not provided");
    expect(screen.getByText(/does not mean the FDR has been verified with the bank/i)).toBeInTheDocument();
  });

  it("lists status history oldest first with roles only, including a reviewer's comment", async () => {
    await open("AICTE_REVIEWER");
    const panel = await screen.findByRole("region", { name: "Status history" });
    const items = await within(panel).findAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual([
      expect.stringContaining("Created as Draft"),
      expect.stringContaining("Draft → Submitted"),
      expect.stringContaining("Submitted → Correction requested"),
    ]);
    expect(items[2]).toHaveTextContent("AICTE reviewer");
    expect(items[2]).toHaveTextContent("Please attach the receipt.");
  });

  it("offers an institution user the actions that apply to a returned FDR, and no reviewer links", async () => {
    await open("INSTITUTION_USER");
    await screen.findByRole("region", { name: "DET-100" });
    expect(screen.getByRole("link", { name: "Make corrections" })).toHaveAttribute("href", `/fdrs/${ID}/edit`);
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Documents" })).toHaveAttribute("href", `/documents/${ID}`);
    expect(screen.queryByRole("link", { name: "Open for review" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("link", { name: "Verification" })).not.toBeInTheDocument();
  });

  it("offers reviewers review and verification, but never an edit link", async () => {
    await open("AICTE_APPROVER");
    await screen.findByRole("region", { name: "DET-100" });
    expect(screen.getByRole("link", { name: "Open for review" })).toHaveAttribute("href", `/review/${ID}`);
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Verification" })).toHaveAttribute("href", `/verification/${ID}`);
    expect(screen.queryByRole("link", { name: /edit draft|make corrections/i })).not.toBeInTheDocument();
  });

  it("offers no edit link for a record that is no longer editable", async () => {
    await open("INSTITUTION_USER", {}, { status: "ACCEPTED" });
    await screen.findByRole("region", { name: "DET-100" });
    expect(screen.queryByRole("link", { name: /edit draft|make corrections/i })).not.toBeInTheDocument();
  });

  it("says so, with a retry, when the record cannot be loaded, instead of showing blanks", async () => {
    await open("AICTE_REVIEWER", { [`GET /fdrs/${ID}`]: [textReply(500), jsonReply(200, makeFdr({ id: ID, fdr_number: "DET-100" }))] });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load this fdr/i);
    expect(screen.queryByRole("region", { name: "DET-100" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "DET-100" })).toBeInTheDocument();
  });

  it("shows a history failure inside the history panel only, and keeps the record", async () => {
    await open("AICTE_REVIEWER", { [`GET /fdrs/${ID}/history`]: textReply(500) });
    expect(await screen.findByRole("region", { name: "DET-100" })).toBeInTheDocument();
    const panel = screen.getByRole("region", { name: "Status history" });
    expect(await within(panel).findByRole("alert")).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("No status changes are recorded yet.");
  });

  it("treats a record the server will not show (404) as not available, without revealing anything", async () => {
    await open("INSTITUTION_USER", { [`GET /fdrs/${ID}`]: jsonReply(404, { detail: "Not found" }) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("DET-100");
  });
});

describe("registry links to the details page", () => {
  it("opens the details page from the registry row", async () => {
    installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession(makeUser({ role: "AICTE_REVIEWER" }), "csrf-detail")),
      "GET /fdrs": jsonReply(200, { items: [makeFdr({ id: ID, fdr_number: "DET-100" })], page: 1, page_size: 25, total: 1, total_pages: 1 }),
      "GET /banks": jsonReply(200, { items: [], page: 1, page_size: 100, total: 0, total_pages: 0 }),
      "GET /institutions": jsonReply(200, { items: [], total: 0, limit: 100, offset: 0 }),
      [`GET /fdrs/${ID}`]: jsonReply(200, makeFdr({ id: ID, fdr_number: "DET-100" })),
      [`GET /fdrs/${ID}/history`]: jsonReply(200, { history: [], revisions: [] }),
    });
    renderApp("/fdrs");
    await userEvent.click(await screen.findByRole("link", { name: "View details of DET-100" }));
    expect(await screen.findByRole("heading", { name: "FDR details" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(`/fdrs/${ID}`);
  });
});
