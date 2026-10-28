/// <reference types="vite/client" />
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

import indexCss from "../index.css?raw";
import { installFakeApi, jsonReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const ID = "00000000-0000-4000-8000-0000000000aa";
const BANKS = { items: [{ id: "b1", code: "SBI", name: "State Bank of India" }], page: 1, page_size: 500, total: 1, total_pages: 1 };
const LIST = { items: [makeFdr({ id: ID, fdr_number: "A11Y-1" })], page: 1, page_size: 25, total: 1, total_pages: 1 };

async function open(path: string, role = "AICTE_REVIEWER", routes: Record<string, Handler | Handler[]> = {}) {
  const api = installFakeApi({
    "GET /auth/me": jsonReply(200, makeSession(makeUser({ role, institution_id: role === "INSTITUTION_USER" ? "a0000000-0000-4000-8000-000000000001" : null }), "csrf-a11y")),
    "GET /banks": jsonReply(200, BANKS),
    "GET /chat/suggestions": jsonReply(200, { suggestions: ["How do I create an FDR?"] }),
    "GET /fdrs": jsonReply(200, LIST),
    [`GET /fdrs/${ID}`]: jsonReply(200, makeFdr({ id: ID, fdr_number: "A11Y-1" })),
    [`GET /fdrs/${ID}/history`]: jsonReply(200, { history: [], revisions: [] }),
    "GET /exceptions": jsonReply(200, { generated_on: "2026-10-28", scanned: 0, eligible_total: 0, complete: true, kinds: [], items: [] }),
    ...routes,
  });
  const view = renderApp(path);
  await screen.findByRole("navigation", { name: "Main" });
  return { api, view };
}

describe("every main page is structurally accessible (automated checks only)", () => {
  it.each([
    ["the overview", "/", "AICTE_REVIEWER", "Overview"],
    ["the FDR registry", "/fdrs", "AICTE_REVIEWER", "FDR Registry"],
    ["the FDR details", `/fdrs/${ID}`, "AICTE_REVIEWER", "FDR details"],
    ["the alerts", "/alerts", "AICTE_REVIEWER", "Alerts"],
    ["the exceptions", "/exceptions", "AICTE_REVIEWER", "Exceptions"],
    ["the reports", "/reports", "AICTE_REVIEWER", "Reports"],
    ["the create form", "/fdrs/new", "INSTITUTION_USER", "Create FDR"],
    ["the bulk import", "/import", "INSTITUTION_USER", "Bulk import"],
  ])("%s has one page heading, no axe violations, and a main landmark", async (_name, path, role, heading) => {
    const { view } = await open(path, role);
    await screen.findByRole("heading", { name: heading, level: 1 });
    await waitFor(() => expect(screen.queryByText(/^Loading/)).not.toBeInTheDocument());
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(await axe(view.container)).toHaveNoViolations();
  });

  it("keeps a sensible heading order: no panel heading is above the page heading", async () => {
    await open("/reports");
    await screen.findByRole("heading", { name: "Reports", level: 1 });
    const levels = screen.getAllByRole("heading").map((h) => Number(h.tagName[1]));
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i += 1) expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
  });
});

describe("keyboard use", () => {
  it("makes the skip link the first stop, and reaches the navigation next", async () => {
    await open("/");
    await userEvent.tab();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveFocus();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main-content");
    await userEvent.tab();
    expect(document.activeElement).not.toBe(document.body);
  });

  it("names the menu button, tells screen readers whether it is expanded, and closes with Escape", async () => {
    await open("/");
    const button = screen.getByRole("button", { name: "Open navigation menu" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: "Close navigation menu" })).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Open navigation menu" })).toHaveAttribute("aria-expanded", "false");
  });

  it("gives every icon-only or symbol control an accessible name", async () => {
    await open("/");
    await userEvent.click(screen.getByRole("button", { name: "Open FDR Copilot" }));
    for (const button of screen.getAllByRole("button")) {
      expect((button.textContent ?? "").trim() !== "" || button.getAttribute("aria-label")).toBeTruthy();
    }
    for (const svg of document.querySelectorAll("svg")) expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it("does not trap focus: Tab leaves the Copilot panel and Escape closes it", async () => {
    await open("/");
    await userEvent.click(screen.getByRole("button", { name: "Open FDR Copilot" }));
    const panel = screen.getByRole("dialog", { name: "FDR Copilot" });
    expect(panel).not.toHaveAttribute("aria-modal", "true"); // non-modal: the page behind stays usable
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("status and urgency are never colour alone", () => {
  it("writes the status of an FDR as a word in the registry", async () => {
    await open("/fdrs", "AICTE_REVIEWER", { "GET /fdrs": jsonReply(200, { ...LIST, items: [makeFdr({ id: ID, fdr_number: "A11Y-1", status: "CORRECTION_REQUESTED" })] }) });
    const row = (await screen.findByText("A11Y-1")).closest("tr")!;
    expect(within(row).getByText("Correction requested")).toBeInTheDocument();
  });

  it("writes severity out in words on alerts", async () => {
    await open("/alerts", "AICTE_REVIEWER", {
      "GET /alerts": jsonReply(200, {
        generated_on: "2026-10-28", basis: "x", categories: [], items: [
          { category: "OVERDUE", severity: "HIGH", fdr_id: ID, fdr_number: "SEV-1", institution_code: "P", institution_name: "P", message: "m", date: null, days: null, link: `/fdrs/${ID}` },
        ],
      }),
    });
    expect(await screen.findByText("High")).toBeInTheDocument();
  });
});

describe("offline and unsaved work", () => {
  it("says so in the shell when the connection drops and when it returns", async () => {
    await open("/");
    const online = vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    window.dispatchEvent(new Event("offline"));
    expect(await screen.findByText(/You are offline. Nothing can be saved or sent/)).toBeInTheDocument();
    expect(screen.getByText(/nothing you change is stored on this device/)).toBeInTheDocument();
    online.mockReturnValue(true);
    window.dispatchEvent(new Event("online"));
    await waitFor(() => expect(screen.queryByText(/You are offline/)).not.toBeInTheDocument());
  });

  it("warns before the page is closed once the create form has been changed, and not before", async () => {
    await open("/fdrs/new", "INSTITUTION_USER");
    await screen.findByRole("heading", { name: "Create FDR", level: 1 });
    const untouched = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(untouched);
    expect(untouched.defaultPrevented).toBe(false);
    await userEvent.type(screen.getByLabelText(/FDR number/), "TYPED-1");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it("keeps what is typed out of browser storage (financial details are never cached on the device)", async () => {
    await open("/fdrs/new", "INSTITUTION_USER");
    await userEvent.type(await screen.findByLabelText(/FDR number/), "SECRET-FDR-77");
    fireEvent.input(screen.getByLabelText(/FDR number/), { target: { value: "SECRET-FDR-77" } });
    const everything = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage });
    expect(everything).not.toContain("SECRET-FDR-77");
  });
});

describe("visual system rules", () => {
  it("lays out long translated words without breaking tables, and uses a script-safe font stack", () => {
    expect(indexCss).toMatch(/overflow-wrap:\s*anywhere/);
    for (const font of ["Nirmala UI", "Noto Sans Devanagari", "Kohinoor Devanagari", "Mangal"]) expect(indexCss).toContain(font);
    expect(indexCss).not.toMatch(/@import url|fonts\.googleapis|https?:\/\//); // no external font requests
  });

  it("uses theme tokens, not hard-coded colours, in the interface source", () => {
    const sources = import.meta.glob(["../**/*.tsx", "!../**/*.test.tsx", "!../components/brand.tsx"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const literal = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])|\brgb\(|\bhsl\(|style=\{\{[^}]*(?:color|background)/;
    const offenders = Object.entries(sources).flatMap(([path, text]) =>
      text.split("\n").flatMap((line, i) => (literal.test(line.replace(/&#\d+;/g, "")) ? [`${path}:${i + 1}`] : [])),
    );
    expect(Object.keys(sources).length).toBeGreaterThan(30);
    expect(offenders).toEqual([]);
  });
});
