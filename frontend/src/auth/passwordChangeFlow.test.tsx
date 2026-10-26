import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installFakeApi, jsonReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const TEMP = "temporary-password-1";
const NEW = "a-brand-new-passphrase";

/**
 * A tiny stateful stand-in for the backend's auth endpoints, following the real contract:
 * a successful password change clears must_change_password and revokes the session (the cookie is
 * gone, so /auth/me answers 401 until the next login).
 */
function statefulBackend({ startsTemporary }: { startsTemporary: boolean }) {
  let password = startsTemporary ? TEMP : NEW;
  let mustChange = startsTemporary;
  let signedIn = false;
  const user = () => makeUser({ role: "ADMIN", full_name: "First Administrator", must_change_password: mustChange });
  const session = () => jsonReply(200, makeSession(user(), "csrf-flow"));

  const login: Handler = (req) => {
    if ((req.body as { password: string }).password !== password) return jsonReply(401, { detail: "Invalid credentials" })(req);
    signedIn = true;
    return session()(req);
  };
  const me: Handler = (req) => (signedIn ? session()(req) : jsonReply(401, { detail: "Not authenticated" })(req));
  const change: Handler = (req) => {
    if (!signedIn) return jsonReply(401, { detail: "Not authenticated" })(req);
    const body = req.body as { current_password: string; new_password: string };
    if (body.current_password !== password) return jsonReply(400, { detail: "Current password is incorrect" })(req);
    password = body.new_password;
    mustChange = false;
    signedIn = false; // every session of the user is revoked
    return jsonReply(200, { detail: "Password changed. Please sign in again." })(req);
  };
  const logout: Handler = (req) => {
    signedIn = false;
    return jsonReply(200, { detail: "Signed out" })(req);
  };
  return installFakeApi({
    "POST /auth/login": login,
    "GET /auth/me": me,
    "POST /auth/change-password": change,
    "POST /auth/logout": logout,
  });
}

const location = () => screen.getByTestId("location").textContent;

async function signIn(password: string) {
  await userEvent.clear(screen.getByLabelText("Email"));
  await userEvent.type(screen.getByLabelText("Email"), "admin@example.invalid");
  await userEvent.type(screen.getByLabelText("Password"), password);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

async function changePassword(current: string, next: string, currentLabel: RegExp) {
  for (const [label, value] of [[currentLabel, current], [/^new password$/i, next], [/^confirm new password$/i, next]] as const) {
    await userEvent.click(screen.getByLabelText(label));
    await userEvent.paste(value);
  }
  await userEvent.click(screen.getByRole("button", { name: "Update password" }));
}

describe("temporary password -> new password -> application", () => {
  it("lets the user into the application after the forced change, instead of the change-password page again", async () => {
    const api = statefulBackend({ startsTemporary: true });
    renderApp("/");

    // 1-2. Sign in with the temporary password: the change is required.
    await screen.findByRole("heading", { name: "Sign in" });
    await signIn(TEMP);
    await screen.findByRole("heading", { name: "Set a new password" });

    // 3-5. Change it; the backend revokes the session, so the user must sign in again.
    await changePassword(TEMP, NEW, /^temporary password$/i);
    await screen.findByRole("heading", { name: "Sign in" });
    expect(location()).toBe("/login");

    // 6-7. Sign in with the new password: must_change_password is now false.
    await signIn(NEW);
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(location()).toBe("/");
    expect(screen.queryByRole("heading", { name: /password/i })).not.toBeInTheDocument();
    expect(api.callsTo("POST /auth/change-password")).toHaveLength(1);
  });

  it("also lands on the application after a voluntary password change", async () => {
    statefulBackend({ startsTemporary: false });
    renderApp("/change-password");

    await screen.findByRole("heading", { name: "Sign in" }); // not signed in yet
    await signIn(NEW);
    // The auth screen is not remembered as a place to return to: sign-in leads to the application.
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(location()).toBe("/");
    await userEvent.click(screen.getByRole("link", { name: "Change password" }));
    await screen.findByRole("heading", { name: "Change password" });
    await changePassword(NEW, "yet-another-passphrase-9", /^current password$/i);
    await screen.findByRole("heading", { name: "Sign in" });

    await signIn("yet-another-passphrase-9");
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(location()).toBe("/");
  });

  it("still returns to an ordinary page that required sign-in", async () => {
    statefulBackend({ startsTemporary: false });
    renderApp("/reports");
    await screen.findByRole("heading", { name: "Sign in" });
    await signIn(NEW);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(location()).toBe("/reports");
  });

  it("does not send a user who signed out of the change-password page back to it", async () => {
    statefulBackend({ startsTemporary: true });
    renderApp("/");
    await screen.findByRole("heading", { name: "Sign in" });
    await signIn(TEMP);
    await screen.findByRole("heading", { name: "Set a new password" });
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });
    await signIn(TEMP);
    // Still temporary, so the forced screen is correct here, once, without bouncing.
    await screen.findByRole("heading", { name: "Set a new password" });
    expect(location()).toBe("/change-password");
  });
});
