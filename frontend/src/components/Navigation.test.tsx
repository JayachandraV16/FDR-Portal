import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { installFakeApi, jsonReply } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const EVERYONE = ["Overview", "Institutions", "FDRs", "Calendar", "Alerts", "Exceptions", "Release / Refund", "Reports", "Security"];

const EXPECTED: Record<string, string[]> = {
  INSTITUTION_USER: [...EVERYONE, "Documents", "Bulk import"],
  AICTE_REVIEWER: [...EVERYONE, "Review queue", "Workload", "Documents", "Verification"],
  AICTE_APPROVER: [...EVERYONE, "Review queue", "Workload", "Documents", "Verification"],
  ADMIN: [...EVERYONE, "Workload", "Administration"],
  AUDITOR: [...EVERYONE],
};

const HREF: Record<string, string> = {
  Calendar: "/calendar", "Bulk import": "/import", Security: "/security", Workload: "/workload", Documents: "/documents", Verification: "/verification", Alerts: "/alerts", Exceptions: "/exceptions",
  "Release / Refund": "/release", Reports: "/reports", Administration: "/admin",
};

describe("sidebar", () => {
  it.each(Object.keys(EXPECTED))("shows %s exactly the sections that role can use, all as real links", async (role) => {
    installFakeApi(
      { "GET /auth/me": jsonReply(200, makeSession(makeUser({ role, institution_id: role === "INSTITUTION_USER" ? "a0000000-0000-4000-8000-000000000001" : null }))) },
      { dashboardDefaults: true },
    );
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const labels = within(nav).getAllByRole("link").map((a) => a.textContent);
    expect([...labels].sort()).toEqual([...EXPECTED[role]].sort());
    expect(nav.textContent).not.toMatch(/not built/i);
    for (const [label, href] of Object.entries(HREF)) {
      if (EXPECTED[role].includes(label)) expect(within(nav).getByRole("link", { name: label })).toHaveAttribute("href", href);
    }
  });
});
