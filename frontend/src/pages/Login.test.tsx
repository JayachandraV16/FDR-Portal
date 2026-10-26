import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { deferred, installFakeApi, jsonReply, networkError, textReply } from "../test/fakeApi";
import { PASSWORD, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const signedOut = jsonReply(401, { detail: "Not authenticated" });
const EMAIL = "officer@example.invalid";

async function openLogin(routes: Parameters<typeof installFakeApi>[0] = {}) {
  const api = installFakeApi({ "GET /auth/me": signedOut, ...routes });
  renderApp("/login");
  await screen.findByRole("heading", { name: "Sign in" });
  return api;
}

const emailField = () => screen.getByLabelText("Email");
const passwordField = () => screen.getByLabelText("Password");
const submit = () => screen.getByRole("button", { name: /sign in|signing in/i });

async function fillAndSubmit(email = EMAIL, password = PASSWORD) {
  await userEvent.type(emailField(), email);
  await userEvent.type(passwordField(), password);
  await userEvent.click(submit());
}

describe("form", () => {
  it("renders labelled email and password fields and a submit button", async () => {
    await openLogin();
    expect(emailField()).toHaveAttribute("type", "email");
    expect(emailField()).toBeRequired();
    expect(passwordField()).toHaveAttribute("type", "password");
    expect(passwordField()).toBeRequired();
    expect(submit()).toBeEnabled();
  });

  it("toggles password visibility", async () => {
    await openLogin();
    await userEvent.type(passwordField(), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(passwordField()).toHaveAttribute("type", "text");
    expect(passwordField()).toHaveValue(PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(passwordField()).toHaveAttribute("type", "password");
  });

  it.each([
    ["both fields empty", "", ""],
    ["no password", EMAIL, ""],
    ["no email", "", PASSWORD],
    ["a blank email", "   ", PASSWORD],
  ])("does not contact the server when %s", async (_label, email, password) => {
    const api = await openLogin();
    if (email) await userEvent.type(emailField(), email);
    if (password) await userEvent.type(passwordField(), password);
    await userEvent.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter your email address and password.");
    expect(api.callsTo("POST /auth/login")).toHaveLength(0);
  });

  it("never shows a password in the page text", async () => {
    await openLogin();
    await userEvent.type(passwordField(), PASSWORD);
    expect(document.body.textContent).not.toContain(PASSWORD);
  });
});

describe("submitting", () => {
  it("sends the trimmed email and the password, then opens the application", async () => {
    const user = makeUser({ full_name: "Meera Kulkarni", role: "AICTE_APPROVER" });
    const api = await openLogin({ "POST /auth/login": jsonReply(200, makeSession(user)) });
    await fillAndSubmit(`  ${EMAIL}  `);
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    const [post] = api.callsTo("POST /auth/login");
    expect(post.body).toEqual({ email: EMAIL, password: PASSWORD });
    expect(within(screen.getByRole("banner")).getByText("Meera Kulkarni")).toBeInTheDocument();
  });

  it("goes to the password-change screen when the backend says the password is temporary", async () => {
    const user = makeUser({ must_change_password: true, role: "ADMIN" });
    await openLogin({ "POST /auth/login": jsonReply(200, makeSession(user)) });
    await fillAndSubmit();
    expect(await screen.findByRole("heading", { name: "Set a new password" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("shows a busy state and ignores repeat submissions while a request is in flight", async () => {
    const pending = deferred();
    const api = await openLogin({
      "POST /auth/login": async () => {
        await pending.promise;
        return jsonReply(200, makeSession())({} as never) as Response;
      },
    });
    await userEvent.type(emailField(), EMAIL);
    await userEvent.type(passwordField(), PASSWORD);
    await userEvent.click(submit());

    const busyButton = await screen.findByRole("button", { name: "Signing in…" });
    expect(busyButton).toBeDisabled();
    expect(emailField()).toBeDisabled();
    expect(passwordField()).toBeDisabled();
    await userEvent.click(busyButton);
    await userEvent.keyboard("{Enter}");
    expect(api.callsTo("POST /auth/login")).toHaveLength(1);

    pending.resolve();
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(api.callsTo("POST /auth/login")).toHaveLength(1);
  });
});

describe("failures", () => {
  it("explains invalid credentials, keeps the email and clears the password", async () => {
    await openLogin({ "POST /auth/login": jsonReply(401, { detail: "Invalid credentials" }) });
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(emailField()).toHaveValue(EMAIL);
    expect(passwordField()).toHaveValue("");
    expect(submit()).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("says plainly that the server cannot be reached", async () => {
    await openLogin({ "POST /auth/login": networkError });
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot reach the server/i);
    expect(submit()).toBeEnabled();
  });

  it("reports a server error with its status code", async () => {
    await openLogin({ "POST /auth/login": textReply(500) });
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 500");
  });

  it("explains a blocked origin without suggesting the credentials were wrong", async () => {
    await openLogin({ "POST /auth/login": jsonReply(403, { detail: "Untrusted request origin" }) });
    await fillAndSubmit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/not served from an allowed address/i);
    expect(alert).not.toHaveTextContent(/invalid email or password/i);
  });

  it("asks for valid input when the backend returns a validation error", async () => {
    await openLogin({ "POST /auth/login": jsonReply(422, { detail: [{ loc: ["body", "email"], msg: "bad" }] }) });
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(/valid email address/i);
  });

  it("never exposes stack traces, raw server text, or the password in an error", async () => {
    const leak = "Traceback (most recent call last): File app/main.py line 1 psycopg.OperationalError";
    await openLogin({ "POST /auth/login": jsonReply(500, { detail: leak }) });
    await fillAndSubmit();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/traceback|psycopg|app\/main\.py/i);
    expect(document.body.textContent).not.toContain(PASSWORD);
  });

  it("does not write to the console", async () => {
    const spies = (["log", "info", "warn", "error"] as const).map((m) => vi.spyOn(console, m));
    await openLogin({ "POST /auth/login": jsonReply(401, { detail: "Invalid credentials" }) });
    await fillAndSubmit();
    await screen.findByRole("alert");
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it("allows a retry after a failure", async () => {
    const api = await openLogin({
      "POST /auth/login": [networkError, jsonReply(200, makeSession())],
    });
    await fillAndSubmit();
    await screen.findByRole("alert");
    await userEvent.type(passwordField(), PASSWORD);
    await userEvent.click(submit());
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(api.callsTo("POST /auth/login")).toHaveLength(2);
  });
});

describe("page content", () => {
  it("shows the demonstration-data notice and no self-registration route", async () => {
    await openLogin();
    expect(screen.getByText(/demonstration data only/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /register|sign up/i })).not.toBeInTheDocument();
    await waitFor(() => expect(submit()).toBeEnabled());
  });
});
