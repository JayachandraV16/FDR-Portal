import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { deferred, installFakeApi, jsonReply, networkError } from "../test/fakeApi";
import { TEMPORARY_USER, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const signedOut = jsonReply(401, { detail: "Not authenticated" });
const signedIn = (user = makeUser()) => jsonReply(200, makeSession(user));
const location = () => screen.getByTestId("location").textContent;
const waitForLocation = (path: string) => waitFor(() => expect(location()).toBe(path));

const loginHeading = () => screen.findByRole("heading", { name: "Sign in" });
const shellNav = () => screen.findByRole("navigation", { name: "Main" });

describe("unauthenticated visitors", () => {
  it.each(["/", "/change-password", "/institutions", "/some/deep/path"])("are redirected from %s to /login", async (path) => {
    const api = installFakeApi({ "GET /auth/me": signedOut });
    renderApp(path);
    expect(await loginHeading()).toBeInTheDocument();
    expect(location()).toBe("/login");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(api.callsTo("GET /auth/me")).toHaveLength(1); // no redirect loop re-checking the session
  });

  it("see nothing of the application while the session check is pending", async () => {
    const pending = deferred();
    installFakeApi({
      "GET /auth/me": async () => {
        await pending.promise;
        return signedOut({} as never) as Response;
      },
    });
    renderApp("/");
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    pending.resolve();
    expect(await loginHeading()).toBeInTheDocument();
  });

  it("can reach the login page without being redirected", async () => {
    const api = installFakeApi({ "GET /auth/me": signedOut });
    renderApp("/login");
    expect(await loginHeading()).toBeInTheDocument();
    expect(location()).toBe("/login");
    expect(api.callsTo("GET /auth/me")).toHaveLength(1);
  });
});

describe("authenticated users with a current password", () => {
  it("can open the protected application", async () => {
    installFakeApi({ "GET /auth/me": signedIn() });
    renderApp("/");
    expect(await shellNav()).toBeInTheDocument();
    expect(location()).toBe("/");
  });

  it("see the not-found page, inside the shell, for an unknown route", async () => {
    installFakeApi({ "GET /auth/me": signedIn() });
    renderApp("/does-not-exist");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(await shellNav()).toBeInTheDocument();
  });

  it("are sent from /login straight to the application", async () => {
    installFakeApi({ "GET /auth/me": signedIn() });
    renderApp("/login");
    expect(await shellNav()).toBeInTheDocument();
    expect(location()).toBe("/");
  });

  it("may voluntarily open the change-password page, without the forced-change wording", async () => {
    installFakeApi({ "GET /auth/me": signedIn() });
    renderApp("/change-password");
    expect(await screen.findByRole("heading", { name: "Change password" })).toBeInTheDocument();
    expect(location()).toBe("/change-password");
  });
});

describe("users who must change a temporary password", () => {
  it.each(["/", "/institutions", "/login"])("are redirected from %s to /change-password", async (path) => {
    const api = installFakeApi({ "GET /auth/me": signedIn(TEMPORARY_USER) });
    renderApp(path);
    expect(await screen.findByRole("heading", { name: "Set a new password" })).toBeInTheDocument();
    expect(location()).toBe("/change-password");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(api.callsTo("GET /auth/me")).toHaveLength(1);
  });

  it("settle on the change-password page without bouncing between routes", async () => {
    const visited: string[] = [];
    installFakeApi({ "GET /auth/me": signedIn(TEMPORARY_USER) });
    renderApp("/");
    await waitForLocation("/change-password");
    // Poll a little longer than a render cycle: the location must not move again.
    for (let i = 0; i < 5; i++) {
      visited.push(location()!);
      await new Promise((r) => setTimeout(r, 10));
    }
    expect(new Set(visited)).toEqual(new Set(["/change-password"]));
  });
});

describe("session-check failure", () => {
  it("shows a retry panel rather than the app or the login page, and recovers", async () => {
    installFakeApi({ "GET /auth/me": [networkError, signedIn()] });
    renderApp("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to verify your session");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await shellNav()).toBeInTheDocument();
  });
});

describe("returning to the requested page after sign-in", () => {
  it("lands on the originally requested route", async () => {
    installFakeApi({
      "GET /auth/me": signedOut,
      "POST /auth/login": jsonReply(200, makeSession()),
    });
    renderApp("/deep/link");
    await loginHeading();
    await userEvent.type(screen.getByLabelText("Email"), "officer@example.invalid");
    await userEvent.type(screen.getByLabelText("Password"), "a-long-enough-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(location()).toBe("/deep/link");
  });
});
