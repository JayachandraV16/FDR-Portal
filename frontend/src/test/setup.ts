import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, expect, vi } from "vitest";

import { unroutedRequests } from "./fakeApi";
import { setCsrfToken, setUnauthorizedHandler } from "../lib/api";
import { LANGUAGE_STORAGE_KEY } from "../lib/chatText";
import { setThemePreference, THEME_KEY } from "../lib/theme";

afterEach(() => {
  // Reset first, so a failing check below can never leave a mounted page behind for the next test.
  cleanup();
  vi.unstubAllGlobals();
  setCsrfToken(null);
  setUnauthorizedHandler(null);
  const unrouted = unroutedRequests.splice(0);

  // The auth design forbids persisting credentials or tokens in browser storage; enforce it for
  // every test, not just the ones that think of checking. The one exception is the colour-theme
  // preference, which is non-sensitive by design; it is checked for, then reset so tests stay independent.
  const allowedKeys = [THEME_KEY, LANGUAGE_STORAGE_KEY]; // two non-sensitive keywords: colour theme and Copilot language
  expect(Object.keys(localStorage).filter((key) => !allowedKeys.includes(key)), "localStorage must hold nothing but the theme and language preferences").toEqual([]);
  expect(["light", "dark", "system", null], "the theme preference must be a plain keyword").toContain(localStorage.getItem(THEME_KEY));
  setThemePreference("system");
  localStorage.removeItem(THEME_KEY);
  localStorage.removeItem(LANGUAGE_STORAGE_KEY);
  expect(sessionStorage.length, "sessionStorage must stay empty").toBe(0);
  expect(unrouted, "requests with no fake route").toEqual([]);
});

// Accessibility assertions (vitest-axe). They catch structural problems in jsdom (names, roles, labels, ARIA use);
// they cannot judge colour contrast or keyboard and screen-reader behaviour, so they supplement manual checks.
import * as axeMatchers from "vitest-axe/matchers";
import type { AxeMatchers } from "vitest-axe/matchers";

expect.extend(axeMatchers);
declare module "vitest" {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface Assertion extends AxeMatchers {}
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface AsymmetricMatchersContaining extends AxeMatchers {}
}

// axe-core probes canvas support for an icon-font check; jsdom has none and says so on stderr. The probe's
// result is irrelevant here (nothing in the app uses icon fonts), so give it an inert answer to keep output clean.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
