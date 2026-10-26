import { describe, expect, it, vi } from "vitest";

import {
  ApiError,
  changePassword,
  fetchHealth,
  fetchSession,
  login,
  logout,
  setCsrfToken,
  setUnauthorizedHandler,
} from "./api";
import { installFakeApi, jsonReply, networkError, textReply } from "../test/fakeApi";
import { PASSWORD, makeSession } from "../test/fixtures";

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (err) {
    return err;
  }
  throw new Error("expected the promise to reject");
}

describe("successful requests", () => {
  it("returns parsed JSON and sends cookies for same-origin requests only", async () => {
    const session = makeSession();
    const api = installFakeApi({ "GET /auth/me": jsonReply(200, session) });
    await expect(fetchSession()).resolves.toEqual(session);
    const [call] = api.calls;
    expect(call.url).toBe("/api/v1/auth/me");
    expect(call.credentials).toBe("same-origin");
    expect(call.headers.accept).toBe("application/json");
    expect(call.headers["content-type"]).toBeUndefined();
  });

  it("sends the documented login and change-password bodies and nothing else", async () => {
    const api = installFakeApi({
      "POST /auth/login": jsonReply(200, makeSession()),
      "POST /auth/change-password": jsonReply(200, { detail: "Password changed. Please sign in again." }),
    });
    await login("a@example.invalid", PASSWORD);
    await changePassword("old-password-value", "new-password-value");
    expect(api.calls[0].body).toEqual({ email: "a@example.invalid", password: PASSWORD });
    expect(api.calls[0].headers["content-type"]).toBe("application/json");
    expect(api.calls[1].body).toEqual({ current_password: "old-password-value", new_password: "new-password-value" });
  });
});

describe("CSRF header", () => {
  it("is added to unsafe requests once a token is known, and only then", async () => {
    const api = installFakeApi({
      "POST /auth/logout": jsonReply(200, { detail: "Signed out" }),
      "GET /auth/me": jsonReply(200, makeSession()),
    });
    await logout();
    setCsrfToken("token-1");
    await logout();
    await fetchSession(); // safe method: never carries it
    setCsrfToken(null);
    await logout();
    expect(api.calls.map((c) => c.headers["x-csrf-token"])).toEqual([undefined, "token-1", undefined, undefined]);
  });
});

describe("errors", () => {
  it.each([
    [401, "unauthorized", "Invalid credentials"],
    [403, "forbidden", "Untrusted request origin"],
    [400, "client", "Current password is incorrect"],
    [404, "client", "Not found"],
    [500, "server", "boom"],
  ] as const)("maps HTTP %i with a JSON detail to a %s error", async (status, kind, detail) => {
    installFakeApi({ "POST /auth/login": jsonReply(status, { detail }) });
    const err = (await rejection(login("a@example.invalid", PASSWORD))) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ kind, status, message: detail });
  });

  it("treats a FastAPI validation-error list as a generic validation error", async () => {
    installFakeApi({ "POST /auth/login": jsonReply(422, { detail: [{ loc: ["body", "email"], msg: "field required" }] }) });
    const err = (await rejection(login("", ""))) as ApiError;
    expect(err).toMatchObject({ kind: "validation", status: 422, message: "The request was not valid" });
  });

  it.each([
    ["an empty body (the dev proxy's bare 500)", textReply(500)],
    ["a plain-text body", textReply(502, "Bad gateway")],
    ["an HTML body", textReply(503, "<html>down</html>", "text/html")],
  ])("copes with %s", async (_label, handler) => {
    installFakeApi({ "GET /auth/me": handler });
    const err = (await rejection(fetchSession())) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe("server");
    expect(err.message).toMatch(/^Server error \(HTTP 50\d\)$/);
  });

  it("reports a network failure as a typed error with status 0", async () => {
    installFakeApi({ "GET /auth/me": networkError });
    const err = (await rejection(fetchSession())) as ApiError;
    expect(err).toMatchObject({ kind: "network", status: 0 });
  });

  it("lets an aborted request surface as AbortError rather than a network error", async () => {
    installFakeApi({ "GET /auth/me": () => Promise.reject(new DOMException("Aborted", "AbortError")) });
    const err = await rejection(fetchSession());
    expect(err).toBeInstanceOf(DOMException);
    expect((err as DOMException).name).toBe("AbortError");
  });
});

describe("401 handling", () => {
  it("notifies the session-expired handler for an ordinary authenticated request", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    installFakeApi({ "POST /auth/change-password": jsonReply(401, { detail: "Not authenticated" }) });
    await rejection(changePassword("a", "b"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("leaves 401 to the caller for login, session check and logout", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    installFakeApi({
      "POST /auth/login": jsonReply(401, { detail: "Invalid credentials" }),
      "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }),
      "POST /auth/logout": jsonReply(401, { detail: "Not authenticated" }),
    });
    await rejection(login("a@example.invalid", PASSWORD));
    await rejection(fetchSession());
    await rejection(logout());
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not notify for other error statuses", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    installFakeApi({ "POST /auth/change-password": jsonReply(400, { detail: "Current password is incorrect" }) });
    await rejection(changePassword("a", "b"));
    expect(handler).not.toHaveBeenCalled();
  });
});

describe("fetchHealth", () => {
  it("returns the health payload", async () => {
    installFakeApi({});
    await expect(fetchHealth()).resolves.toMatchObject({ status: "ok" });
  });

  it("keeps the 'Health check failed with HTTP n' wording for HTTP errors", async () => {
    installFakeApi({ "GET /health": textReply(500) });
    await expect(fetchHealth()).rejects.toThrow("Health check failed with HTTP 500");
  });

  it("reports an unreachable server as a network error", async () => {
    installFakeApi({ "GET /health": networkError });
    const err = (await rejection(fetchHealth())) as ApiError;
    expect(err.kind).toBe("network");
  });
});

describe("sensitive data", () => {
  it("never logs passwords, CSRF tokens or cookies, even when requests fail", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    installFakeApi({
      "POST /auth/login": [jsonReply(401, { detail: "Invalid credentials" }), networkError, textReply(500)],
      "POST /auth/change-password": jsonReply(400, { detail: "Current password is incorrect" }),
    });
    setCsrfToken("csrf-secret-value");
    for (let i = 0; i < 3; i++) await rejection(login("a@example.invalid", PASSWORD));
    await rejection(changePassword(PASSWORD, "another-long-passphrase"));
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it("keeps secrets out of error messages", async () => {
    installFakeApi({ "POST /auth/login": [networkError, textReply(500)] });
    setCsrfToken("csrf-secret-value");
    for (let i = 0; i < 2; i++) {
      const err = (await rejection(login("a@example.invalid", PASSWORD))) as ApiError;
      expect(err.message + String(err.stack)).not.toContain(PASSWORD);
      expect(err.message).not.toContain("csrf-secret-value");
    }
  });

  it("does not put the password or CSRF token in the URL", async () => {
    const api = installFakeApi({ "POST /auth/login": jsonReply(200, makeSession()) });
    setCsrfToken("csrf-secret-value");
    await login("a@example.invalid", PASSWORD);
    expect(api.calls[0].url).toBe("/api/v1/auth/login");
  });
});
