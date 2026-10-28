import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { FdrOut, InstitutionOut } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const BANKS = [
  { id: "b0000000-0000-4000-8000-000000000001", code: "BOM", name: "Bank of Maharashtra" },
  { id: "b0000000-0000-4000-8000-000000000002", code: "SBI", name: "State Bank of India" },
];
const INSTITUTIONS: InstitutionOut[] = ["PICT", "COEP"].map((code, i) => ({
  id: `a0000000-0000-4000-8000-00000000000${i + 1}`, code, name: `${code} Test Institute`, state: "Maharashtra",
  institution_type: "Engineering", contact_email: null, status: "active", created_at: "2026-10-26T00:00:00Z",
}));

const fdr = (i: number, overrides: Partial<FdrOut> = {}): FdrOut =>
  makeFdr({
    id: `f0000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
    fdr_number: `FDR-${String(i).padStart(4, "0")}`,
    institution: { id: INSTITUTIONS[0].id, code: INSTITUTIONS[0].code, name: INSTITUTIONS[0].name },
    bank: BANKS[0],
    ...overrides,
  });
const many = (n: number) => Array.from({ length: n }, (_, k) => fdr(k + 1));

/** GET /fdrs with the backend's semantics: filters narrow the visible set, `q` is a prefix match on
 * the whitespace-stripped, upper-cased number, and `total` counts the whole filtered set. */
function fdrEndpoint(visible: FdrOut[], { institutionUser = false }: { institutionUser?: boolean } = {}): Handler {
  return (req) => {
    const { status, bank_id, institution_id, q } = req.query;
    const pageSize = Number(req.query.page_size ?? 25);
    const page = Number(req.query.page ?? 1);
    let rows = visible;
    if (status) rows = rows.filter((r) => r.status === status);
    if (bank_id) rows = rows.filter((r) => r.bank.id === bank_id);
    if (institution_id && !institutionUser) rows = rows.filter((r) => r.institution.id === institution_id);
    if (q) rows = rows.filter((r) => r.fdr_number.replace(/\s+/g, "").toUpperCase().startsWith(q.replace(/\s+/g, "").toUpperCase()));
    return jsonReply(200, {
      items: rows.slice((page - 1) * pageSize, page * pageSize), page, page_size: pageSize,
      total: rows.length, total_pages: Math.ceil(rows.length / pageSize),
    })(req);
  };
}

const banksEndpoint = jsonReply(200, { items: BANKS, page: 1, page_size: 500, total: BANKS.length, total_pages: 1 });
const institutionsEndpoint = jsonReply(200, { items: INSTITUTIONS, total: INSTITUTIONS.length, limit: 200, offset: 0 });

async function openRegistry({
  role = "AICTE_REVIEWER", visible = many(3), routes = {}, path = "/fdrs",
}: { role?: string; visible?: FdrOut[]; routes?: Parameters<typeof installFakeApi>[0]; path?: string } = {}) {
  const user = makeUser({ role, institution_id: role === "INSTITUTION_USER" ? INSTITUTIONS[0].id : null });
  const api = installFakeApi(
    {
      "GET /auth/me": jsonReply(200, makeSession(user)),
      "GET /fdrs": fdrEndpoint(visible, { institutionUser: role === "INSTITUTION_USER" }),
      "GET /banks": banksEndpoint,
      "GET /institutions": institutionsEndpoint,
      ...routes,
    },
    { dashboardDefaults: false },
  );
  renderApp(path);
  return api;
}

const lastFdrQuery = (api: Awaited<ReturnType<typeof openRegistry>>) => api.callsTo("GET /fdrs").at(-1)!.query;
const count = () => screen.findByTestId("fdr-count");
const search = () => screen.getByRole("searchbox", { name: "FDR number" });
const status = () => screen.getByLabelText("Status");
const bank = () => screen.getByLabelText("Bank");
const institution = () => screen.getByLabelText("Institution");
const waitForRows = (text: string) => screen.findByText(text);

describe("loaded registry", () => {
  it("shows every field the API provides, formatted exactly", async () => {
    await openRegistry({
      visible: [
        fdr(1, {
          fdr_number: "BOM-2026-77", principal_amount: "12345678.90", interest_rate_pct: "7.10", issue_date: "2026-01-15",
          maturity_date: "2027-03-31", status: "CORRECTION_REQUESTED", created_at: "2026-10-01T10:00:00Z",
          institution: { id: INSTITUTIONS[1].id, code: "COEP", name: "COEP Technological University" }, bank: BANKS[1],
        }),
      ],
    });
    const row = within((await waitForRows("BOM-2026-77")).closest("tr")!);
    expect(row.getByText("COEP Technological University")).toBeInTheDocument();
    expect(row.getByText("COEP")).toBeInTheDocument();
    expect(row.getByText("State Bank of India")).toBeInTheDocument();
    expect(row.getByText("₹1,23,45,678.90")).toBeInTheDocument();
    expect(row.getByText("7.10%")).toBeInTheDocument();
    expect(row.getByText("15 Jan 2026")).toBeInTheDocument();
    expect(row.getByText("31 Mar 2027")).toBeInTheDocument();
    expect(row.getByText("Correction requested")).toBeInTheDocument();
    expect(row.getByText("1 Oct 2026")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "FDR number", "Institution", "Bank", "Principal", "Interest rate", "Issued", "Matures", "Status", "Created",
    ]);
  });

  it("states the backend total, not the number of rows", async () => {
    await openRegistry({ visible: many(60) });
    expect(await count()).toHaveTextContent("60 FDRs visible to you");
    expect(screen.getAllByRole("row")).toHaveLength(26);
  });

  it("explains, for cross-institution roles, that drafts and withdrawn records are not visible", async () => {
    await openRegistry();
    expect(await count()).toHaveTextContent(/drafts and withdrawn records are not visible to your role/i);
  });

  it("shows the demonstration notice and the read-only description", async () => {
    await openRegistry();
    expect(await screen.findByRole("heading", { name: "FDR Registry", level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/fixed deposit receipts visible to your account/i)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/demonstration environment/i);
  });
});

describe("optional and missing fields", () => {
  it("shows 'Not provided' for a null interest rate and invents nothing", async () => {
    await openRegistry({ visible: [fdr(1, { interest_rate_pct: null })] });
    const row = within((await waitForRows("FDR-0001")).closest("tr")!);
    expect(row.getByText("Not provided")).toBeInTheDocument();
    expect(row.queryByText(/%/)).not.toBeInTheDocument();
  });

  it.each([
    ["0.50", "₹0.50"],
    ["999.00", "₹999.00"],
    ["100000.00", "₹1,00,000.00"],
    ["99999999999999.99", "₹9,99,99,99,99,99,999.99"],
  ])("formats principal %s as %s without floating-point rounding", async (amount, expected) => {
    await openRegistry({ visible: [fdr(1, { principal_amount: amount })] });
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });
});

describe("empty and no-match states", () => {
  it("shows an honest empty state when no FDRs exist", async () => {
    await openRegistry({ visible: [] });
    expect(await screen.findByText("No FDR records are available to you yet.")).toBeInTheDocument();
    expect(await count()).toHaveTextContent("0 FDRs visible to you");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /clear search and filters/i })).not.toBeInTheDocument();
  });

  it("distinguishes 'nothing matches' from 'nothing exists', and clearing restores the list", async () => {
    await openRegistry();
    await waitForRows("FDR-0001");
    await userEvent.type(search(), "ZZZ");
    expect(await screen.findByText("No FDRs match the current search and filters.")).toBeInTheDocument();
    expect(screen.queryByText("No FDR records are available to you yet.")).not.toBeInTheDocument();
    expect(await count()).toHaveTextContent("0 FDRs match the current filters");
    await userEvent.click(screen.getAllByRole("button", { name: "Clear search and filters" })[0]);
    expect(await waitForRows("FDR-0001")).toBeInTheDocument();
    expect(search()).toHaveValue("");
  });
});

describe("search by FDR-number prefix", () => {
  it("debounces typing into a single request and searches only by prefix", async () => {
    const api = await openRegistry({ visible: [fdr(1, { fdr_number: "BOM-2026-1" }), fdr(2, { fdr_number: "SBI-2026-1" })] });
    await waitForRows("BOM-2026-1");
    const before = api.callsTo("GET /fdrs").length;
    await userEvent.type(search(), "bom-2026");
    await waitFor(() => expect(lastFdrQuery(api).q).toBe("bom-2026"));
    expect(api.callsTo("GET /fdrs")).toHaveLength(before + 1); // one request, not one per keystroke
    expect(await screen.findByText("BOM-2026-1")).toBeInTheDocument();
    expect(screen.queryByText("SBI-2026-1")).not.toBeInTheDocument();
    expect(screen.getByText(/matches the start of the fdr number\. spaces and letter case are ignored\. no other field is searched/i)).toBeInTheDocument();
  });

  it("does not treat a mid-number fragment as a match", async () => {
    await openRegistry({ visible: [fdr(1, { fdr_number: "BOM-2026-1" })] });
    await waitForRows("BOM-2026-1");
    await userEvent.type(search(), "2026"); // present in the number, but not at the start
    expect(await screen.findByText("No FDRs match the current search and filters.")).toBeInTheDocument();
  });

  it("clears the search, both by the button and by emptying the box, and sends no empty q", async () => {
    const api = await openRegistry();
    await waitForRows("FDR-0001");
    await userEvent.type(search(), "FDR-0002");
    await waitFor(() => expect(lastFdrQuery(api).q).toBe("FDR-0002"));
    await userEvent.clear(search());
    await waitFor(() => expect(lastFdrQuery(api)).not.toHaveProperty("q"));
    expect(await screen.findByText("FDR-0001")).toBeInTheDocument();

    await userEvent.type(search(), "FDR-0003");
    await waitFor(() => expect(lastFdrQuery(api).q).toBe("FDR-0003"));
    await userEvent.click(screen.getByRole("button", { name: "Clear search and filters" }));
    await waitFor(() => expect(lastFdrQuery(api)).not.toHaveProperty("q"));
    expect(search()).toHaveValue("");
  });

  it("sends the search term trimmed of surrounding spaces", async () => {
    const api = await openRegistry({ visible: [fdr(1, { fdr_number: "BOM-2026-1" })] });
    await waitForRows("BOM-2026-1");
    await userEvent.type(search(), "  BOM-2026  ");
    await waitFor(() => expect(lastFdrQuery(api).q).toBe("BOM-2026"));
    expect(search()).toHaveValue("  BOM-2026  "); // the box itself is left as typed
  });

  it("never sends a whitespace-only search (the backend would answer 422)", async () => {
    const api = await openRegistry();
    await waitForRows("FDR-0001");
    const before = api.callsTo("GET /fdrs").length;
    await userEvent.type(search(), "   ");
    await new Promise((r) => setTimeout(r, 450));
    expect(api.callsTo("GET /fdrs")).toHaveLength(before);
    expect(api.calls.every((c) => c.query.q === undefined || c.query.q.trim() !== "")).toBe(true);
  });

  it("returns to page 1 when the search changes", async () => {
    const api = await openRegistry({ visible: many(60) });
    await waitForRows("FDR-0001");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastFdrQuery(api).page).toBe("2"));
    await userEvent.type(search(), "FDR-00");
    await waitFor(() => expect(lastFdrQuery(api)).toMatchObject({ q: "FDR-00", page: "1" }));
  });
});

describe("filters", () => {
  const sample = () => [
    fdr(1, { status: "SUBMITTED", bank: BANKS[0] }),
    fdr(2, { status: "ACCEPTED", bank: BANKS[1] }),
    fdr(3, { status: "ACCEPTED", bank: BANKS[0], institution: { id: INSTITUTIONS[1].id, code: "COEP", name: INSTITUTIONS[1].name } }),
  ];

  it("filters by status using the backend's status values", async () => {
    const api = await openRegistry({ visible: sample() });
    await waitForRows("FDR-0001");
    await userEvent.selectOptions(status(), "Accepted");
    await waitFor(() => expect(lastFdrQuery(api).status).toBe("ACCEPTED"));
    expect(await count()).toHaveTextContent("2 FDRs match the current filters");
    expect(screen.queryByText("FDR-0001")).not.toBeInTheDocument();
  });

  it("offers only statuses a cross-institution role can ever see, and every status to an institution user", async () => {
    await openRegistry();
    await waitForRows("FDR-0001");
    const labels = (el: HTMLElement) => within(el).getAllByRole("option").map((o) => o.textContent);
    expect(labels(status())).toEqual(["All statuses", "Submitted", "Correction requested", "Accepted", "Rejected"]);
  });

  it("offers Draft and Withdrawn to institution users", async () => {
    await openRegistry({ role: "INSTITUTION_USER" });
    await waitForRows("FDR-0001");
    expect(within(status()).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "All statuses", "Draft", "Submitted", "Correction requested", "Accepted", "Rejected", "Withdrawn",
    ]);
  });

  it("filters by bank, with options loaded from the banks endpoint", async () => {
    const api = await openRegistry({ visible: sample() });
    await waitForRows("FDR-0001");
    await waitFor(() => expect(within(bank()).getAllByRole("option")).toHaveLength(3));
    await userEvent.selectOptions(bank(), "State Bank of India");
    await waitFor(() => expect(lastFdrQuery(api).bank_id).toBe(BANKS[1].id));
    expect(await count()).toHaveTextContent("1 FDR match the current filters");
    expect(api.callsTo("GET /banks")[0].query).toEqual({ page_size: "500" });
  });

  it("filters by institution for cross-institution roles", async () => {
    const api = await openRegistry({ visible: sample() });
    await waitForRows("FDR-0001");
    await waitFor(() => expect(within(institution()).getAllByRole("option")).toHaveLength(3));
    await userEvent.selectOptions(institution(), "COEP — COEP Test Institute");
    await waitFor(() => expect(lastFdrQuery(api).institution_id).toBe(INSTITUTIONS[1].id));
    expect(await count()).toHaveTextContent("1 FDR match the current filters");
  });

  it("gives institution users no institution filter and never sends an institution id", async () => {
    const api = await openRegistry({ role: "INSTITUTION_USER", visible: sample() });
    await waitForRows("FDR-0001");
    expect(screen.queryByLabelText("Institution")).not.toBeInTheDocument();
    expect(api.callsTo("GET /institutions")).toHaveLength(0);
    expect(api.callsTo("GET /fdrs").every((c) => c.query.institution_id === undefined)).toBe(true);
  });

  it("sorts on the server from the column headings, flips direction, and returns to page 1", async () => {
    const api = await openRegistry({ visible: sample() });
    await waitForRows("FDR-0001");
    expect(lastFdrQuery(api).sort).toBeUndefined(); // the default request is unchanged
    const heading = (name: string) => screen.getByRole("columnheader", { name });
    expect(heading("Created")).toHaveAttribute("aria-sort", "descending");
    await userEvent.click(within(heading("Principal")).getByRole("button"));
    await waitFor(() => expect(lastFdrQuery(api)).toMatchObject({ sort: "principal", order: "asc", page: "1" }));
    expect(heading("Principal")).toHaveAttribute("aria-sort", "ascending");
    await userEvent.click(within(heading("Principal")).getByRole("button"));
    await waitFor(() => expect(lastFdrQuery(api)).toMatchObject({ sort: "principal", order: "desc" }));
    expect(heading("Principal")).toHaveAttribute("aria-sort", "descending");
    expect(heading("Created")).toHaveAttribute("aria-sort", "none");
  });

  it("combines filters and clears them all together", async () => {
    const api = await openRegistry({ visible: sample() });
    await waitForRows("FDR-0001");
    await waitFor(() => expect(within(bank()).getAllByRole("option")).toHaveLength(3));
    await userEvent.selectOptions(status(), "Accepted");
    await userEvent.selectOptions(bank(), "Bank of Maharashtra");
    await waitFor(() => expect(lastFdrQuery(api)).toMatchObject({ status: "ACCEPTED", bank_id: BANKS[0].id }));
    expect(await count()).toHaveTextContent("1 FDR match the current filters");
    await userEvent.click(screen.getByRole("button", { name: "Clear search and filters" }));
    await waitFor(() => expect(lastFdrQuery(api)).toEqual({ page: "1", page_size: "25" }));
    expect(status()).toHaveValue("");
    expect(bank()).toHaveValue("");
  });

  it("keeps the table usable when the bank or institution options fail to load, and can retry", async () => {
    let bankCalls = 0;
    await openRegistry({
      routes: { "GET /banks": (req) => (bankCalls++ === 0 ? textReply(500)(req) : banksEndpoint(req)), "GET /institutions": networkError },
    });
    expect(await screen.findByText("FDR-0001")).toBeInTheDocument(); // the list does not depend on the options
    expect(await screen.findByText(/banks could not be loaded/i)).toBeInTheDocument();
    expect(await screen.findByText(/institutions could not be loaded/i)).toBeInTheDocument();
    expect(bank()).toBeDisabled();
    await userEvent.click(within(screen.getByText(/banks could not be loaded/i).closest("p")!).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(bank()).toBeEnabled());
  });

  it("returns to page 1 when a filter changes", async () => {
    const api = await openRegistry({ visible: many(60).map((r, i) => ({ ...r, status: i % 2 ? "ACCEPTED" : "SUBMITTED" })) });
    await waitForRows("FDR-0001");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastFdrQuery(api).page).toBe("2"));
    await userEvent.selectOptions(status(), "Accepted");
    await waitFor(() => expect(lastFdrQuery(api)).toMatchObject({ status: "ACCEPTED", page: "1" }));
  });
});

describe("pagination", () => {
  it("uses the backend total and pages through the whole result set", async () => {
    const api = await openRegistry({ visible: many(60) });
    expect(await count()).toHaveTextContent("60 FDRs visible to you");
    expect(screen.getByText("Showing 1–25 of 60")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(api.callsTo("GET /fdrs")[0].query).toEqual({ page: "1", page_size: "25" });

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("FDR-0026")).toBeInTheDocument();
    expect(screen.queryByText("FDR-0001")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 26–50 of 60")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("FDR-0060")).toBeInTheDocument();
    expect(screen.getByText("Showing 51–60 of 60")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("FDR-0026")).toBeInTheDocument();
  });

  it("does not show pagination when everything fits on one page", async () => {
    await openRegistry({ visible: many(25) });
    await waitForRows("FDR-0001");
    expect(screen.queryByRole("navigation", { name: "Pagination" })).not.toBeInTheDocument();
  });

  it("recovers from an out-of-range page without looping", async () => {
    const api = await openRegistry({
      visible: many(30),
      routes: {
        "GET /fdrs": (req) =>
          req.query.page === "1" ? fdrEndpoint(many(30))(req) : jsonReply(200, { items: [], page: 2, page_size: 25, total: 30, total_pages: 2 })(req),
      },
    });
    await waitForRows("FDR-0001");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("There are no records on this page.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Go to the first page" }));
    expect(await screen.findByText("FDR-0001")).toBeInTheDocument();
    const settled = api.callsTo("GET /fdrs").length;
    await new Promise((r) => setTimeout(r, 500));
    expect(api.callsTo("GET /fdrs")).toHaveLength(settled); // no request loop
  });
});

describe("loading and failures", () => {
  it("shows a loading state first, with no count or table", async () => {
    const pending = deferred();
    await openRegistry({
      routes: { "GET /fdrs": async (req) => { await pending.promise; return fdrEndpoint(many(2))(req) as Response; } },
    });
    expect(await screen.findByText("Loading FDR records…")).toBeInTheDocument();
    expect(screen.queryByTestId("fdr-count")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(search()).toBeEnabled(); // the controls stay usable while loading
    pending.resolve();
    expect(await screen.findByText("FDR-0001")).toBeInTheDocument();
  });

  it.each([
    ["a server error", textReply(500), /HTTP 500/],
    ["a gateway error", textReply(502), /HTTP 502/],
    ["an unreachable server", networkError, /cannot reach the server/i],
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted for your role/i],
  ])("shows an error, never an empty list or zero count, for %s — and recovers on retry", async (_label, failing, message) => {
    let calls = 0;
    await openRegistry({
      routes: { "GET /fdrs": (req) => (calls++ === 0 ? failing(req) : fdrEndpoint(many(2))(req)) },
    });
    const alert = await screen.findByText("Could not load FDR records.");
    expect(alert.closest("[role=alert]")).toHaveTextContent(message);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fdr-count")).not.toBeInTheDocument();
    expect(screen.queryByText(/no fdr records are available|no fdrs match/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/₹\d|FDR-\d/);

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("FDR-0002")).toBeInTheDocument();
  });

  it("returns to sign-in with a notice when the session has expired", async () => {
    await openRegistry({ routes: { "GET /fdrs": jsonReply(401, { detail: "Not authenticated" }) } });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/session has expired/i);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("integrity of what is displayed", () => {
  it("shows only the records the API returned", async () => {
    await openRegistry({ visible: [fdr(7, { fdr_number: "ONLY-ONE" })] });
    await waitForRows("ONLY-ONE");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(await count()).toHaveTextContent("1 FDR visible to you");
  });

  it("never describes an accepted FDR as bank-verified", async () => {
    await openRegistry({ visible: [fdr(1, { status: "ACCEPTED" })] });
    await waitForRows("FDR-0001");
    const badge = within(screen.getByRole("table")).getByText("Accepted").closest("span")!;
    expect(badge).toHaveAttribute("title", expect.stringMatching(/not bank verification/i));
    expect(screen.getByText(/does not mean the fdr has been verified with the bank/i)).toBeInTheDocument();
    expect(within(screen.getByRole("table")).queryByText(/verified/i)).not.toBeInTheDocument();
  });

  it("is read-only: no mutating controls; the only buttons are sort headings and the only links open details", async () => {
    await openRegistry();
    await waitForRows("FDR-0001");
    expect(
      screen.queryByRole("button", { name: /(create|add|new|edit|delete|remove|submit|accept|reject|correction|upload|release|refund|withdraw)/i }),
    ).not.toBeInTheDocument();
    const table = within(screen.getByRole("table"));
    expect(table.queryAllByRole("link").every((l) => /^\/fdrs\/[^/]+$/.test(l.getAttribute("href") ?? ""))).toBe(true);
    const bodyButtons = within(table.getAllByRole("rowgroup")[1]).queryAllByRole("button");
    expect(bodyButtons).toHaveLength(0);
  });
});

describe("navigation and route protection", () => {
  it("makes FDRs a real sidebar link and opens the registry from the dashboard", async () => {
    await openRegistry({
      path: "/",
      routes: {
        "GET /health": jsonReply(200, { status: "ok", service: "s", version: "1", environment: "development" }),
        "GET /health/ready": jsonReply(200, { status: "ready", database: "ok" }),
        "GET /alerts": jsonReply(200, { items: [] }),
        "GET /activity": jsonReply(200, { items: [] }),
        "GET /reports/summary": jsonReply(200, {
          generated_on: "2026-10-27", scope: "all", fdr_total: 0, by_status: [], by_institution: [], principal_live_total: "0.00",
          maturities: [], release: [], release_status: { available: true, note: null }, verification: [],
          verification_status: { available: true, note: null }, exceptions: [], exceptions_status: { available: true, note: null },
        }),
      },
    });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const link = within(nav).getByRole("link", { name: "FDRs" });
    expect(link).toHaveAttribute("href", "/fdrs");
    expect(link.textContent).not.toMatch(/not built/i);
    expect(within(nav).getByRole("link", { name: "Documents" })).toHaveAttribute("href", "/documents");
    expect(nav.textContent).not.toMatch(/not built/i); // every section is a real page now
    await userEvent.click(link);
    expect(await screen.findByRole("heading", { name: "FDR Registry", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText("FDR-0001")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/fdrs");
  });

  it("keeps the registry behind sign-in", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }) }, { dashboardDefaults: false });
    renderApp("/fdrs");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("sends a user who must change a temporary password to the change-password screen instead", async () => {
    installFakeApi(
      { "GET /auth/me": jsonReply(200, makeSession(makeUser({ must_change_password: true }))) },
      { dashboardDefaults: false },
    );
    renderApp("/fdrs");
    expect(await screen.findByRole("heading", { name: "Set a new password" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
