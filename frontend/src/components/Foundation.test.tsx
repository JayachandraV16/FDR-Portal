import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
/// <reference types="vite/client" />
import { afterEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

import favicon from "../../public/favicon.svg?raw";
import indexHtml from "../../index.html?raw";
import indexCss from "../index.css?raw";

import { getThemePreference, resolveTheme, setThemePreference, THEME_KEY } from "../lib/theme";
import { installFakeApi, jsonReply } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";
import { Icon } from "./icons";
import { LogoMark, Wordmark } from "./brand";
import ThemeToggle from "./ThemeToggle";

const root = () => document.documentElement;

afterEach(() => {
  root().classList.remove("dark");
});

describe("theme preference", () => {
  it("defaults to the system setting and follows it", () => {
    expect(getThemePreference()).toBe("system");
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("dark"), addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    expect(resolveTheme("system")).toBe("dark");
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    expect(resolveTheme("system")).toBe("light");
    expect(resolveTheme("dark")).toBe("dark");
    expect(resolveTheme("light")).toBe("light");
  });

  it("applies the dark class and colour-scheme, and stores only the keyword", () => {
    setThemePreference("dark");
    expect(root()).toHaveClass("dark");
    expect(root().style.colorScheme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    setThemePreference("light");
    expect(root()).not.toHaveClass("dark");
    expect(root().style.colorScheme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("still applies the choice when browser storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    setThemePreference("dark");
    expect(root()).toHaveClass("dark");
  });
});

describe("theme toggle", () => {
  it("is a labelled radio group with three options and reflects the current choice", async () => {
    render(<ThemeToggle />);
    const group = screen.getByRole("radiogroup", { name: "Colour theme" });
    const options = within(group).getAllByRole("radio");
    expect(options.map((o) => o.getAttribute("value"))).toEqual(["light", "dark", "system"]);
    expect(within(group).getByRole("radio", { name: "System" })).toBeChecked();
    await userEvent.click(within(group).getByRole("radio", { name: "Dark" }));
    expect(within(group).getByRole("radio", { name: "Dark" })).toBeChecked();
    expect(root()).toHaveClass("dark");
  });

  it("is operable from the keyboard with arrow keys", async () => {
    render(<ThemeToggle />);
    await userEvent.tab();
    expect(screen.getByRole("radio", { name: "System" })).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
    expect(root()).toHaveClass("dark");
  });

  it("persists across a reload of the page state", () => {
    setThemePreference("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
  });
});

describe("branding and icons", () => {
  it("hides decorative icons and the logo mark from assistive technology", () => {
    const { container } = render(<><Icon name="alerts" /><LogoMark /></>);
    for (const svg of container.querySelectorAll("svg")) {
      expect(svg).toHaveAttribute("aria-hidden", "true");
      expect(svg).toHaveAttribute("focusable", "false");
    }
  });

  it("renders the product name as text, so it is read and searchable", () => {
    render(<Wordmark />);
    expect(screen.getByText("FDR & Security Deposit Portal")).toBeInTheDocument();
  });

  it("shows the logo and an icon for every navigation item in the shell", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(200, makeSession(makeUser({ role: "ADMIN" }))) });
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const link of within(nav).getAllByRole("link")) {
      expect(link.querySelector("svg"), `${link.textContent} has an icon`).not.toBeNull();
    }
    expect(within(screen.getByRole("complementary")).getByText("FDR & Security Deposit Portal")).toBeInTheDocument();
  });

  it("ships a favicon and links it", () => {
    expect(favicon).toContain("<svg");
    expect(indexHtml).toContain('rel="icon"');
  });

  it("uses no emoji or pictographic decoration anywhere in the interface source", () => {
    const pictographic = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B50}]/u;
    const sources = import.meta.glob(["../**/*.ts", "../**/*.tsx", "!../**/*.test.*", "!../test/**"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    expect(Object.keys(sources).length).toBeGreaterThan(20); // the scan really looked at the sources
    const offenders = Object.entries(sources).flatMap(([path, text]) =>
      text.split("\n").flatMap((line, i) => (pictographic.test(line) ? [`${path}:${i + 1}`] : [])),
    );
    expect(offenders).toEqual([]);
  });

  it("keeps theme tokens central: the dark theme redefines palette variables rather than overriding components", () => {
    const css = indexCss;
    expect(css).toContain(".dark {");
    expect(css).toContain("--color-white:");
    expect(css).toContain("prefers-reduced-motion");
    expect(css).toContain(":focus-visible");
    expect(css).not.toMatch(/https?:\/\//); // no external font or asset requests
  });
});

describe("automated accessibility checks (structure only)", () => {
  it("finds no violations in the signed-in shell, including the theme toggle", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(200, makeSession(makeUser({ role: "AICTE_REVIEWER" }))) });
    const { container } = renderApp("/");
    await screen.findByRole("navigation", { name: "Main" });
    await screen.findByRole("heading", { name: "Overview" });
    expect(await axe(container)).toHaveNoViolations();
  });

  it("finds no violations in the sign-in page", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }) }, { dashboardDefaults: false });
    const { container } = renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in" });
    expect(await axe(container)).toHaveNoViolations();
  });

  it("finds no violations with the Copilot panel open", async () => {
    installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession(makeUser())),
      "GET /chat/suggestions": jsonReply(200, { suggestions: ["How do I create an FDR?"] }),
    });
    const { container } = renderApp("/");
    await screen.findByRole("navigation", { name: "Main" });
    await userEvent.click(screen.getByRole("button", { name: "Open FDR Copilot" }));
    await screen.findByRole("dialog", { name: "FDR Copilot" });
    expect(await axe(container)).toHaveNoViolations();
  });
});
