import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, expect, vi } from "vitest";

import { unroutedRequests } from "./fakeApi";
import { setCsrfToken, setUnauthorizedHandler } from "../lib/api";

afterEach(() => {
  // The auth design forbids persisting credentials or tokens in browser storage; enforce it for
  // every test, not just the ones that think of checking.
  expect(localStorage.length, "localStorage must stay empty").toBe(0);
  expect(sessionStorage.length, "sessionStorage must stay empty").toBe(0);
  expect(unroutedRequests.splice(0), "requests with no fake route").toEqual([]);
  cleanup();
  vi.unstubAllGlobals();
  setCsrfToken(null);
  setUnauthorizedHandler(null);
});
