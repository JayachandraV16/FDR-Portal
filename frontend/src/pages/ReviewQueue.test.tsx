import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { DocumentCheckSummary, FdrOut } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeSummary, makeUser } from "../test/fixtures";
import { BANKS, INSTITUTION } from "../test/reviewBackend";
import { renderApp } from "../test/render";

type QueueFdr = FdrOut & { document_check: DocumentCheckSummary };

const fdr = (i: number, overrides: Partial<QueueFdr> = {}): QueueFdr => ({
  ...makeFdr({
    id: `f0000000-0000-4000-8000-${String(i).padStart(12, "0")}`, fdr_number: `Q-${String(i).padStart(3, "0")}`,
    status: "SUBMITTED", institution: INSTITUTION, bank: BANKS[0], submitted_at: "2026-10-26T09:00:00Z",
  }),
  document_check: makeSummary(),
  ...overrides,
});

/** GET /review/queue with the backend's semantics: SUBMITTED only, filters narrow, `total` is the whole set. */
function queueEndpoint(all: QueueFdr[]): Handler {
  return (req) => {
    const { q, bank_id, institution_id } = req.query;
    const size = Number(req.query.page_size ?? 25);
    const page = Number(req.query.page ?? 1);
    let rows = all;
    if (q) rows = rows.filter((r) => r.fdr_number.replace(/\s+/g, "").toUpperCase().startsWith(q.replace(/\s+/g, "").toUpperCase()));
    if (bank_id) rows = rows.filter((r) => r.bank.id === bank_id);
    if (institution_id) rows = rows.filter((r) => r.institution.id === institution_id);
    return jsonReply(200, { items: rows.slice((page - 1) * size, page * size), page, page_size: size, total: rows.length, total_pages: Math.ceil(rows.length / size) })(req);
  };
}

async function openQueue(role = "AICTE_REVIEWER", all: QueueFdr[] = [fdr(1), fdr(2)], extra: Record<string, Handler | Handler[]> = {}) {
  const api = installFakeApi(
    {
      "GET /auth/me": jsonReply(200, makeSession(makeUser({ role }))),
      "GET /review/queue": queueEndpoint(all),
      "GET /banks": jsonReply(200, { items: BANKS, page: 1, page_size: 500, total: 2, total_pages: 1 }),
      "GET /institutions": jsonReply(200, { items: [INSTITUTION], total: 1, limit: 200, offset: 0 }),
      ...extra,
    },
    { dashboardDefaults: false },
  );
  renderApp("/review");
  return api;
}
const lastQuery = (api: Awaited<ReturnType<typeof openQueue>>) => api.callsTo("GET /review/queue").at(-1)!.query;

describe("access", () => {
  it.each(["AICTE_REVIEWER", "AICTE_APPROVER"])("%s sees the queue", async (role) => {
    await openQueue(role);
    expect(await screen.findByRole("heading", { name: "Review queue", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText("Q-001")).toBeInTheDocument();
  });

  it.each(["INSTITUTION_USER", "ADMIN", "AUDITOR"])("%s gets an explanation and the queue is never requested", async (role) => {
    const api = await openQueue(role);
    expect(await screen.findByRole("alert")).toHaveTextContent(/only institution users can do this/i);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(api.callsTo("GET /review/queue")).toHaveLength(0);
  });

  it("is behind sign-in", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }) }, { dashboardDefaults: false });
    renderApp("/review");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows a sidebar link to reviewers and approvers only", async () => {
    for (const [role, visible] of [["AICTE_REVIEWER", true], ["AICTE_APPROVER", true], ["ADMIN", false], ["AUDITOR", false], ["INSTITUTION_USER", false]] as const) {
      const api = installFakeApi(
        {
          "GET /auth/me": jsonReply(200, makeSession(makeUser({ role, institution_id: role === "INSTITUTION_USER" ? INSTITUTION.id : null }))),
          "GET /review/queue": queueEndpoint([]), "GET /banks": jsonReply(200, { items: [], page: 1, page_size: 500, total: 0, total_pages: 0 }),
          "GET /institutions": jsonReply(200, { items: [], total: 0, limit: 200, offset: 0 }),
          "GET /fdrs": jsonReply(200, { items: [], page: 1, page_size: 25, total: 0, total_pages: 0 }),
        },
        { dashboardDefaults: false },
      );
      const { unmount } = renderApp("/fdrs");
      const nav = await screen.findByRole("navigation", { name: "Main" });
      expect(Boolean(within(nav).queryByRole("link", { name: "Review queue" })), role).toBe(visible);
      unmount();
      expect(api).toBeDefined();
    }
  });
});

describe("queue contents", () => {
  it("lists awaiting FDRs with the fields needed to choose one, and a Review link for each", async () => {
    await openQueue("AICTE_REVIEWER", [
      fdr(1, { fdr_number: "BOM-9", principal_amount: "12345678.90", maturity_date: "2027-03-31", submitted_at: "2026-10-26T09:00:00Z",
        institution: { id: "i", code: "COEP", name: "COEP Technological University" }, bank: BANKS[1] }),
    ]);
    const row = within((await screen.findByText("BOM-9")).closest("tr")!);
    expect(row.getByText("COEP Technological University")).toBeInTheDocument();
    expect(row.getByText("State Bank of India")).toBeInTheDocument();
    expect(row.getByText("₹1,23,45,678.90")).toBeInTheDocument();
    expect(row.getByText("31 Mar 2027")).toBeInTheDocument();
    expect(row.getByText("26 Oct 2026")).toBeInTheDocument();
    expect(row.getByRole("link", { name: "Review BOM-9" })).toHaveAttribute("href", "/review/f0000000-0000-4000-8000-000000000001");
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
      ["FDR number", "Institution", "Bank", "Principal", "Matures", "Submitted", "Document check", "Action"],
    );
  });

  it("states the backend total and reminds reviewers nothing is bank-verified", async () => {
    await openQueue("AICTE_REVIEWER", Array.from({ length: 60 }, (_, i) => fdr(i + 1)));
    expect(await screen.findByTestId("queue-count")).toHaveTextContent("60 FDRs awaiting review");
    expect(screen.getAllByRole("row")).toHaveLength(26);
    expect(screen.getByText(/none has been verified with a bank/i)).toBeInTheDocument();
  });

  it("shows an honest empty state", async () => {
    await openQueue("AICTE_REVIEWER", []);
    expect(await screen.findByText("No FDRs are awaiting review.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(await screen.findByTestId("queue-count")).toHaveTextContent("0 FDRs awaiting review");
  });
});

describe("search, filters and paging", () => {
  it("searches by number prefix (debounced), filters by bank and institution, and returns to page 1", async () => {
    const api = await openQueue("AICTE_REVIEWER", Array.from({ length: 60 }, (_, i) => fdr(i + 1, i % 2 ? { bank: BANKS[1] } : {})));
    await screen.findByText("Q-001");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastQuery(api).page).toBe("2"));

    const before = api.callsTo("GET /review/queue").length;
    await userEvent.type(screen.getByRole("searchbox", { name: "FDR number" }), "q-00");
    await waitFor(() => expect(lastQuery(api)).toMatchObject({ q: "q-00", page: "1" }));
    expect(api.callsTo("GET /review/queue")).toHaveLength(before + 1);

    await waitFor(() => expect(within(screen.getByLabelText("Bank")).getAllByRole("option")).toHaveLength(3));
    await userEvent.selectOptions(screen.getByLabelText("Bank"), "State Bank of India");
    await waitFor(() => expect(lastQuery(api).bank_id).toBe(BANKS[1].id));
    await waitFor(() => expect(within(screen.getByLabelText("Institution")).getAllByRole("option")).toHaveLength(2));
    await userEvent.selectOptions(screen.getByLabelText("Institution"), "PICT — PICT Test Institute");
    await waitFor(() => expect(lastQuery(api).institution_id).toBe(INSTITUTION.id));
    expect(lastQuery(api)).not.toHaveProperty("status"); // the queue is SUBMITTED by definition
  });

  it("explains an empty result for filters differently from an empty queue, and clears", async () => {
    await openQueue();
    await screen.findByText("Q-001");
    await userEvent.type(screen.getByRole("searchbox", { name: "FDR number" }), "ZZZ");
    expect(await screen.findByText("No queued FDRs match the current search and filters.")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Clear search and filters" })[0]);
    expect(await screen.findByText("Q-001")).toBeInTheDocument();
  });

  it("pages with the backend's totals", async () => {
    const api = await openQueue("AICTE_REVIEWER", Array.from({ length: 60 }, (_, i) => fdr(i + 1)));
    expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–25 of 60")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Q-026")).toBeInTheDocument();
    expect(lastQuery(api)).toMatchObject({ page: "2", page_size: "25" });
    expect(screen.getByText("Showing 26–50 of 60")).toBeInTheDocument();
  });
});

describe("document check summary", () => {
  const cellFor = async (n: number) => (await screen.findByText(`Q-00${n}`)).closest("tr")!.querySelector("[data-status]") as HTMLElement;

  it("shows 'Not analyzed' as a neutral state, not a mismatch, and says what the summary is", async () => {
    await openQueue("AICTE_REVIEWER", [fdr(1, { document_check: makeSummary({ documents_total: 2 }) })]);
    const cell = await cellFor(1);
    expect(cell).toHaveAttribute("data-status", "NOT_ANALYZED");
    expect(within(cell).getByText("Not analyzed")).toBeInTheDocument();
    expect(within(cell).getByText("2 documents uploaded")).toBeInTheDocument();
    expect(cell).toHaveAttribute("data-mismatches", "0");
    expect(within(cell).queryByText(/mismatch/i)).not.toBeInTheDocument();
    expect(screen.getByText(/does not show that a document is genuine or fraudulent/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing here starts a check/i)).toBeInTheDocument();
  });

  it("states the number of real mismatches and names the fields, separately from the other outcomes", async () => {
    await openQueue("AICTE_REVIEWER", [fdr(1, { document_check: makeSummary({
      status: "ANALYZED", documents_analyzed: 1, mismatch_count: 2, mismatch_fields: ["fdr_number", "principal_amount"],
      ambiguous_count: 1, not_found_count: 3, not_comparable_count: 1, match_count: 2,
    }) })]);
    const cell = within(await cellFor(1));
    expect(cell.getByText(/2 mismatches/)).toBeInTheDocument();
    expect(cell.getByText("FDR number, principal amount")).toBeInTheDocument();
    expect(cell.getByText("1 ambiguous · 3 not found · 1 not comparable")).toBeInTheDocument();
  });

  it("uses the singular for one mismatch and says 'No mismatches found' (not 'verified') when there are none", async () => {
    await openQueue("AICTE_REVIEWER", [
      fdr(1, { document_check: makeSummary({ status: "ANALYZED", documents_analyzed: 1, mismatch_count: 1, mismatch_fields: ["bank_name"], match_count: 8 }) }),
      fdr(2, { document_check: makeSummary({ status: "ANALYZED", documents_analyzed: 1, match_count: 9 }) }),
    ]);
    expect(within(await cellFor(1)).getByText(/1 mismatch$/)).toBeInTheDocument();
    const clean = within(await cellFor(2));
    expect(clean.getByText("No mismatches found")).toBeInTheDocument();
    expect(clean.queryByText(/verified|genuine|authentic/i)).not.toBeInTheDocument();
  });

  it.each([
    [{ status: "NO_DOCUMENTS", documents_total: 0 }, "No documents"],
    [{ status: "PENDING", documents_pending: 1 }, "Check in progress"],
    [{ status: "FAILED", documents_failed: 1 }, "Check failed"],
    [{ status: "UNAVAILABLE", documents_total: 0 }, "Summary unavailable"],
  ] as const)("shows %j as '%s'", async (overrides, text) => {
    await openQueue("AICTE_REVIEWER", [fdr(1, { document_check: makeSummary({ ...overrides }) })]);
    const cell = await cellFor(1);
    expect(within(cell).getByText(text)).toBeInTheDocument();
    expect(within(cell).queryByText(/mismatch/i)).not.toBeInTheDocument();
  });

  it("notes when only some of an FDR's documents were analysed", async () => {
    await openQueue("AICTE_REVIEWER", [fdr(1, { document_check: makeSummary({
      status: "ANALYZED", documents_total: 3, documents_analyzed: 1, documents_failed: 1, match_count: 9,
    }) })]);
    expect(within(await cellFor(1)).getByText("1 of 3 documents analyzed, 1 failed")).toBeInTheDocument();
  });

  it("never asks the server to analyse anything: the queue is read-only", async () => {
    const api = await openQueue("AICTE_REVIEWER", [fdr(1), fdr(2)]);
    await screen.findByText("Q-001");
    expect(api.calls.filter((c) => c.method !== "GET")).toEqual([]);
    expect(api.calls.some((c) => c.path.includes("extraction"))).toBe(false);
  });

  it("still shows the queue's own loading, empty and error states", async () => {
    await openQueue("AICTE_REVIEWER", []);
    expect(await screen.findByText("No FDRs are awaiting review.")).toBeInTheDocument();
    expect(screen.queryByText("Document check")).not.toBeInTheDocument(); // no table, no column
  });
});

describe("loading, errors and sessions", () => {
  it("shows a loading state", async () => {
    const pending = deferred();
    await openQueue("AICTE_REVIEWER", [], {
      "GET /review/queue": async (req) => { await pending.promise; return queueEndpoint([fdr(1)])(req) as Response; },
    });
    expect(await screen.findByText("Loading the review queue…")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-count")).not.toBeInTheDocument();
    pending.resolve();
    expect(await screen.findByText("Q-001")).toBeInTheDocument();
  });

  it.each([
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted for your role/i],
  ])("shows %s as an error, never as an empty queue, and recovers on retry", async (_label, failing, message) => {
    let calls = 0;
    await openQueue("AICTE_REVIEWER", [], {
      "GET /review/queue": (req) => (calls++ === 0 ? failing(req) : queueEndpoint([fdr(1)])(req)),
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText(/no fdrs are awaiting review/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("queue-count")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Q-001")).toBeInTheDocument();
  });

  it("returns to sign-in with a notice when the session has expired", async () => {
    await openQueue("AICTE_REVIEWER", [], { "GET /review/queue": jsonReply(401, { detail: "Not authenticated" }) });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/session has expired/i);
  });
});
