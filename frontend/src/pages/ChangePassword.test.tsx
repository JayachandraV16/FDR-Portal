import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { deferred, installFakeApi, jsonReply, networkError, textReply } from "../test/fakeApi";
import { TEMPORARY_USER, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const TEMP = "temporary-password-1";
const GOOD = "a-brand-new-passphrase";
const CSRF = "csrf-for-change-password";
const OK = jsonReply(200, { detail: "Password changed. Please sign in again." });

async function openForcedChange(routes: Parameters<typeof installFakeApi>[0] = {}, user = TEMPORARY_USER) {
  const api = installFakeApi({ "GET /auth/me": jsonReply(200, makeSession(user, CSRF)), ...routes });
  renderApp("/change-password");
  await screen.findByRole("heading", { name: user.must_change_password ? "Set a new password" : "Change password" });
  return api;
}

const currentField = () => screen.getByLabelText(/^(temporary|current) password$/i);
const newField = () => screen.getByLabelText("New password");
const confirmField = () => screen.getByLabelText("Confirm new password");
const submit = () => screen.getByRole("button", { name: /update password|updating password/i });

async function paste(field: HTMLElement, value: string) {
  await userEvent.click(field);
  await userEvent.paste(value);
}
async function fill(current = TEMP, next = GOOD, confirm = next) {
  await paste(currentField(), current);
  await paste(newField(), next);
  await paste(confirmField(), confirm);
}
const alertText = async () => (await screen.findByRole("alert")).textContent ?? "";

describe("forced password change screen", () => {
  it("renders the three password fields and explains why it is shown", async () => {
    await openForcedChange();
    expect(currentField()).toHaveAttribute("type", "password");
    expect(newField()).toHaveAttribute("type", "password");
    expect(confirmField()).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Temporary password")).toBeInTheDocument();
    expect(screen.getByText(/must change your password before using the portal/i)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("uses 'Current password' wording for a voluntary change", async () => {
    await openForcedChange({}, makeUser());
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
    expect(screen.queryByText(/must change your password/i)).not.toBeInTheDocument();
  });

  it("lets the user sign out instead", async () => {
    const api = await openForcedChange({ "POST /auth/logout": jsonReply(200, { detail: "Signed out" }) });
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(api.callsTo("POST /auth/logout")[0].headers["x-csrf-token"]).toBe(CSRF);
  });
});

describe("client-side validation (the API is not called)", () => {
  it("rejects a confirmation that does not match", async () => {
    const api = await openForcedChange();
    await fill(TEMP, GOOD, GOOD + "x");
    await userEvent.click(submit());
    expect(await alertText()).toMatch(/do not match/i);
    expect(api.callsTo("POST /auth/change-password")).toHaveLength(0);
  });

  it.each([
    ["empty", ""],
    ["11 characters", "a".repeat(11)],
    ["129 characters", "a".repeat(129)],
  ])("rejects a new password that is %s", async (_label, value) => {
    const api = await openForcedChange();
    await fill(TEMP, value, value);
    await userEvent.click(submit());
    expect(await alertText()).toMatch(/between 12 and 128 characters/i);
    expect(api.callsTo("POST /auth/change-password")).toHaveLength(0);
  });

  it("rejects a new password equal to the current one", async () => {
    const api = await openForcedChange();
    await fill("same-long-passphrase", "same-long-passphrase");
    await userEvent.click(submit());
    expect(await alertText()).toMatch(/must be different from the current password/i);
    expect(api.callsTo("POST /auth/change-password")).toHaveLength(0);
  });

  it.each([12, 128])("accepts a new password of exactly %i characters", async (length) => {
    const api = await openForcedChange({ "POST /auth/change-password": OK });
    await fill(TEMP, "n".repeat(length));
    await userEvent.click(submit());
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(api.callsTo("POST /auth/change-password")).toHaveLength(1);
  });
});

describe("submitting", () => {
  it("sends the documented fields with the CSRF token, then signs out and shows a notice", async () => {
    const api = await openForcedChange({ "POST /auth/change-password": OK });
    await fill();
    await userEvent.click(submit());

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/password was changed/i);
    const [post] = api.callsTo("POST /auth/change-password");
    expect(post.body).toEqual({ current_password: TEMP, new_password: GOOD });
    expect(post.headers["x-csrf-token"]).toBe(CSRF);
    expect(post.credentials).toBe("same-origin");
    expect(screen.getByTestId("location")).toHaveTextContent("/login");
  });

  it("requires signing in again afterwards: protected pages are no longer reachable", async () => {
    installFakeApi({
      "GET /auth/me": [jsonReply(200, makeSession(TEMPORARY_USER, CSRF)), jsonReply(401, { detail: "Not authenticated" })],
      "POST /auth/change-password": OK,
    });
    renderApp("/change-password");
    await screen.findByRole("heading", { name: "Set a new password" });
    await fill();
    await userEvent.click(submit());
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("shows a busy state and does not send twice", async () => {
    const pending = deferred();
    const api = await openForcedChange({
      "POST /auth/change-password": async () => {
        await pending.promise;
        return OK({} as never) as Response;
      },
    });
    await fill();
    await userEvent.click(submit());
    const busy = await screen.findByRole("button", { name: "Updating password…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    expect(api.callsTo("POST /auth/change-password")).toHaveLength(1);
    pending.resolve();
    await screen.findByRole("heading", { name: "Sign in" });
  });
});

describe("backend rejections", () => {
  it.each([
    ["wrong current password", 400, "Current password is incorrect"],
    ["password policy", 400, "Password must be at least 12 characters"],
    ["reused password", 400, "New password must differ from the current password"],
  ])("shows the backend's message for %s and keeps the user on the page", async (_label, status, detail) => {
    await openForcedChange({ "POST /auth/change-password": jsonReply(status, { detail }) });
    await fill();
    await userEvent.click(submit());
    expect(await alertText()).toContain(detail);
    expect(screen.getByRole("heading", { name: "Set a new password" })).toBeInTheDocument();
    expect(submit()).toBeEnabled();
    expect(screen.getByTestId("location")).toHaveTextContent("/change-password");
  });

  it("reports an unreachable server", async () => {
    await openForcedChange({ "POST /auth/change-password": networkError });
    await fill();
    await userEvent.click(submit());
    expect(await alertText()).toMatch(/cannot reach the server/i);
  });

  it("reports a server error without leaking its body", async () => {
    await openForcedChange({ "POST /auth/change-password": textReply(500, "Traceback: secret internals") });
    await fill();
    await userEvent.click(submit());
    const text = await alertText();
    expect(text).toContain("HTTP 500");
    expect(text).not.toMatch(/traceback|secret internals/i);
  });

  it("sends the user back to sign in, with a notice, when the session has expired", async () => {
    await openForcedChange({ "POST /auth/change-password": jsonReply(401, { detail: "Not authenticated" }) });
    await fill();
    await userEvent.click(submit());
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/session has expired/i);
  });

  it("never displays the passwords that were typed", async () => {
    await openForcedChange({ "POST /auth/change-password": jsonReply(400, { detail: "Current password is incorrect" }) });
    await fill();
    await userEvent.click(submit());
    await alertText();
    expect(document.body.textContent).not.toContain(TEMP);
    expect(document.body.textContent).not.toContain(GOOD);
  });
});
