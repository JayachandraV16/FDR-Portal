import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { latestCorrection } from "../fdr/HistoryPanel";
import { formatDateTime } from "../lib/format";
import { jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { COMMENT, FDR_ID, entry, reviewBackend, submittedFdr, submittedHistory } from "../test/reviewBackend";
import { renderApp } from "../test/render";

const returnedHistory = () => [
  ...submittedHistory(),
  entry("CORRECTION_REQUESTED", "SUBMITTED", "AICTE_REVIEWER", COMMENT, "2026-10-27T10:30:00Z"),
];
const returnedFdr = () => submittedFdr({ status: "CORRECTION_REQUESTED", row_version: 3 });

async function openReturned(extra: Record<string, Handler | Handler[]> = {}) {
  const backend = reviewBackend(returnedFdr(), { role: "INSTITUTION_USER", history: returnedHistory(), extra });
  renderApp(`/fdrs/${FDR_ID}/edit`);
  await screen.findByDisplayValue("RV-FDR-1");
  return backend;
}

describe("an institution user whose FDR was returned", () => {
  it("sees the reviewer's comment, who asked and when", async () => {
    await openReturned();
    const notice = await screen.findByRole("region", { name: "Corrections requested" });
    expect(await within(notice).findByText(COMMENT)).toBeInTheDocument();
    expect(within(notice).getByText("AICTE reviewer · 27 Oct 2026, 10:30 UTC")).toBeInTheDocument();
    expect(screen.getByText(/returned by aicte for correction/i)).toBeInTheDocument();
  });

  it("shows only the latest request when there have been several", async () => {
    reviewBackend(returnedFdr(), {
      role: "INSTITUTION_USER",
      history: [
        ...returnedHistory(),
        entry("SUBMITTED", "CORRECTION_REQUESTED", "INSTITUTION_USER", null, "2026-10-28T08:00:00Z"),
        entry("CORRECTION_REQUESTED", "SUBMITTED", "AICTE_APPROVER", "Second round: attach the advice.", "2026-10-29T08:00:00Z"),
      ],
    });
    renderApp(`/fdrs/${FDR_ID}/edit`);
    expect(await screen.findByText("Second round: attach the advice.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Corrections requested" })).queryByText(COMMENT)).not.toBeInTheDocument();
  });

  it("is offered 'Make corrections' in the registry, and no edit link for a record under review", async () => {
    reviewBackend(returnedFdr(), { role: "INSTITUTION_USER", history: returnedHistory() });
    renderApp("/fdrs");
    const row = (await screen.findByText("RV-FDR-1")).closest("tr")!;
    expect(within(row).getByRole("link", { name: "Make corrections" })).toHaveAttribute("href", `/fdrs/${FDR_ID}/edit`);
    expect(within(row).getByText("Correction requested")).toBeInTheDocument();
  });

  it("can edit and save the returned record", async () => {
    const { api, store } = await openReturned();
    await userEvent.clear(screen.getByLabelText("Purpose"));
    await userEvent.type(screen.getByLabelText("Purpose"), "Corrected purpose");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(api.callsTo(`PATCH /fdrs/${FDR_ID}`)[0].body).toMatchObject({ row_version: 3, purpose: "Corrected purpose" });
    expect(store.fdr?.status).toBe("CORRECTION_REQUESTED"); // still returned until resubmitted
  });

  it("resubmits after confirming, and is told the earlier submission is kept", async () => {
    const { api, store } = await openReturned();
    expect(screen.queryByRole("button", { name: "Submit for review" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Resubmit for review" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/returns to the aicte review queue/i);
    expect(dialog).toHaveTextContent(/earlier submission is kept/i);
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/submit`)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm resubmission" }));

    expect(await screen.findByRole("heading", { name: "FDR Registry" })).toBeInTheDocument();
    expect(screen.getByText("RV-FDR-1 was resubmitted for review. It can no longer be edited.")).toBeInTheDocument();
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/submit`)[0].body).toEqual({ row_version: 3 });
    expect(store.fdr?.status).toBe("SUBMITTED");
    const row = (await screen.findByText("RV-FDR-1")).closest("tr")!;
    expect(within(row).getByText("Submitted")).toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: /make corrections|edit draft/i })).not.toBeInTheDocument();
  });

  it("must save edits before resubmitting", async () => {
    await openReturned();
    await userEvent.type(screen.getByLabelText("Purpose"), " more");
    expect(screen.getByRole("button", { name: "Resubmit for review" })).toBeDisabled();
  });

  it("keeps the form usable when the reviewer's comment cannot be loaded, and can retry", async () => {
    let calls = 0;
    await openReturned({
      [`GET /fdrs/${FDR_ID}/history`]: (req) =>
        calls++ === 0 ? textReply(500)(req) : jsonReply(200, { history: returnedHistory(), revisions: [] })(req),
    });
    const notice = screen.getByRole("region", { name: "Corrections requested" });
    expect(await within(notice).findByRole("alert")).toHaveTextContent(/could not load the reviewer's comment/i);
    expect(screen.getByLabelText("Purpose")).toBeEnabled();
    await userEvent.click(within(notice).getByRole("button", { name: "Retry" }));
    expect(await within(notice).findByText(COMMENT)).toBeInTheDocument();
  });

  it.each([
    ["a conflict", jsonReply(409, { detail: "Only draft FDRs, or FDRs returned for correction, can be submitted" }), /can be submitted/i],
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
  ])("reports %s on resubmission and stays on the page", async (_label, failing, message) => {
    await openReturned({ [`POST /fdrs/${FDR_ID}/submit`]: failing });
    await userEvent.click(screen.getByRole("button", { name: "Resubmit for review" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm resubmission" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("heading", { name: "Edit FDR" })).toBeInTheDocument();
  });

  it("does not ask for a reviewer comment on an ordinary draft", async () => {
    const { api } = reviewBackend(submittedFdr({ status: "DRAFT", row_version: 1 }), { role: "INSTITUTION_USER" });
    renderApp(`/fdrs/${FDR_ID}/edit`);
    await screen.findByDisplayValue("RV-FDR-1");
    expect(screen.queryByRole("region", { name: "Corrections requested" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeInTheDocument();
    expect(api.callsTo(`GET /fdrs/${FDR_ID}/history`)).toHaveLength(0);
  });

  it("cannot edit a record that is under review or decided", async () => {
    for (const status of ["SUBMITTED", "ACCEPTED", "REJECTED"]) {
      reviewBackend(submittedFdr({ status }), { role: "INSTITUTION_USER" });
      const { unmount } = renderApp(`/fdrs/${FDR_ID}/edit`);
      expect(await screen.findByText(/only drafts and fdrs returned for correction can be edited/i)).toBeInTheDocument();
      expect(screen.queryByLabelText("FDR number")).not.toBeInTheDocument();
      unmount();
    }
  });
});

describe("the whole round trip, across three people", () => {
  it("submitted -> reviewer returns it -> institution corrects and resubmits -> approver accepts", async () => {
    const { api, store } = reviewBackend(submittedFdr(), { role: "AICTE_REVIEWER" });

    // 1. The reviewer sends it back with a reason.
    let view = renderApp(`/review/${FDR_ID}`);
    await screen.findByText("Submitted details");
    await userEvent.click(screen.getByRole("button", { name: "Request corrections" }));
    await userEvent.type(screen.getByLabelText(/corrected/i), COMMENT);
    await userEvent.click(screen.getByRole("button", { name: "Send back to the institution" }));
    await screen.findByText(/corrections requested for RV-FDR-1/i);
    expect(store.fdr?.status).toBe("CORRECTION_REQUESTED");
    view.unmount();

    // 2. The institution reads the reason, corrects, and resubmits.
    store.role = "INSTITUTION_USER";
    view = renderApp(`/fdrs/${FDR_ID}/edit`);
    expect(await screen.findByText(COMMENT)).toBeInTheDocument();
    await screen.findByDisplayValue("RV-FDR-1");
    await userEvent.clear(screen.getByLabelText("Purpose"));
    await userEvent.type(screen.getByLabelText("Purpose"), "Corrected purpose");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Changes saved.");
    await userEvent.click(screen.getByRole("button", { name: "Resubmit for review" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm resubmission" }));
    await screen.findByText(/was resubmitted for review/);
    expect(store.fdr).toMatchObject({ status: "SUBMITTED", purpose: "Corrected purpose" });
    view.unmount();

    // 3. An approver finds it back in the queue and accepts it.
    store.role = "AICTE_APPROVER";
    view = renderApp("/review");
    await userEvent.click(await screen.findByRole("link", { name: "Review RV-FDR-1" }));
    await screen.findByText("Submitted details");
    expect(await screen.findByText(COMMENT)).toBeInTheDocument(); // the earlier request is still in the history
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm acceptance" }));
    await screen.findByText("RV-FDR-1 was accepted.");
    expect(store.fdr?.status).toBe("ACCEPTED");
    expect(store.history.map((h) => h.to_status)).toEqual(["DRAFT", "SUBMITTED", "CORRECTION_REQUESTED", "SUBMITTED", "ACCEPTED"]);
    expect(api.callsTo(`POST /fdrs/${FDR_ID}/accept`)).toHaveLength(1);
    view.unmount();
  });
});

describe("helpers", () => {
  it("finds the most recent correction request, or none", () => {
    const history = [
      entry("CORRECTION_REQUESTED", "SUBMITTED", "AICTE_REVIEWER", "first"),
      entry("SUBMITTED", "CORRECTION_REQUESTED", "INSTITUTION_USER"),
      entry("CORRECTION_REQUESTED", "SUBMITTED", "AICTE_APPROVER", "second"),
    ];
    expect(latestCorrection(history)?.comment).toBe("second");
    expect(latestCorrection(submittedHistory())).toBeNull();
    expect(latestCorrection([])).toBeNull();
  });

  it("formats date-times in UTC without depending on the viewer's timezone", () => {
    expect(formatDateTime("2026-10-27T09:05:00Z")).toBe("27 Oct 2026, 09:05 UTC");
    expect(formatDateTime("2026-01-05T23:59:00+00:00")).toBe("5 Jan 2026, 23:59 UTC");
    expect(formatDateTime("not a date")).toBe("not a date");
  });
});
