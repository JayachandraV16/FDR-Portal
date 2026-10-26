import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installFakeApi, jsonReply, networkError } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

async function openShell(user = makeUser(), routes: Parameters<typeof installFakeApi>[0] = {}) {
  const api = installFakeApi({ "GET /auth/me": jsonReply(200, makeSession(user, "csrf-shell")), ...routes });
  renderApp("/");
  await screen.findByRole("navigation", { name: "Main" });
  return api;
}

describe("signed-in header", () => {
  it.each([
    ["INSTITUTION_USER", "Institution user"],
    ["AICTE_REVIEWER", "AICTE reviewer"],
    ["AICTE_APPROVER", "AICTE approver"],
    ["ADMIN", "Administrator"],
    ["AUDITOR", "Auditor"],
  ])("shows the backend's name, email and the label for role %s", async (role, label) => {
    await openShell(makeUser({ role, full_name: "Priya Nair", email: "priya@example.invalid" }));
    const header = screen.getByRole("banner");
    expect(within(header).getByText("Priya Nair")).toBeInTheDocument();
    expect(header).toHaveTextContent(`${label} · priya@example.invalid`);
  });

  it("falls back to the raw role code for a role the UI does not know", async () => {
    await openShell(makeUser({ role: "NEW_ROLE" }));
    expect(screen.getByRole("banner")).toHaveTextContent("NEW_ROLE");
  });

  it("keeps the existing navigation and the demonstration-data notice", async () => {
    await openShell();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(within(nav).getByText("FDRs")).toBeInTheDocument();
    expect(screen.getByRole("banner")).toHaveTextContent(/demonstration data only/i);
  });

  it("links to the voluntary change-password page", async () => {
    await openShell();
    await userEvent.click(screen.getByRole("link", { name: "Change password" }));
    expect(await screen.findByRole("heading", { name: "Change password" })).toBeInTheDocument();
  });
});

describe("signing out", () => {
  it("calls the backend with the CSRF token and returns to the login page", async () => {
    const api = await openShell(makeUser(), { "POST /auth/logout": jsonReply(200, { detail: "Signed out" }) });
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(api.callsTo("POST /auth/logout")[0].headers["x-csrf-token"]).toBe("csrf-shell");
    expect(screen.getByTestId("location")).toHaveTextContent("/login");
  });

  it("stays signed in and says so when sign-out fails", async () => {
    await openShell(makeUser(), { "POST /auth/logout": networkError });
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign-out failed");
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });
});
