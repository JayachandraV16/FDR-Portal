import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";

import type { WorkloadOut } from "../lib/api";
import { installFakeApi, jsonReply, textReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

function data(over: Partial<WorkloadOut> = {}): WorkloadOut {
  return {
    generated_at: "2026-10-28T10:00:00Z", period_days: 30, window_start: "2026-09-28T10:00:00Z", pending: 7,
    oldest_pending_at: "2026-10-10T10:00:00Z", oldest_pending_days: 18, pending_missing_timestamp: 0,
    waiting: [{ key: "D2", label: "Under 2 days", count: 2 }, { key: "D7", label: "2 to 6 days", count: 3 }, { key: "D14", label: "7 to 13 days", count: 1 }, { key: "D14P", label: "14 days or more", count: 1 }],
    sla: { configured: false, threshold_days: null, beyond: null, approaching: null, note: "No review deadline is configured, so no SLA breach is reported. Only elapsed waiting time is shown." },
    completed_total: 9,
    completed: [{ key: "ACCEPTED", label: "Accepted", count: 5 }, { key: "REJECTED", label: "Rejected", count: 1 }, { key: "CORRECTION_REQUESTED", label: "Returned for correction", count: 3 }],
    turnaround: { measured: 8, excluded: 1, average_hours: 40.5, median_hours: 30, note: "Time from the latest earlier submission to each decision taken in this period. Decisions with a missing timestamp are excluded and counted." },
    by_role: [{ role: "AICTE_APPROVER", count: 6 }, { role: "AICTE_REVIEWER", count: 3 }],
    by_person: null, mine: 3,
    distribution: [{ key: "SUBMITTED", label: "Awaiting review", count: 7 }, { key: "CORRECTION_REQUESTED", label: "Returned for correction", count: 2 }, { key: "ACCEPTED", label: "Accepted", count: 5 }, { key: "REJECTED", label: "Rejected", count: 1 }],
    assignment_tracked: false, assignment_note: "Assignment not tracked: reviews are not assigned to individual reviewers, so assigned and unassigned counts are not available.",
    queue_link: "/review", ...over,
  };
}

async function open(routes: Record<string, Handler | Handler[]> = {}, role = "AICTE_REVIEWER") {
  const api = installFakeApi({
    "GET /auth/me": jsonReply(200, makeSession(makeUser({ role }), "csrf-wl")),
    "GET /review/workload": jsonReply(200, data()),
    ...routes,
  });
  const view = renderApp("/workload");
  await screen.findByRole("navigation", { name: "Main" });
  return { api, view };
}

describe("workload dashboard", () => {
  it("shows the real figures from the server and explains their window", async () => {
    await open();
    expect(await screen.findByTestId("pending")).toHaveTextContent("7");
    expect(screen.getByTestId("oldest")).toHaveTextContent("18 days");
    expect(screen.getByTestId("completed")).toHaveTextContent("9");
    expect(screen.getByTestId("turnaround")).toHaveTextContent("30 hours");
    expect(screen.getByTestId("turnaround")).toHaveTextContent("Median of 8 reviews");
    expect(screen.getByText(/last 30 days/)).toBeInTheDocument();
    expect(screen.getByText(/1 decision was excluded for a missing timestamp/)).toBeInTheDocument();
  });

  it("states that assignment is not tracked and invents no assigned or unassigned counts", async () => {
    await open();
    const note = await screen.findByTestId("assignment");
    expect(note).toHaveTextContent("Assignment not tracked");
    expect(document.body.textContent).not.toMatch(/unassigned (queue|records)|assigned to you/i);
  });

  it("shows elapsed waiting time only when no threshold is configured", async () => {
    await open();
    const sla = await screen.findByTestId("sla");
    expect(sla).toHaveTextContent("No review deadline configured");
    expect(sla).not.toHaveTextContent("beyond the threshold");
  });

  it("shows a configured local threshold with beyond and approaching counts, labelled as local", async () => {
    await open({ "GET /review/workload": jsonReply(200, data({ sla: { configured: true, threshold_days: 5, beyond: 2, approaching: 1, note: "Against the configured threshold of 5 days (REVIEW_SLA_DAYS). It is a local setting, not an AICTE rule." } })) });
    const sla = await screen.findByTestId("sla");
    expect(sla).toHaveTextContent("Local threshold: 5 days");
    expect(sla).toHaveTextContent("2 beyond the threshold · 1 approaching it");
    expect(sla).toHaveTextContent("not an AICTE rule");
  });

  it("draws bars from the same counts, scaled to the largest, with the numbers written beside them", async () => {
    await open();
    const list = await screen.findByRole("list", { name: "Waiting time" });
    const bars = within(list).getAllByTestId("bar").map((b) => parseFloat((b.firstElementChild as HTMLElement).style.width));
    expect(bars).toEqual([(2 / 3) * 100, 100, (1 / 3) * 100, (1 / 3) * 100]);
    expect(within(list).getByText("14 days or more").nextElementSibling).toHaveTextContent("1");
  });

  it("links to the review queue", async () => {
    await open();
    expect(await screen.findByRole("link", { name: "Open the review queue" })).toHaveAttribute("href", "/review");
  });

  it("reloads for the chosen period", async () => {
    const { api } = await open();
    await screen.findByTestId("pending");
    await userEvent.click(screen.getByRole("button", { name: "90 days" }));
    await screen.findByTestId("pending");
    expect(api.callsTo("GET /review/workload").map((c) => c.query.days)).toEqual(["30", "90"]);
    expect(screen.getByRole("button", { name: "90 days" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows totals by role and the reviewer's own count, never names, to reviewers", async () => {
    await open();
    expect(await screen.findByTestId("mine")).toHaveTextContent("3");
    expect(screen.getByRole("list", { name: "Decisions by role" })).toHaveTextContent("AICTE approver");
    expect(screen.queryByRole("table", { name: "Decisions by person" })).not.toBeInTheDocument();
  });

  it("shows decisions by named person to administrators only, and no personal count", async () => {
    await open({ "GET /review/workload": jsonReply(200, data({ by_person: [{ name: "Meera Kulkarni", role: "AICTE_APPROVER", count: 6 }], mine: null })) }, "ADMIN");
    const table = await screen.findByRole("table", { name: "Decisions by person" });
    expect(table).toHaveTextContent("Meera Kulkarni");
    expect(screen.queryByTestId("mine")).not.toBeInTheDocument();
  });

  it("handles an empty queue and an empty period without inventing numbers", async () => {
    await open({
      "GET /review/workload": jsonReply(200, data({
        pending: 0, oldest_pending_at: null, oldest_pending_days: null, completed_total: 0,
        waiting: data().waiting.map((w) => ({ ...w, count: 0 })), completed: data().completed.map((c) => ({ ...c, count: 0 })),
        turnaround: { measured: 0, excluded: 0, average_hours: null, median_hours: null, note: "n" }, by_role: [], mine: 0,
        distribution: data().distribution.map((d) => ({ ...d, count: 0 })),
      })),
    });
    expect(await screen.findByTestId("pending")).toHaveTextContent("0");
    expect(screen.getByTestId("oldest")).toHaveTextContent("None");
    expect(screen.getByTestId("turnaround")).toHaveTextContent("Not available");
    expect(screen.getByText("No completed review could be measured.")).toBeInTheDocument();
    expect(screen.getByText("No decisions in this period.")).toBeInTheDocument();
  });

  it("mentions pending records that have no submission time", async () => {
    await open({ "GET /review/workload": jsonReply(200, data({ pending_missing_timestamp: 2 })) });
    expect(await screen.findByText(/2 pending FDRs have no submission time/)).toBeInTheDocument();
  });

  it("shows an error with retry rather than zeros", async () => {
    await open({ "GET /review/workload": [textReply(500), jsonReply(200, data())] });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not load the workload/i);
    expect(screen.queryByTestId("pending")).not.toBeInTheDocument();
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("pending")).toHaveTextContent("7");
  });

  it("is not offered to institution users and makes no request for them", async () => {
    const { api } = await open({}, "INSTITUTION_USER");
    expect(await screen.findByText(/not available to your role/i)).toBeInTheDocument();
    expect(api.callsTo("GET /review/workload")).toHaveLength(0);
    expect(within(screen.getByRole("navigation", { name: "Main" })).queryByRole("link", { name: "Workload" })).not.toBeInTheDocument();
  });

  it("is in the sidebar for reviewers and administrators", async () => {
    await open();
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Workload" })).toHaveAttribute("href", "/workload");
  });

  it("has no automated accessibility violations", async () => {
    const { view } = await open();
    await screen.findByTestId("pending");
    expect(await axe(view.container)).toHaveNoViolations();
  });
});
