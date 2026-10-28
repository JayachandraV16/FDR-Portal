import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { InstitutionOut } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

function makeInstitution(i: number, overrides: Partial<InstitutionOut> = {}): InstitutionOut {
  return {
    id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
    code: `INST-${String(i).padStart(3, "0")}`,
    name: `Test Institution ${i}`,
    state: "Maharashtra",
    institution_type: "Engineering",
    contact_email: `inst${i}@example.invalid`,
    status: "active",
    created_at: "2026-10-26T00:10:00Z",
    ...overrides,
  };
}
const dataset = (n: number) => Array.from({ length: n }, (_, k) => makeInstitution(k + 1));

/** GET /institutions as the real backend behaves: ordered, sliced by limit/offset, and `total` is
 * the size of the whole visible set. */
function institutionsEndpoint(all: InstitutionOut[]): Handler {
  return (req) => {
    const limit = Number(req.query.limit ?? 50);
    const offset = Number(req.query.offset ?? 0);
    return jsonReply(200, { items: all.slice(offset, offset + limit), total: all.length, limit, offset })(req);
  };
}

async function openInstitutions({
  role = "AICTE_REVIEWER",
  routes = {},
  path = "/institutions",
}: { role?: string; routes?: Parameters<typeof installFakeApi>[0]; path?: string } = {}) {
  const user = makeUser({ role, institution_id: role === "INSTITUTION_USER" ? makeInstitution(1).id : null });
  const api = installFakeApi(
    { "GET /auth/me": jsonReply(200, makeSession(user)), "GET /institutions": institutionsEndpoint(dataset(10)), ...routes },
    { dashboardDefaults: false },
  );
  renderApp(path);
  return api;
}
const heading = () => screen.findByRole("heading", { name: "Institutions", level: 1 });
const rowFor = (code: string) => screen.getByText(code).closest("tr")!;

describe("loaded list", () => {
  it("renders every field the backend returns for each institution", async () => {
    const api = await openInstitutions({
      routes: {
        "GET /institutions": institutionsEndpoint([
          makeInstitution(1, { code: "PICT", name: "Pune Institute of Computer Technology", institution_type: "Engineering", contact_email: "office@example.invalid", created_at: "2026-10-26T00:10:00Z" }),
          makeInstitution(2, { code: "IITB", name: "Indian Institute of Technology Bombay", institution_type: "Institute of National Importance", status: "inactive", contact_email: null }),
        ]),
      },
    });
    await heading();
    const pict = within(await screen.findByText("PICT").then((el) => el.closest("tr")!));
    expect(pict.getByText("Pune Institute of Computer Technology")).toBeInTheDocument();
    expect(pict.getByText("Maharashtra")).toBeInTheDocument();
    expect(pict.getByText("Engineering")).toBeInTheDocument();
    expect(pict.getByText("active")).toBeInTheDocument();
    expect(pict.getByText("office@example.invalid")).toBeInTheDocument();
    expect(pict.getByText("26 Oct 2026")).toBeInTheDocument();

    const iitb = within(rowFor("IITB"));
    expect(iitb.getByText("inactive")).toBeInTheDocument();
    expect(iitb.getByText("Not provided")).toBeInTheDocument(); // null stays null; nothing is invented
    expect(api.callsTo("GET /institutions")[0].query).toEqual({ limit: "25", offset: "0" });
  });

  it("shows only columns the API provides", async () => {
    await openInstitutions();
    await screen.findByText("INST-001");
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Code", "Name", "State", "Type", "Status", "Contact email", "Added to portal"]);
  });

  it("states the complete count from the backend total", async () => {
    await openInstitutions();
    expect(await screen.findByTestId("institution-count")).toHaveTextContent("10 institutions in total");
    expect(screen.getAllByRole("row")).toHaveLength(11); // header + 10, no pagination for a single page
    expect(screen.queryByRole("navigation", { name: "Pagination" })).not.toBeInTheDocument();
  });

  it("is read-only: no create, edit or delete controls and no links out of the table", async () => {
    await openInstitutions();
    await screen.findByText("INST-001");
    expect(screen.queryByRole("button", { name: /add|create|new|edit|delete|remove|import/i })).not.toBeInTheDocument();
    expect(within(screen.getByRole("table")).queryAllByRole("link")).toHaveLength(0);
  });

  it("shows the demonstration-data notice", async () => {
    await openInstitutions();
    await heading();
    expect(screen.getByRole("note")).toHaveTextContent(/demonstration environment/i);
  });
});

describe("pagination (server side)", () => {
  it("reports the whole dataset but one page of rows, and pages through with limit/offset", async () => {
    const api = await openInstitutions({ routes: { "GET /institutions": institutionsEndpoint(dataset(60)) } });
    expect(await screen.findByTestId("institution-count")).toHaveTextContent("60 institutions in total");
    expect(screen.getAllByRole("row")).toHaveLength(26); // header + 25 rows, not 60
    expect(screen.getByText("Showing 1–25 of 60")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("INST-026")).toBeInTheDocument();
    expect(screen.queryByText("INST-001")).not.toBeInTheDocument();
    expect(api.callsTo("GET /institutions").at(-1)!.query).toEqual({ limit: "25", offset: "25" });
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("INST-060")).toBeInTheDocument();
    expect(screen.getByText("Showing 51–60 of 60")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("INST-026")).toBeInTheDocument();
  });

  it("recovers when a page turns out to be empty", async () => {
    await openInstitutions({
      routes: {
        "GET /institutions": (req) =>
          req.query.offset === "0"
            ? institutionsEndpoint(dataset(30))(req)
            : jsonReply(200, { items: [], total: 30, limit: 25, offset: Number(req.query.offset) })(req),
      },
    });
    await screen.findByText("INST-001");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("There are no institutions on this page.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Go to the first page" }));
    expect(await screen.findByText("INST-001")).toBeInTheDocument();
  });
});

describe("filter", () => {
  const filterBox = () => screen.getByRole("searchbox", { name: "Filter this page" });

  it("filters the rows on screen, case-insensitively, without calling the server", async () => {
    const api = await openInstitutions({
      routes: {
        "GET /institutions": institutionsEndpoint([
          makeInstitution(1, { code: "PICT", name: "Pune Institute of Computer Technology" }),
          makeInstitution(2, { code: "VJTI", name: "Veermata Jijabai Technological Institute", state: "Maharashtra" }),
          makeInstitution(3, { code: "XYZ", name: "Elsewhere College", state: "Karnataka" }),
        ]),
      },
    });
    await screen.findByText("PICT");
    const before = api.callsTo("GET /institutions").length;
    await userEvent.type(filterBox(), "jijabai");
    expect(screen.getByText("VJTI")).toBeInTheDocument();
    expect(screen.queryByText("PICT")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 3 rows on this page.")).toBeInTheDocument();
    expect(api.callsTo("GET /institutions")).toHaveLength(before);
    expect(screen.getByTestId("institution-count")).toHaveTextContent("3 institutions in total");

    await userEvent.clear(filterBox());
    await userEvent.type(filterBox(), "karnataka");
    expect(screen.getByText("XYZ")).toBeInTheDocument();
    expect(screen.queryByText("VJTI")).not.toBeInTheDocument();
  });

  it("says plainly that it only covers the current page", async () => {
    await openInstitutions({ routes: { "GET /institutions": institutionsEndpoint(dataset(60)) } });
    await screen.findByText("INST-001");
    expect(screen.getByText(/filters only the 25 rows shown on this page/i)).toBeInTheDocument();
    await userEvent.type(filterBox(), "INST-040"); // exists, but on page 2
    expect(await screen.findByText(/no institutions on this page match “INST-040”\. other pages are not searched\./i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filter" }));
    expect(screen.getByText("INST-001")).toBeInTheDocument();
  });
});

describe("empty", () => {
  it("shows an honest empty state, no table and no filter, when there are no institutions", async () => {
    await openInstitutions({ routes: { "GET /institutions": institutionsEndpoint([]) } });
    expect(await screen.findByText("No institutions have been added yet.")).toBeInTheDocument();
    expect(screen.getByTestId("institution-count")).toHaveTextContent("0 institutions in total");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });

  it("explains an institution user with no linked institution", async () => {
    await openInstitutions({ role: "INSTITUTION_USER", routes: { "GET /institutions": institutionsEndpoint([]) } });
    expect(await screen.findByText("No institution is linked to your account.")).toBeInTheDocument();
  });
});

describe("failures", () => {
  it.each([
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted for your role/i],
  ])("shows an error, never an empty list, for %s — and recovers on retry", async (_label, failing, message) => {
    let calls = 0;
    await openInstitutions({
      routes: { "GET /institutions": (req) => (calls++ === 0 ? failing(req) : institutionsEndpoint(dataset(3))(req)) },
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load institutions.");
    expect(alert).toHaveTextContent(message);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/no institutions have been added/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("institution-count")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/INST-\d/);

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("INST-003")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns to sign-in with a notice if the session has expired", async () => {
    await openInstitutions({ routes: { "GET /institutions": jsonReply(401, { detail: "Not authenticated" }) } });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/session has expired/i);
  });
});

describe("loading", () => {
  it("shows a loading state, with no count or table, until the data arrives", async () => {
    const pending = deferred();
    await openInstitutions({
      routes: {
        "GET /institutions": async (req) => {
          await pending.promise;
          return institutionsEndpoint(dataset(2))(req) as Response;
        },
      },
    });
    await heading();
    expect(await screen.findByText("Loading institutions…")).toBeInTheDocument();
    expect(screen.queryByTestId("institution-count")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    pending.resolve();
    expect(await screen.findByText("INST-001")).toBeInTheDocument();
    expect(screen.queryByText("Loading institutions…")).not.toBeInTheDocument();
  });
});

describe("roles and scope", () => {
  it("shows an institution user only what the backend returns for them, with matching wording", async () => {
    const api = await openInstitutions({
      role: "INSTITUTION_USER",
      routes: { "GET /institutions": institutionsEndpoint([makeInstitution(1, { code: "PICT", name: "Pune Institute of Computer Technology" })]) },
    });
    expect(await screen.findByText("PICT")).toBeInTheDocument();
    expect(screen.getByText(/the institution linked to your account/i)).toBeInTheDocument();
    expect(screen.getByTestId("institution-count")).toHaveTextContent("1 institution linked to your account");
    // The scope is decided by the server; the page never sends an institution identifier.
    expect(Object.keys(api.callsTo("GET /institutions")[0].query).sort()).toEqual(["limit", "offset"]);
  });

  it.each(["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"])("presents the full register to %s", async (role) => {
    await openInstitutions({ role });
    expect(await screen.findByTestId("institution-count")).toHaveTextContent("10 institutions in total");
    expect(screen.getByText(/institutions recorded in the portal/i)).toBeInTheDocument();
  });
});

describe("navigation", () => {
  it("makes Institutions a real sidebar link and opens the page from the dashboard", async () => {
    await openInstitutions({
      path: "/",
      routes: {
        "GET /health": jsonReply(200, { status: "ok", service: "s", version: "1", environment: "development" }),
        "GET /health/ready": jsonReply(200, { status: "ready", database: "ok" }),
        "GET /fdrs": jsonReply(200, { items: [], page: 1, page_size: 1, total: 0, total_pages: 0 }),
        "GET /alerts": jsonReply(200, { generated_on: "2026-10-27", basis: "computed", categories: [], items: [] }),
        "GET /activity": jsonReply(200, { items: [] }),
        "GET /reports/summary": jsonReply(200, {
          generated_on: "2026-10-27", scope: "all", fdr_total: 0, by_status: [], by_institution: [], principal_live_total: "0.00",
          maturities: [], release: [], release_status: { available: true, note: null }, verification: [],
          verification_status: { available: true, note: null }, exceptions: [], exceptions_status: { available: true, note: null },
        }),
      },
    });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const link = within(nav).getByRole("link", { name: "Institutions" });
    expect(link).toHaveAttribute("href", "/institutions");
    expect(link.textContent).not.toMatch(/not built/i);
    // The other sections are real pages too: no sidebar entry is a placeholder any more.
    expect(within(nav).getByRole("link", { name: "Documents" })).toHaveAttribute("href", "/documents");
    expect(nav.textContent).not.toMatch(/not built/i);

    await userEvent.click(link);
    expect(await heading()).toBeInTheDocument();
    expect(await screen.findByText("INST-001")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/institutions");
  });

  it("keeps the page behind sign-in", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }) }, { dashboardDefaults: false });
    renderApp("/institutions");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
