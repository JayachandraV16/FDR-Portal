import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";

import type { CalendarEvent, CalendarOut } from "../lib/api";
import { addDays, addMonths, firstOfMonth, startOfWeek, todayIso } from "../lib/dates";
import { installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const TODAY = todayIso();
const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

function event(n: number, days: number, over: Partial<CalendarEvent> = {}): CalendarEvent {
  const marker = days < 0 ? "OVERDUE" : days <= 30 ? "SOON" : "UPCOMING";
  return {
    fdr_id: ID(n), fdr_number: `CAL-${n}`, institution_code: "PICT", institution_name: "Pune Institute", maturity_date: addDays(TODAY, days),
    status: "ACCEPTED", days_until: days, marker, link: `/fdrs/${ID(n)}`, ...over,
  };
}

function payload(events: CalendarEvent[], over: Partial<CalendarOut> = {}): CalendarOut {
  return {
    today: TODAY, start: TODAY, end: TODAY, soon_days: 30, total: events.length, truncated: false, events,
    summary: { overdue: 2, next_7: 3, next_30: 5, next_90: 8 }, note: "Dates are the maturity dates entered for each FDR.", ...over,
  };
}

const calendarReply = (events: CalendarEvent[], over: Partial<CalendarOut> = {}): Handler => (req) => {
  const body = payload(events.filter((e) => e.maturity_date >= req.query.start && e.maturity_date <= req.query.end), { start: req.query.start, end: req.query.end, ...over });
  body.total = over.total ?? body.events.length;
  return jsonReply(200, body)(req);
};

async function open(routes: Record<string, Handler | Handler[]> = {}, role = "AICTE_REVIEWER") {
  const api = installFakeApi({
    "GET /auth/me": jsonReply(200, makeSession(makeUser({ role }), "csrf-cal")),
    "GET /calendar": calendarReply([]),
    [`GET /fdrs/${ID(1)}`]: jsonReply(200, makeFdr({ id: ID(1), fdr_number: "CAL-1" })),
    [`GET /fdrs/${ID(1)}/history`]: jsonReply(200, { history: [], revisions: [] }),
    ...routes,
  });
  const view = renderApp("/calendar");
  await screen.findByRole("heading", { name: "Maturity calendar" });
  return { api, view };
}

const lastQuery = (api: Awaited<ReturnType<typeof open>>["api"]) => api.callsTo("GET /calendar").at(-1)!.query;

describe("month view", () => {
  it("shows the month grid for the current month and requests a six-week range from the server", async () => {
    const { api } = await open();
    await screen.findByRole("status");
    const q = api.callsTo("GET /calendar")[0].query;
    expect(q.start).toBe(startOfWeek(firstOfMonth(TODAY)));
    expect(q.end).toBe(addDays(q.start, 41));
    expect(screen.getByRole("list", { name: /\d{4}/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Month" })).toHaveAttribute("aria-pressed", "true");
  });

  it("places each real event on its maturity date as a link to the FDR, with the marker written out", async () => {
    const soon = event(1, 3);
    const later = event(2, 60, { institution_code: "COEP" });
    const past = event(3, -4);
    const draft = event(4, 2, { status: "DRAFT", marker: "NONE" });
    await open({ "GET /calendar": calendarReply([soon, later, past, draft], { start: addDays(TODAY, -40), end: addDays(TODAY, 80) }) });
    const link = await screen.findByRole("link", { name: /CAL-1/ });
    expect(link).toHaveAttribute("href", `/fdrs/${ID(1)}`);
    expect(link).toHaveTextContent("Due soon");
    // events outside the displayed grid are not drawn, but a past event in view says Past maturity
    for (const e of [past, draft]) {
      if (e.maturity_date >= startOfWeek(firstOfMonth(TODAY)) && e.maturity_date <= addDays(startOfWeek(firstOfMonth(TODAY)), 41)) {
        expect(screen.getByRole("link", { name: new RegExp(e.fdr_number) })).toHaveTextContent(e.marker === "NONE" ? "Draft" : "Past maturity");
      }
    }
  });

  it("marks today with aria-current and a Today label", async () => {
    await open({ "GET /calendar": calendarReply([event(1, 0)]) });
    await screen.findByRole("link", { name: /CAL-1/ });
    const cell = document.querySelector('[aria-current="date"]') as HTMLElement;
    expect(cell).not.toBeNull();
    expect(within(cell).getByText("Today")).toBeInTheDocument();
  });

  it("navigates between months and returns to today, requesting the right range each time", async () => {
    const { api } = await open();
    await screen.findByRole("button", { name: /Next month/ });
    const nextMonth = addMonths(TODAY, 1);
    await userEvent.click(screen.getByRole("button", { name: /Next month/ }));
    await screen.findByRole("heading", { name: new Date(`${nextMonth}T00:00:00Z`).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" }) });
    expect(lastQuery(api).start).toBe(startOfWeek(firstOfMonth(nextMonth)));
    await userEvent.click(screen.getByRole("button", { name: /Previous month/ }));
    await userEvent.click(screen.getByRole("button", { name: /Previous month/ }));
    expect(lastQuery(api).start).toBe(startOfWeek(firstOfMonth(addMonths(TODAY, -1))));
    await userEvent.click(screen.getByRole("button", { name: "Today" }));
    expect(lastQuery(api).start).toBe(startOfWeek(firstOfMonth(TODAY)));
  });

  it("collapses a crowded day to three events and a '+N more' button that opens that week", async () => {
    const many = [1, 2, 3, 4, 5].map((n) => event(n, 3));
    const { api } = await open({ "GET /calendar": calendarReply(many) });
    expect(await screen.findByRole("button", { name: "+2 more" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /CAL-/ })).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "+2 more" }));
    expect(await screen.findAllByRole("link", { name: /CAL-/ })).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Week" })).toHaveAttribute("aria-pressed", "true");
    expect(lastQuery(api).end).toBe(addDays(lastQuery(api).start, 6));
  });
});

describe("week and list views", () => {
  it("shows a seven-day week range", async () => {
    const { api } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Week" }));
    await screen.findByRole("button", { name: /Next week/ });
    expect(lastQuery(api).start).toBe(startOfWeek(TODAY));
    expect(lastQuery(api).end).toBe(addDays(startOfWeek(TODAY), 6));
  });

  it.each(["7", "30", "90"])("lists the next %s days from today, grouped by day, soonest first", async (n) => {
    const { api } = await open({ "GET /calendar": calendarReply([event(2, 5), event(1, 2)]) });
    await userEvent.click(screen.getByRole("button", { name: `Next ${n} days` }));
    const list = await screen.findByRole("list", { name: `FDRs maturing in the next ${n} days` });
    expect(lastQuery(api)).toMatchObject({ start: TODAY, end: addDays(TODAY, Number(n)) });
    const links = within(list).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([expect.stringContaining("CAL-1"), expect.stringContaining("CAL-2")]);
    expect(screen.queryByRole("button", { name: /Next week|Next month/ })).not.toBeInTheDocument(); // no month paging in a list
  });

  it("jumps to a list from the summary cards", async () => {
    await open();
    await userEvent.click((await screen.findAllByRole("button", { name: "Show list" }))[1]);
    expect(await screen.findByRole("button", { name: "Next 30 days" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("summary, alerts and honesty", () => {
  it("shows the server's counts for live FDRs and says what they cover", async () => {
    await open();
    expect(await screen.findByTestId("summary-past-maturity")).toHaveTextContent("2");
    expect(screen.getByTestId("summary-next-7-days")).toHaveTextContent("3");
    expect(screen.getByTestId("summary-next-90-days")).toHaveTextContent("8");
    expect(screen.getByText(/Live FDRs only/)).toBeInTheDocument();
  });

  it("distinguishes the maturity date from any renewal deadline", async () => {
    await open();
    expect(await screen.findByText(/maturity dates entered for each FDR/i)).toBeInTheDocument();
  });

  it("lists only maturity alerts with links, from the existing alerts endpoint", async () => {
    await open({
      "GET /alerts": jsonReply(200, {
        generated_on: TODAY, basis: "computed", categories: [], items: [
          { category: "MATURITY_SOON", severity: "MEDIUM", fdr_id: ID(1), fdr_number: "ALR-1", institution_code: "P", institution_name: "P", message: "Matures in 5 days.", date: TODAY, days: 5, link: `/fdrs/${ID(1)}` },
          { category: "AWAITING_REVIEW", severity: "LOW", fdr_id: ID(2), fdr_number: "NOT-SHOWN", institution_code: "P", institution_name: "P", message: "Waiting.", date: null, days: null, link: "/review" },
        ],
      }),
    });
    const panel = await screen.findByRole("region", { name: "Maturity alerts" });
    expect(await within(panel).findByRole("link", { name: "ALR-1" })).toHaveAttribute("href", `/fdrs/${ID(1)}`);
    expect(panel).not.toHaveTextContent("NOT-SHOWN");
  });

  it("keeps the calendar usable when the alerts panel fails, and lets it retry on its own", async () => {
    await open({ "GET /alerts": [textReply(500), jsonReply(200, { generated_on: TODAY, basis: "x", categories: [], items: [] })] });
    const panel = await screen.findByRole("region", { name: "Maturity alerts" });
    expect(await within(panel).findByRole("alert")).toHaveTextContent(/could not load alerts/i);
    expect(screen.getByRole("heading", { name: "Maturity calendar" })).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Retry" }));
    expect(await within(panel).findByText("No maturity alerts right now.")).toBeInTheDocument();
  });
});

describe("filters and scope", () => {
  it("sends the chosen status to the server", async () => {
    const { api } = await open();
    await screen.findByLabelText("Status");
    await userEvent.selectOptions(screen.getByLabelText("Status"), "Accepted");
    await screen.findByRole("status");
    expect(lastQuery(api).status).toBe("ACCEPTED");
  });

  it("offers an institution filter to AICTE roles and sends the choice", async () => {
    const inst = { id: ID(77), code: "COEP", name: "COEP Test Institute", state: null, institution_type: null, contact_email: null, status: "active", created_at: "2026-01-01T00:00:00Z" };
    const { api } = await open({ "GET /institutions": jsonReply(200, { items: [inst], total: 1, limit: 100, offset: 0 }) });
    await screen.findByLabelText("Institution");
    await screen.findByRole("option", { name: /COEP/ });
    await userEvent.selectOptions(screen.getByLabelText("Institution"), screen.getByRole("option", { name: /COEP/ }));
    await screen.findByRole("status");
    expect(lastQuery(api).institution_id).toBe(ID(77));
  });

  it("gives institution users no institution filter and never sends an institution id", async () => {
    const { api } = await open({}, "INSTITUTION_USER");
    await screen.findByLabelText("Status");
    expect(screen.queryByLabelText("Institution")).not.toBeInTheDocument();
    expect(api.callsTo("GET /calendar").every((c) => c.query.institution_id === undefined)).toBe(true);
  });
});

describe("states", () => {
  it("shows a clear empty state, not a blank grid", async () => {
    await open({ "GET /calendar": calendarReply([]) });
    expect(await screen.findByText(/No FDR matures in this period/)).toBeInTheDocument();
  });

  it("says when filters caused the empty result", async () => {
    await open();
    await userEvent.selectOptions(await screen.findByLabelText("Status"), "Rejected");
    expect(await screen.findByText(/No FDR matures in this period with these filters/)).toBeInTheDocument();
  });

  it("says the list was cut off and how many exist", async () => {
    await open({ "GET /calendar": calendarReply([event(1, 3)], { total: 1500, truncated: true }) });
    expect(await screen.findByText(/Showing the first 1 of 1,500 events/)).toBeInTheDocument();
  });

  it("shows loading, then an error with retry that recovers", async () => {
    await open({ "GET /calendar": [textReply(500), calendarReply([event(1, 3)])] });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not load the calendar/i);
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: /CAL-1/ })).toBeInTheDocument();
  });

  it("reports an unreachable server as an error rather than an empty calendar", async () => {
    await open({ "GET /calendar": networkError });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load the calendar/i);
    expect(screen.queryByText(/No FDR matures/)).not.toBeInTheDocument();
  });
});

describe("navigation and accessibility", () => {
  it("opens the FDR detail page from an event", async () => {
    await open({ "GET /calendar": calendarReply([event(1, 3)]) });
    await userEvent.click(await screen.findByRole("link", { name: /CAL-1/ }));
    expect(await screen.findByRole("heading", { name: "FDR details" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(`/fdrs/${ID(1)}`);
  });

  it("is linked from the sidebar", async () => {
    await open();
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Calendar" })).toHaveAttribute("href", "/calendar");
  });

  it("has no automated accessibility violations with events, and its controls are keyboard-reachable", async () => {
    const { view } = await open({ "GET /calendar": calendarReply([event(1, 3), event(2, -2)]) });
    await screen.findByRole("link", { name: /CAL-1/ });
    expect(await axe(view.container)).toHaveNoViolations();
    await userEvent.tab();
    expect(document.activeElement).not.toBe(document.body);
  });
});
