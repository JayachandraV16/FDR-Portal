import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { FdrOut } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const HEALTH = { status: "ok", service: "fdr-portal-api", version: "1.2.3", environment: "development" };
const READY = { status: "ready", database: "ok" };
const INSTITUTION = {
  id: "11111111-2222-4333-8444-555555555555", code: "PICT", name: "Pune Institute of Computer Technology",
  state: "Maharashtra", institution_type: "Engineering", contact_email: null, status: "active", created_at: "2026-10-26T00:00:00Z",
};

/** GET /fdrs as the real backend behaves: `total` counts every visible match (filtered by status),
 * while `items` is only the requested page. Totals are deliberately independent of items. */
function fdrEndpoint(totals: Record<string, number>, items: FdrOut[] = []): Handler {
  return (req) => {
    const pageSize = Number(req.query.page_size ?? 25);
    return jsonReply(200, {
      items: items.slice(0, pageSize), page: 1, page_size: pageSize,
      total: totals[req.query.status ?? "ALL"] ?? 0, total_pages: 1,
    })(req);
  };
}
const institutionsEndpoint = (total = 4, items = [INSTITUTION]): Handler => jsonReply(200, { items, total, limit: 1, offset: 0 });

interface Options { role?: string; routes?: Parameters<typeof installFakeApi>[0] }

async function openDashboard({ role = "AICTE_REVIEWER", routes = {} }: Options = {}) {
  const user = makeUser({
    role, full_name: "Meera Kulkarni",
    institution_id: role === "INSTITUTION_USER" ? INSTITUTION.id : null,
  });
  const api = installFakeApi(
    {
      "GET /auth/me": jsonReply(200, makeSession(user)),
      "GET /health": jsonReply(200, HEALTH),
      "GET /health/ready": jsonReply(200, READY),
      "GET /fdrs": fdrEndpoint({}),
      "GET /institutions": institutionsEndpoint(),
      "GET /users": jsonReply(200, { items: [], total: 3, limit: 1, offset: 0 }),
      ...routes,
    },
    { dashboardDefaults: false },
  );
  renderApp("/");
  await screen.findByRole("heading", { name: "Overview" });
  return api;
}

const card = (key: string) => screen.getByTestId(`metric-${key}`);
const cardValue = async (key: string, text: string) => within(card(key)).findByText(text);

describe("loaded dashboard", () => {
  it("shows each count exactly as the backend's totals, requested per status", async () => {
    const api = await openDashboard({
      routes: {
        "GET /fdrs": fdrEndpoint({ ALL: 1234, SUBMITTED: 40, CORRECTION_REQUESTED: 5, ACCEPTED: 1100, REJECTED: 89 }),
      },
    });
    await cardValue("total", "1,234");
    await cardValue("submitted", "40");
    await cardValue("correction", "5");
    await cardValue("accepted", "1,100");
    await cardValue("rejected", "89");
    await cardValue("institutions", "4");

    const queries = api.callsTo("GET /fdrs").map((c) => `${c.query.status ?? "ALL"}:${c.query.page_size}`);
    expect(queries).toEqual(expect.arrayContaining(["ALL:1", "SUBMITTED:1", "CORRECTION_REQUESTED:1", "ACCEPTED:1", "REJECTED:1"]));
  });

  it("never derives a count from the rows returned (a one-row page can still mean 40)", async () => {
    await openDashboard({
      routes: { "GET /fdrs": fdrEndpoint({ ALL: 40 }, [makeFdr(), makeFdr({ id: "2" }), makeFdr({ id: "3" })]) },
    });
    await cardValue("total", "40"); // not 1 (the page size asked for) and not 3 (rows available)
    expect(within(card("total")).queryByText("1")).not.toBeInTheDocument();
  });

  it("lists recent FDRs with exact money formatting, dates and status labels", async () => {
    await openDashboard({
      routes: {
        "GET /fdrs": fdrEndpoint({ ALL: 7 }, [
          makeFdr({ fdr_number: "BOM-2026-77", principal_amount: "12345678.90", maturity_date: "2027-03-31", status: "CORRECTION_REQUESTED",
            institution: { id: "i", code: "COEP", name: "COEP Technological University" }, bank: { id: "b", code: "BOM", name: "Bank of Maharashtra" } }),
        ]),
      },
    });
    const row = (await screen.findByText("BOM-2026-77")).closest("tr")!;
    expect(within(row).getByText("₹1,23,45,678.90")).toBeInTheDocument();
    expect(within(row).getByText("31 Mar 2027")).toBeInTheDocument();
    expect(within(row).getByText("Correction requested")).toBeInTheDocument();
    expect(within(row).getByText("COEP Technological University")).toBeInTheDocument();
    expect(within(row).getByText("Bank of Maharashtra")).toBeInTheDocument();
    expect(screen.getByText(/showing the newest 1 of 7 records/i)).toBeInTheDocument();
  });

  it("states that the demo environment is not verified data and drops the obsolete checkpoint text", async () => {
    await openDashboard();
    expect(screen.getByRole("note")).toHaveTextContent(/development \/ demonstration environment/i);
    expect(document.body.textContent).not.toMatch(/checkpoint 0/i);
  });
});

describe("empty database", () => {
  it("shows genuine zeros and an honest empty table when the backend confirms there are no FDRs", async () => {
    await openDashboard({ routes: { "GET /fdrs": fdrEndpoint({}) } });
    for (const key of ["total", "submitted", "correction", "accepted", "rejected"]) await cardValue(key, "0");
    expect(await screen.findByText("No FDR records exist yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("failures are never shown as zeros", () => {
  it("marks only the failed metric unavailable, keeps the others, and recovers on retry", async () => {
    const accepted = [textReply(500), fdrEndpoint({ ACCEPTED: 9 })];
    let acceptedCalls = 0;
    await openDashboard({
      routes: {
        "GET /fdrs": (req) => {
          if (req.query.status === "ACCEPTED") return accepted[Math.min(acceptedCalls++, 1)](req);
          return fdrEndpoint({ ALL: 12, SUBMITTED: 3 })(req);
        },
      },
    });
    await cardValue("total", "12");
    const failed = await within(card("accepted")).findByRole("alert");
    expect(failed).toHaveTextContent("Unavailable");
    expect(failed).toHaveTextContent("HTTP 500");
    expect(within(card("accepted")).queryByText("0")).not.toBeInTheDocument();

    await userEvent.click(within(card("accepted")).getByRole("button", { name: "Retry" }));
    await cardValue("accepted", "9");
  });

  it("says when the server cannot be reached, with no numbers invented", async () => {
    await openDashboard({
      routes: { "GET /fdrs": networkError, "GET /institutions": networkError, "GET /health": networkError, "GET /health/ready": networkError },
    });
    for (const key of ["total", "submitted", "correction", "accepted", "rejected", "institutions"]) {
      expect(await within(card(key)).findByRole("alert")).toHaveTextContent("Cannot reach the server");
    }
    expect(document.body.textContent).not.toMatch(/₹\d/);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(await screen.findByText(/could not load fdr records/i)).toBeInTheDocument();
    expect(screen.queryByText(/no fdr records exist yet/i)).not.toBeInTheDocument();
  });

  it("does not show 'no records' when the recent list fails", async () => {
    await openDashboard({
      routes: { "GET /fdrs": (req) => (req.query.page_size === "5" ? textReply(500)(req) : fdrEndpoint({ ALL: 2 })(req)) },
    });
    expect(await screen.findByText(/could not load fdr records/i)).toBeInTheDocument();
    expect(screen.queryByText(/no fdr records exist yet/i)).not.toBeInTheDocument();
    await cardValue("total", "2");
  });

  it("shows a forbidden metric as unavailable rather than zero", async () => {
    await openDashboard({ routes: { "GET /institutions": jsonReply(403, { detail: "Forbidden" }) } });
    expect(await within(card("institutions")).findByRole("alert")).toHaveTextContent("Not permitted for your role");
  });
});

describe("loading states", () => {
  it("shows loading indicators while data is pending", async () => {
    const pending = deferred();
    const slow: Handler = async (req) => {
      await pending.promise;
      return fdrEndpoint({ ALL: 3 })(req) as Response;
    };
    await openDashboard({ routes: { "GET /fdrs": slow } });
    expect(card("total")).toHaveAttribute("aria-busy", "true");
    expect(within(card("total")).getByRole("status")).toHaveTextContent("Loading");
    expect(screen.getByText("Loading records…")).toBeInTheDocument();
    pending.resolve();
    await cardValue("total", "3");
    expect(card("total")).toHaveAttribute("aria-busy", "false");
  });
});

describe("system status", () => {
  const apiRow = () => screen.getByText("API service").closest("div")!;
  const dbRow = () => screen.getByText("Database").closest("div")!;

  it("reports API reachability and database readiness as two separate facts", async () => {
    await openDashboard();
    expect(await within(apiRow()).findByText(/reachable — fdr-portal-api v1\.2\.3 \(development\)/i)).toBeInTheDocument();
    expect(await within(dbRow()).findByText(/ready — accepting queries/i)).toBeInTheDocument();
  });

  it("shows the API as up but the database as not ready when only readiness fails", async () => {
    await openDashboard({ routes: { "GET /health/ready": jsonReply(503, { detail: "Database unavailable" }) } });
    expect(await within(apiRow()).findByText(/reachable/i)).toBeInTheDocument();
    expect(await within(dbRow()).findByText(/not ready \(HTTP 503\)/i)).toBeInTheDocument();
  });

  it("shows the API as unavailable and the database as unknown when the API cannot be reached", async () => {
    await openDashboard({ routes: { "GET /health": networkError, "GET /health/ready": networkError } });
    expect(await within(apiRow()).findByText(/unavailable — cannot reach the api/i)).toBeInTheDocument();
    expect(await within(dbRow()).findByText(/unknown — api not reachable/i)).toBeInTheDocument();
  });

  it("shows a checking state first and can be rechecked", async () => {
    const pending = deferred();
    const api = await openDashboard({
      routes: { "GET /health": async () => { await pending.promise; return jsonReply(200, HEALTH)({} as never) as Response; } },
    });
    expect(within(apiRow()).getByText("Checking…")).toBeInTheDocument();
    pending.resolve();
    await within(apiRow()).findByText(/reachable/i);
    await userEvent.click(screen.getByRole("button", { name: "Recheck" }));
    await within(apiRow()).findByText(/reachable/i);
    expect(api.callsTo("GET /health")).toHaveLength(2);
    expect(api.callsTo("GET /health/ready")).toHaveLength(2);
  });
});

describe("unsupported capabilities", () => {
  it("shows principal total and maturity metrics as unavailable with the reason", async () => {
    await openDashboard();
    const unsupported = screen.getAllByTestId("metric-unsupported");
    expect(unsupported).toHaveLength(2);
    expect(unsupported[0]).toHaveTextContent("Total principal amount");
    expect(unsupported[0]).toHaveTextContent("Unavailable");
    expect(unsupported[0]).toHaveTextContent(/no aggregate endpoint/i);
    expect(unsupported[1]).toHaveTextContent("Maturing soon");
    expect(unsupported[1]).toHaveTextContent(/no maturity-date filter/i);
  });

  it("shows no recent activity, only an explanation, and requests no audit data", async () => {
    const api = await openDashboard();
    const panel = screen.getByRole("region", { name: "Recent activity" });
    expect(panel).toHaveTextContent("Not available");
    expect(panel).toHaveTextContent(/does not yet provide a way to read audit events/i);
    expect(api.calls.some((c) => /audit|activity|history/i.test(c.path))).toBe(false);
  });
});

describe("role and institution context", () => {
  it("shows the signed-in user's name, role and all-institution scope for AICTE roles", async () => {
    const api = await openDashboard({ role: "AICTE_APPROVER" });
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByText("Meera Kulkarni", { selector: "span.font-medium" })).toBeInTheDocument();
    expect(screen.getByText("Role: AICTE approver")).toBeInTheDocument();
    expect(screen.getByText("Scope: all institutions")).toBeInTheDocument();
    expect(screen.getByTestId("metric-institutions")).toBeInTheDocument();
    expect(screen.queryByTestId("metric-users")).not.toBeInTheDocument();
    expect(screen.queryByTestId("metric-draft")).not.toBeInTheDocument();
    expect(api.callsTo("GET /users")).toHaveLength(0);
  });

  it("shows the institution name from the backend, and draft/withdrawn counts, for an institution user", async () => {
    const api = await openDashboard({
      role: "INSTITUTION_USER",
      routes: { "GET /fdrs": fdrEndpoint({ ALL: 6, DRAFT: 2, WITHDRAWN: 1 }), "GET /institutions": institutionsEndpoint(1) },
    });
    expect(await screen.findByText("Institution: Pune Institute of Computer Technology (PICT)")).toBeInTheDocument();
    expect(screen.getByText("Role: Institution user")).toBeInTheDocument();
    await cardValue("draft", "2");
    await cardValue("withdrawn", "1");
    expect(screen.queryByTestId("metric-institutions")).not.toBeInTheDocument();
    expect(screen.queryByText("Scope: all institutions")).not.toBeInTheDocument();
    expect(api.callsTo("GET /users")).toHaveLength(0);
  });

  it("says the institution is unavailable if it cannot be loaded, without guessing a name", async () => {
    await openDashboard({ role: "INSTITUTION_USER", routes: { "GET /institutions": textReply(500) } });
    expect(await screen.findByText("Institution: unavailable")).toBeInTheDocument();
  });

  it("offers administrators the user-account count, and only administrators", async () => {
    const api = await openDashboard({ role: "ADMIN" });
    await cardValue("users", "3");
    expect(api.callsTo("GET /users")[0].query).toMatchObject({ limit: "1" });
  });

  it("treats auditors like other cross-institution read-only roles", async () => {
    await openDashboard({ role: "AUDITOR" });
    expect(screen.getByText("Role: Auditor")).toBeInTheDocument();
    expect(screen.queryByTestId("metric-users")).not.toBeInTheDocument();
  });

  it("describes the current application state accurately in the quick actions", async () => {
    await openDashboard();
    const panel = screen.getByRole("region", { name: "Quick actions" });
    expect(panel).toHaveTextContent(/institutions and fdr registry pages are available from the sidebar/i);
    expect(panel).toHaveTextContent(/documents, verification, alerts, exceptions, release \/ refund, reports and administration are not built yet/i);
    // The old wording claimed institution and FDR screens did not exist.
    expect(panel).not.toHaveTextContent(/fdr, institution and administration screens are not built/i);
  });

  it("links only to destinations that exist", async () => {
    await openDashboard();
    const main = screen.getByRole("main");
    const hrefs = within(main).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/change-password"]);
  });
});

describe("expired session", () => {
  it("returns to sign-in with a notice when a dashboard request is rejected with 401", async () => {
    await openDashboard({ routes: { "GET /fdrs": jsonReply(401, { detail: "Not authenticated" }) } });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/session has expired/i);
    expect(screen.getByTestId("location")).toHaveTextContent("/login");
  });
});

describe("no fabricated data", () => {
  it("renders no amounts, no FDR numbers and no counts when nothing could be loaded", async () => {
    await openDashboard({
      routes: { "GET /fdrs": textReply(500), "GET /institutions": textReply(500), "GET /health": textReply(500), "GET /health/ready": textReply(503) },
    });
    for (const key of ["total", "submitted", "accepted"]) await within(card(key)).findByRole("alert");
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/₹/);
    expect(text).not.toMatch(/FDR-\d|-FDR-/);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    for (const key of ["total", "submitted", "correction", "accepted", "rejected"]) {
      expect(within(card(key)).queryByLabelText(/: \d+$/)).not.toBeInTheDocument();
    }
  });
});
