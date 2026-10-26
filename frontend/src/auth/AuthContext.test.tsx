import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { useAuth } from "./AuthContext";
import { ApiError, changePassword as rawChangePassword } from "../lib/api";
import { installFakeApi, jsonReply, networkError, textReply } from "../test/fakeApi";
import { PASSWORD, makeSession, makeUser } from "../test/fixtures";
import { renderWithAuth } from "../test/render";

/** Shows the context state and exposes each action as a button; the outcome of an action is displayed. */
function Probe() {
  const { state, notice, login, logout, changePassword, retrySession } = useAuth();
  return (
    <div>
      <p data-testid="status">{state.status}</p>
      {state.status === "authenticated" && <p data-testid="user">{JSON.stringify(state.user)}</p>}
      {state.status === "error" && <p data-testid="problem">{state.message}</p>}
      {notice && <p data-testid="notice">{notice}</p>}
      <ActionButton label="login" run={() => login("  officer@example.invalid ", PASSWORD)} />
      <ActionButton label="logout" run={logout} />
      <ActionButton label="change" run={() => changePassword("old-password-1", "new-password-22")} />
      <ActionButton label="raw-change" run={() => rawChangePassword("x", "y").then(() => undefined)} />
      <button onClick={retrySession}>retry</button>
    </div>
  );
}

function ActionButton({ label, run }: { label: string; run: () => Promise<void> }) {
  const [result, setResult] = useState("");
  return (
    <>
      <button
        onClick={() =>
          run().then(
            () => setResult("ok"),
            (err: unknown) => setResult(err instanceof ApiError ? `${err.kind}:${err.status}` : "failed"),
          )
        }
      >
        {label}
      </button>
      {result && <output aria-label={`${label} result`}>{result}</output>}
    </>
  );
}

const status = () => screen.getByTestId("status").textContent;
const waitForStatus = (expected: string) => waitFor(() => expect(status()).toBe(expected));

describe("session restoration", () => {
  it("restores the session from GET /auth/me using the backend's response", async () => {
    const user = makeUser({ full_name: "Asha Verma", role: "ADMIN" });
    const api = installFakeApi({ "GET /auth/me": jsonReply(200, makeSession(user)) });
    renderWithAuth(<Probe />);
    expect(status()).toBe("loading");
    await waitForStatus("authenticated");
    expect(JSON.parse(screen.getByTestId("user").textContent!)).toEqual(user);
    expect(api.callsTo("GET /auth/me")).toHaveLength(1);
    expect(api.calls[0].credentials).toBe("same-origin");
  });

  it("survives React StrictMode's double effect", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(200, makeSession()) });
    renderWithAuth(<Probe />, { strict: true });
    await waitForStatus("authenticated");
  });

  it("treats 401 as signed out, without an 'expired' notice", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }) });
    renderWithAuth(<Probe />);
    await waitForStatus("unauthenticated");
    expect(screen.queryByTestId("notice")).not.toBeInTheDocument();
  });

  it("shows a recoverable error, not a signed-in or signed-out state, when the network fails", async () => {
    installFakeApi({ "GET /auth/me": networkError });
    renderWithAuth(<Probe />);
    await waitForStatus("error");
    expect(screen.getByTestId("problem")).toHaveTextContent(/cannot reach the server/i);
  });

  it("shows a recoverable error for a server failure", async () => {
    installFakeApi({ "GET /auth/me": textReply(500) });
    renderWithAuth(<Probe />);
    await waitForStatus("error");
    expect(screen.getByTestId("problem")).toHaveTextContent("HTTP 500");
  });

  it("restores the session when retried after a temporary failure", async () => {
    const api = installFakeApi({ "GET /auth/me": [networkError, jsonReply(200, makeSession())] });
    renderWithAuth(<Probe />);
    await waitForStatus("error");
    await userEvent.click(screen.getByRole("button", { name: "retry" }));
    await waitForStatus("authenticated");
    expect(api.callsTo("GET /auth/me")).toHaveLength(2);
  });
});

describe("login", () => {
  const signedOut = () => jsonReply(401, { detail: "Not authenticated" });

  it("posts exactly the email and password and adopts the backend's user", async () => {
    const user = makeUser({ full_name: "Rohan Iyer", role: "INSTITUTION_USER", institution_id: "11111111-2222-4333-8444-555555555555" });
    const api = installFakeApi({
      "GET /auth/me": signedOut(),
      "POST /auth/login": jsonReply(200, makeSession(user)),
    });
    renderWithAuth(<Probe />);
    await waitForStatus("unauthenticated");
    await userEvent.click(screen.getByRole("button", { name: "login" }));
    await waitForStatus("authenticated");
    const [post] = api.callsTo("POST /auth/login");
    expect(post.body).toEqual({ email: "  officer@example.invalid ", password: PASSWORD });
    expect(JSON.parse(screen.getByTestId("user").textContent!)).toEqual(user);
  });

  it("stays signed out and reports the typed error when the credentials are rejected", async () => {
    installFakeApi({
      "GET /auth/me": signedOut(),
      "POST /auth/login": jsonReply(401, { detail: "Invalid credentials" }),
    });
    renderWithAuth(<Probe />);
    await waitForStatus("unauthenticated");
    await userEvent.click(screen.getByRole("button", { name: "login" }));
    expect(await screen.findByLabelText("login result")).toHaveTextContent("unauthorized:401");
    expect(status()).toBe("unauthenticated");
    expect(screen.queryByTestId("notice")).not.toBeInTheDocument();
  });
});

describe("logout", () => {
  async function signedIn(extra: Parameters<typeof installFakeApi>[0] = {}) {
    const api = installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession(makeUser(), "csrf-from-session-check")),
      ...extra,
    });
    renderWithAuth(<Probe />);
    await waitForStatus("authenticated");
    return api;
  }

  it("signs out after a successful response and sends the in-memory CSRF token", async () => {
    const api = await signedIn({ "POST /auth/logout": jsonReply(200, { detail: "Signed out" }) });
    await userEvent.click(screen.getByRole("button", { name: "logout" }));
    await waitForStatus("unauthenticated");
    expect(api.callsTo("POST /auth/logout")[0].headers["x-csrf-token"]).toBe("csrf-from-session-check");
    expect(screen.queryByTestId("notice")).not.toBeInTheDocument();
  });

  it("forgets the CSRF token once signed out", async () => {
    const api = await signedIn({
      "POST /auth/logout": jsonReply(200, { detail: "Signed out" }),
      "POST /auth/change-password": jsonReply(401, { detail: "Not authenticated" }),
    });
    await userEvent.click(screen.getByRole("button", { name: "logout" }));
    await waitForStatus("unauthenticated");
    await userEvent.click(screen.getByRole("button", { name: "raw-change" }));
    await screen.findByLabelText("raw-change result");
    expect(api.callsTo("POST /auth/change-password")[0].headers["x-csrf-token"]).toBeUndefined();
  });

  it("treats 401 from logout as already signed out", async () => {
    await signedIn({ "POST /auth/logout": jsonReply(401, { detail: "Not authenticated" }) });
    await userEvent.click(screen.getByRole("button", { name: "logout" }));
    await waitForStatus("unauthenticated");
  });

  it.each([
    ["a network failure", networkError, "network:0"],
    ["a server error", textReply(500), "server:500"],
  ])("stays signed in and reports the failure on %s", async (_label, handler, expected) => {
    await signedIn({ "POST /auth/logout": handler });
    await userEvent.click(screen.getByRole("button", { name: "logout" }));
    expect(await screen.findByLabelText("logout result")).toHaveTextContent(expected);
    expect(status()).toBe("authenticated");
  });
});

describe("session expiry", () => {
  it("signs the user out with a notice when a later request gets 401", async () => {
    installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession()),
      "POST /auth/change-password": jsonReply(401, { detail: "Not authenticated" }),
    });
    renderWithAuth(<Probe />);
    await waitForStatus("authenticated");
    await userEvent.click(screen.getByRole("button", { name: "raw-change" }));
    await waitForStatus("unauthenticated");
    expect(screen.getByTestId("notice")).toHaveTextContent(/session has expired/i);
  });

  it("does not announce an expiry for a 401 received while already signed out", async () => {
    installFakeApi({
      "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }),
      "POST /auth/change-password": jsonReply(401, { detail: "Not authenticated" }),
    });
    renderWithAuth(<Probe />);
    await waitForStatus("unauthenticated");
    await userEvent.click(screen.getByRole("button", { name: "raw-change" }));
    await screen.findByLabelText("raw-change result");
    expect(screen.queryByTestId("notice")).not.toBeInTheDocument();
  });
});

describe("password change", () => {
  it("sends the CSRF token, then ends the session as the backend revokes it", async () => {
    const api = installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession(makeUser(), "csrf-abc")),
      "POST /auth/change-password": jsonReply(200, { detail: "Password changed. Please sign in again." }),
    });
    renderWithAuth(<Probe />);
    await waitForStatus("authenticated");
    await userEvent.click(screen.getByRole("button", { name: "change" }));
    await waitForStatus("unauthenticated");
    const [post] = api.callsTo("POST /auth/change-password");
    expect(post.headers["x-csrf-token"]).toBe("csrf-abc");
    expect(post.body).toEqual({ current_password: "old-password-1", new_password: "new-password-22" });
    expect(screen.getByTestId("notice")).toHaveTextContent(/password was changed/i);
  });

  it("stays signed in when the backend rejects the change", async () => {
    installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession()),
      "POST /auth/change-password": jsonReply(400, { detail: "Current password is incorrect" }),
    });
    renderWithAuth(<Probe />);
    await waitForStatus("authenticated");
    await userEvent.click(screen.getByRole("button", { name: "change" }));
    expect(await screen.findByLabelText("change result")).toHaveTextContent("client:400");
    expect(status()).toBe("authenticated");
  });
});

describe("in-memory session data", () => {
  it("keeps the session out of browser storage (the global teardown also asserts this)", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(200, makeSession(makeUser(), "csrf-in-memory-only")) });
    renderWithAuth(<Probe />);
    await waitForStatus("authenticated");
    expect(localStorage.length + sessionStorage.length).toBe(0);
    expect(document.cookie).toBe(""); // the session cookie is HttpOnly; script never sees it
  });
});
