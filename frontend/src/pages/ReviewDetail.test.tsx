import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { deferred, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { COMMENT, FDR_ID, entry, reviewBackend, submittedFdr, submittedHistory } from "../test/reviewBackend";
import { renderApp } from "../test/render";

async function openReview(
  role = "AICTE_APPROVER",
  fdr = submittedFdr(),
  extra: Record<string, Handler | Handler[]> = {},
  history = submittedHistory(),
) {
  const backend = reviewBackend(fdr, { role, extra, history });
  renderApp(`/review/${FDR_ID}`);
  await screen.findByRole("heading", { name: "Review FDR" });
  await screen.findByText("Submitted details");
  return backend;
}
const button = (name: string | RegExp) => screen.getByRole("button", { name });
const startAction = (label: string) => userEvent.click(button(label));
const confirmGroup = () => screen.findByRole("group");
const commentBox = () => screen.getByLabelText(/comment|reason|corrected/i);

describe("what is shown", () => {
  it("shows every submitted field, the version and the submission time", async () => {
    await openReview("AICTE_APPROVER", submittedFdr({ principal_amount: "12345678.90", interest_rate_pct: null, purpose: null, maturity_date: "2027-03-31" }));
    const details = within(screen.getByRole("region", { name: "Submitted details" }));
    expect(details.getByText("PICT Test Institute")).toBeInTheDocument();
    expect(details.getByText("Bank of Maharashtra")).toBeInTheDocument();
    expect(details.getByText("Main Branch")).toBeInTheDocument();
    expect(details.getByText("Test Depositor")).toBeInTheDocument();
    expect(details.getByText("₹1,23,45,678.90")).toBeInTheDocument();
    expect(details.getByText("31 Mar 2027")).toBeInTheDocument();
    expect(details.getByText("26 Oct 2026, 09:00 UTC")).toBeInTheDocument();
    expect(details.getAllByText("Not provided")).toHaveLength(2); // null stays null: nothing invented
    expect(screen.getByText("Version 2")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Review FDR" })).toBeInTheDocument();
  });

  it("separates submitted information from verified information", async () => {
    await openReview();
    const notes = screen.getAllByRole("note").map((n) => n.textContent ?? "");
    expect(notes.some((text) => /submitted information, not verified information/i.test(text))).toBe(true);
    expect(notes.some((text) => /demonstration environment/i.test(text))).toBe(true);
    expect(document.body.textContent).toMatch(/not been checked with the bank/i);
    expect(document.body.textContent).not.toMatch(/bank-verified|verified by the bank/i);
  });

  it("shows the history, including earlier comments and the number of submissions", async () => {
    await openReview("AICTE_APPROVER", submittedFdr({ row_version: 4 }), {}, [
      entry("DRAFT", null, "INSTITUTION_USER", null, "2026-10-20T09:00:00Z"),
      entry("SUBMITTED", "DRAFT", "INSTITUTION_USER", null, "2026-10-21T09:00:00Z"),
      entry("CORRECTION_REQUESTED", "SUBMITTED", "AICTE_REVIEWER", COMMENT, "2026-10-22T09:00:00Z"),
      entry("SUBMITTED", "CORRECTION_REQUESTED", "INSTITUTION_USER", null, "2026-10-23T09:00:00Z"),
    ]);
    const history = within(screen.getByRole("region", { name: "History" }));
    expect(await history.findByText(COMMENT)).toBeInTheDocument();
    expect(history.getByText(/AICTE reviewer · 22 Oct 2026, 09:00 UTC/)).toBeInTheDocument();
    expect(history.getAllByRole("listitem")).toHaveLength(4);
    expect(history.getByText(/submitted 2 times; every submission is kept/i)).toBeInTheDocument();
  });

  it("keeps the details usable when only the history fails to load, and can retry it", async () => {
    let calls = 0;
    await openReview("AICTE_APPROVER", submittedFdr(), {
      [`GET /fdrs/${FDR_ID}/history`]: (req) =>
        calls++ === 0 ? textReply(500)(req) : jsonReply(200, { history: submittedHistory(), revisions: [] })(req),
    });
    const history = screen.getByRole("region", { name: "History" });
    expect(await within(history).findByRole("alert")).toHaveTextContent(/could not load the history/i);
    expect(screen.getByText("Test Depositor")).toBeInTheDocument();
    await userEvent.click(within(history).getByRole("button", { name: "Retry" }));
    expect(await within(history).findAllByRole("listitem")).toHaveLength(2);
  });
});

describe("role-aware actions", () => {
  it("offers approvers all three decisions", async () => {
    await openReview("AICTE_APPROVER");
    for (const label of ["Request corrections", "Accept", "Reject"]) expect(button(label)).toBeInTheDocument();
  });

  it("offers reviewers only the correction request", async () => {
    await openReview("AICTE_REVIEWER");
    expect(button("Request corrections")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.getByText(/accepting and rejecting are done by approvers/i)).toBeInTheDocument();
  });

  it.each(["INSTITUTION_USER", "ADMIN", "AUDITOR"])("%s gets an explanation and no data or actions", async (role) => {
    const { api } = reviewBackend(submittedFdr(), { role });
    renderApp(`/review/${FDR_ID}`);
    expect(await screen.findByRole("alert")).toHaveTextContent(/only institution users can do this/i);
    expect(screen.queryByRole("button", { name: /accept|reject|corrections/i })).not.toBeInTheDocument();
    expect(api.callsTo(`GET /fdrs/${FDR_ID}`)).toHaveLength(0);
  });

  it.each(["CORRECTION_REQUESTED", "ACCEPTED", "REJECTED"])("offers no decision on a %s FDR", async (status) => {
    await openReview("AICTE_APPROVER", submittedFdr({ status }));
    expect(screen.queryByRole("button", { name: /accept|reject|request corrections/i })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Decision" })).toBeInTheDocument();
  });
});

describe("request corrections", () => {
  it("requires a comment and sends nothing without one", async () => {
    const { api } = await openReview("AICTE_REVIEWER");
    await startAction("Request corrections");
    await confirmGroup();
    await userEvent.click(button("Send back to the institution"));
    expect(await screen.findByRole("alert")).toHaveTextContent("A comment is required.");
    await userEvent.type(commentBox(), "   ");
    await userEvent.click(button("Send back to the institution"));
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/request-correction`)).toHaveLength(0);
  });

  it("sends the trimmed comment with the current version, then returns to the queue with a confirmation", async () => {
    const { api, store } = await openReview("AICTE_REVIEWER");
    await startAction("Request corrections");
    await userEvent.type(commentBox(), `  ${COMMENT}  `);
    await userEvent.click(button("Send back to the institution"));

    expect(await screen.findByRole("heading", { name: "Review queue" })).toBeInTheDocument();
    expect(screen.getByText(/corrections requested for RV-FDR-1/i)).toBeInTheDocument();
    const [post] = api.callsTo(`POST /fdrs/${FDR_ID}/request-correction`);
    expect(post.body).toEqual({ row_version: 2, comment: COMMENT });
    expect(post.headers["x-csrf-token"]).toBe("csrf-token-for-tests");
    expect(store.fdr?.status).toBe("CORRECTION_REQUESTED");
    expect(await screen.findByText("No FDRs are awaiting review.")).toBeInTheDocument(); // it left the queue
  });

  it("can be cancelled without any request", async () => {
    const { api } = await openReview("AICTE_REVIEWER");
    await startAction("Request corrections");
    await userEvent.type(commentBox(), "draft remark");
    await userEvent.click(button("Cancel"));
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(api.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });
});

describe("accept and reject", () => {
  it("accepts with no comment, sending no empty comment, and warns it is not bank verification", async () => {
    const { api, store } = await openReview("AICTE_APPROVER");
    await startAction("Accept");
    expect(await confirmGroup()).toHaveTextContent(/does not verify the fdr with the bank/i);
    await userEvent.click(button("Confirm acceptance"));
    expect(await screen.findByText("RV-FDR-1 was accepted.")).toBeInTheDocument();
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/accept`)[0].body).toEqual({ row_version: 2 });
    expect(store.fdr).toMatchObject({ status: "ACCEPTED", decided_at: expect.any(String) });
  });

  it("includes an optional comment when one is given", async () => {
    const { api } = await openReview("AICTE_APPROVER");
    await startAction("Accept");
    await userEvent.type(commentBox(), "  Checked against the submitted advice.  ");
    await userEvent.click(button("Confirm acceptance"));
    await screen.findByText("RV-FDR-1 was accepted.");
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/accept`)[0].body).toEqual({ row_version: 2, comment: "Checked against the submitted advice." });
  });

  it("requires a reason to reject", async () => {
    const { api } = await openReview("AICTE_APPROVER");
    await startAction("Reject");
    await confirmGroup();
    await userEvent.click(button("Confirm rejection"));
    expect(await screen.findByRole("alert")).toHaveTextContent("A comment is required.");
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/reject`)).toHaveLength(0);
    await userEvent.type(commentBox(), "Amount does not match the advice.");
    await userEvent.click(button("Confirm rejection"));
    expect(await screen.findByText("RV-FDR-1 was rejected.")).toBeInTheDocument();
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/reject`)[0].body).toEqual({ row_version: 2, comment: "Amount does not match the advice." });
  });

  it("sends only once even if confirmed repeatedly", async () => {
    const pending = deferred();
    const { api } = await openReview("AICTE_APPROVER", submittedFdr(), {
      [`POST /fdrs/${FDR_ID}/reject`]: async (req) => { await pending.promise; return jsonReply(200, submittedFdr({ status: "REJECTED", row_version: 3 }))(req) as Response; },
    });
    await startAction("Reject");
    await userEvent.type(commentBox(), "Reason");
    await userEvent.click(button("Confirm rejection"));
    const busy = await screen.findByRole("button", { name: "Recording…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/reject`)).toHaveLength(1);
    pending.resolve();
    await screen.findByRole("heading", { name: "Review queue" });
  });
});

describe("failures", () => {
  it("explains a stale version and reloads the latest copy on request", async () => {
    const { store } = await openReview("AICTE_APPROVER");
    store.fdr = submittedFdr({ row_version: 7, purpose: "Edited elsewhere" }); // someone else changed it
    await startAction("Accept");
    await userEvent.click(button("Confirm acceptance"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/changed since you loaded/i);
    await userEvent.click(screen.getByRole("button", { name: "Reload the latest version" }));
    expect(await screen.findByText("Version 7")).toBeInTheDocument();
    expect(await screen.findByText("Edited elsewhere")).toBeInTheDocument();
  });

  it("shows that another reviewer already decided, and offers a reload", async () => {
    const { store } = await openReview("AICTE_APPROVER");
    store.fdr = submittedFdr({ status: "ACCEPTED", row_version: 3 });
    await startAction("Reject");
    await userEvent.type(commentBox(), "Too late");
    await userEvent.click(button("Confirm rejection"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not awaiting review/i);
    await userEvent.click(screen.getByRole("button", { name: "Reload the latest version" }));
    expect(await screen.findByText(/no further review action is available/i)).toBeInTheDocument();
  });

  it.each([
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted/i],
  ])("reports %s and stays on the page", async (_label, failing, message) => {
    await openReview("AICTE_APPROVER", submittedFdr(), { [`POST /fdrs/${FDR_ID}/accept`]: failing });
    await startAction("Accept");
    await userEvent.click(button("Confirm acceptance"));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("heading", { name: "Review FDR" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Review queue" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm acceptance" })).toBeEnabled();
  });

  it("does not leak server internals", async () => {
    await openReview("AICTE_APPROVER", submittedFdr(), {
      [`POST /fdrs/${FDR_ID}/accept`]: jsonReply(500, { detail: "Traceback: psycopg.OperationalError secret" }),
    });
    await startAction("Accept");
    await userEvent.click(button("Confirm acceptance"));
    expect((await screen.findByRole("alert")).textContent).not.toMatch(/traceback|psycopg|secret/i);
  });

  it("returns to sign-in if the session has expired", async () => {
    await openReview("AICTE_APPROVER", submittedFdr(), { [`POST /fdrs/${FDR_ID}/accept`]: jsonReply(401, { detail: "Not authenticated" }) });
    await startAction("Accept");
    await userEvent.click(button("Confirm acceptance"));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/session has expired/i);
  });

  it("says plainly when the FDR does not exist", async () => {
    reviewBackend(null);
    renderApp(`/review/${FDR_ID}`);
    expect(await screen.findByRole("alert")).toHaveTextContent("This FDR was not found.");
  });

  it("offers a retry when the FDR cannot be loaded", async () => {
    let calls = 0;
    reviewBackend(submittedFdr(), { extra: { [`GET /fdrs/${FDR_ID}`]: (req) => (calls++ === 0 ? textReply(500)(req) : jsonReply(200, submittedFdr())(req)) } });
    renderApp(`/review/${FDR_ID}`);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load this fdr/i);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Submitted details")).toBeInTheDocument();
  });
});
