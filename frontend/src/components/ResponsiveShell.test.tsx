import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installFakeApi, jsonReply } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

async function open(role = "AICTE_REVIEWER", path = "/") {
  installFakeApi({ "GET /auth/me": jsonReply(200, makeSession(makeUser({ role }), "csrf-shell")) });
  renderApp(path);
  await screen.findByRole("navigation", { name: "Main" });
}

describe("responsive navigation", () => {
  it("has one sidebar landmark, and a menu button that opens and closes it", async () => {
    await open();
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
    const button = screen.getByRole("button", { name: "Open navigation menu" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", "app-sidebar");
    await userEvent.click(button);
    const close = screen.getByRole("button", { name: "Close navigation menu" });
    expect(close).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("nav-overlay")).toBeInTheDocument();
    await userEvent.click(close);
    expect(screen.queryByTestId("nav-overlay")).not.toBeInTheDocument();
  });

  it("closes when the overlay is clicked, on Escape, and after choosing a page", async () => {
    await open();
    const menu = () => screen.getByRole("button", { name: /navigation menu/ });
    await userEvent.click(menu());
    await userEvent.click(screen.getByTestId("nav-overlay"));
    expect(menu()).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(menu());
    await userEvent.keyboard("{Escape}");
    expect(menu()).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(menu());
    await userEvent.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Alerts" }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/alerts");
    expect(menu()).toHaveAttribute("aria-expanded", "false");
  });

  it("marks the current page, links to every section, and has a skip link to the main content", async () => {
    await open("AICTE_REVIEWER", "/alerts");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Alerts" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    for (const name of ["Documents", "Verification", "Exceptions", "Release / Refund", "Reports"]) {
      expect(within(nav).getByRole("link", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("shows Administration only to administrators", async () => {
    await open("ADMIN");
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Administration" })).toBeInTheDocument();
  });

  it("does not show Administration or reviewer sections to an institution user", async () => {
    await open("INSTITUTION_USER");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Administration" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Verification" })).not.toBeInTheDocument();
  });
});
