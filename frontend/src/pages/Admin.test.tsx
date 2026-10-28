import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { describeAdminError } from "./Admin";
import { ApiError, type AdminUser } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const PICT = { id: "a0000000-0000-4000-8000-000000000001", code: "PICT", name: "PICT Test Institute", state: "Maharashtra", institution_type: "Engineering", contact_email: null, status: "active", created_at: "2026-01-01T00:00:00Z" };
const COEP = { ...PICT, id: "a0000000-0000-4000-8000-000000000002", code: "COEP", name: "COEP Technological University" };

const account = (overrides: Partial<AdminUser> = {}): AdminUser => ({
  id: "u0000000-0000-4000-8000-000000000001", email: "asha@example.invalid", full_name: "Asha Patil", role: "INSTITUTION_USER",
  institution_id: PICT.id, is_active: true, must_change_password: false, is_locked: false, last_login_at: "2026-10-26T09:00:00Z",
  created_at: "2026-01-01T00:00:00Z", ...overrides,
});
const users = (items: AdminUser[], total = items.length) => ({ items, total, limit: 50, offset: 0 });

function open(role = "ADMIN", routes: Record<string, Handler | Handler[]> = {}) {
  const api = installFakeApi(
    {
      "GET /auth/me": jsonReply(200, makeSession(makeUser({ role, id: "u-self", full_name: "Root Admin", email: "root@example.invalid" }))),
      "GET /institutions": jsonReply(200, { items: [PICT, COEP], total: 2, limit: 200, offset: 0 }),
      "GET /users": jsonReply(200, users([account(), account({ id: "u-self", email: "root@example.invalid", full_name: "Root Admin", role: "ADMIN", institution_id: null })])),
      "GET /banks": jsonReply(200, { items: [{ id: "b1", code: "SBI", name: "State Bank of India" }], page: 1, page_size: 500, total: 1, total_pages: 1 }),
      ...routes,
    },
    { dashboardDefaults: false },
  );
  renderApp("/admin");
  return api;
}

describe("access", () => {
  it.each(["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "AUDITOR"])("%s gets an explanation and nothing is requested", async (role) => {
    const api = open(role);
    expect(await screen.findByRole("alert")).toHaveTextContent(/only institution users can do this|not available|permitted/i);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(api.callsTo("GET /users")).toHaveLength(0);
  });

  it("shows the administrator the users, institution names, and each account's state", async () => {
    open();
    expect(await screen.findByRole("heading", { name: "Administration", level: 1 })).toBeInTheDocument();
    const row = (await screen.findByText("Asha Patil")).closest("tr")!;
    expect(within(row).getByText("asha@example.invalid")).toBeInTheDocument();
    expect(within(row).getByText("Institution user")).toBeInTheDocument();
    expect(within(row).getByText("PICT Test Institute (PICT)")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(within(row).getByText("26 Oct 2026, 09:00 UTC")).toBeInTheDocument();
    expect(screen.queryByText(/hash|csrf|session (id|token)|token_hash|fdr_session/i)).not.toBeInTheDocument(); // no security internals on screen (the 'End sessions' action is a button, not a token)
  });

  it("flags locked accounts, forced password changes, deactivated accounts and users who never signed in", async () => {
    open("ADMIN", { "GET /users": jsonReply(200, users([account({ is_locked: true, must_change_password: true }), account({ id: "u2", email: "b@example.invalid", full_name: "Bela", is_active: false, last_login_at: null })])) });
    const first = (await screen.findByText("Asha Patil")).closest("tr")!;
    expect(within(first).getByText("Locked after failed sign-ins")).toBeInTheDocument();
    expect(within(first).getByText("Must change password")).toBeInTheDocument();
    const second = screen.getByText("Bela").closest("tr")!;
    expect(within(second).getByText("Deactivated")).toBeInTheDocument();
    expect(within(second).getByText("Never")).toBeInTheDocument();
  });

  it("shows a failed load as an error with a retry", async () => {
    let calls = 0;
    open("ADMIN", { "GET /users": (req) => (calls++ === 0 ? textReply(500)(req) : jsonReply(200, users([account()]))(req)) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load users/i);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Asha Patil")).toBeInTheDocument();
  });
});

describe("creating a user", () => {
  const fill = async (email = "new@example.invalid", name = "New Person") => {
    await userEvent.type(await screen.findByLabelText("Email"), email);
    await userEvent.type(screen.getByLabelText("Full name"), name);
  };

  it("requires an institution for an institution user and sends exactly the entered fields", async () => {
    const created = { user: account({ id: "u9", email: "new@example.invalid", full_name: "New Person", must_change_password: true }), temporary_password: "Temp-Pass-Once-123" };
    const api = open("ADMIN", { "POST /users": jsonReply(201, created) });
    await fill();
    const submit = screen.getByRole("button", { name: "Create user" });
    expect(submit).toBeDisabled(); // an institution user needs an institution
    await userEvent.selectOptions(screen.getByLabelText(/Institution \(required/), "COEP Technological University (COEP)");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(await screen.findByTestId("temporary-password")).toHaveTextContent("Temp-Pass-Once-123");
    expect(api.callsTo("POST /users")[0].body).toEqual({ email: "new@example.invalid", full_name: "New Person", role: "INSTITUTION_USER", institution_id: COEP.id });
    expect(api.callsTo("GET /users").length).toBeGreaterThanOrEqual(2); // the list was refreshed
  });

  it("shows the temporary password once, with a warning, and it disappears when dismissed", async () => {
    open("ADMIN", { "POST /users": jsonReply(201, { user: account({ email: "n@example.invalid" }), temporary_password: "Temp-Pass-Once-123" }) });
    await fill("n@example.invalid");
    await userEvent.selectOptions(screen.getByLabelText(/Institution \(required/), "PICT Test Institute (PICT)");
    await userEvent.click(screen.getByRole("button", { name: "Create user" }));
    const notice = (await screen.findByTestId("temporary-password")).closest("[role=status]")!;
    expect(notice).toHaveTextContent(/shown only now/i);
    expect(notice).toHaveTextContent(/cannot be retrieved later/i);
    await userEvent.click(within(notice as HTMLElement).getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByTestId("temporary-password")).not.toBeInTheDocument();
    expect(screen.queryByText("Temp-Pass-Once-123")).not.toBeInTheDocument();
  });

  it("needs no institution for AICTE and administrator roles, and does not send one", async () => {
    const api = open("ADMIN", { "POST /users": jsonReply(201, { user: account({ role: "AICTE_REVIEWER", institution_id: null }), temporary_password: "x-Temp-Pass-123" }) });
    await fill();
    await userEvent.selectOptions(screen.getByLabelText("Role"), "AICTE reviewer");
    expect(screen.queryByLabelText(/Institution \(required/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create user" }));
    await screen.findByTestId("temporary-password");
    expect(api.callsTo("POST /users")[0].body).toMatchObject({ role: "AICTE_REVIEWER", institution_id: null });
  });

  it("sends a double-clicked create only once", async () => {
    const gate = deferred();
    const api = open("ADMIN", { "POST /users": async (req) => { await gate.promise; return jsonReply(201, { user: account(), temporary_password: "T-123456789-x" })(req) as Response; } });
    await fill();
    await userEvent.selectOptions(screen.getByLabelText(/Institution \(required/), "PICT Test Institute (PICT)");
    await userEvent.dblClick(screen.getByRole("button", { name: "Create user" }));
    expect(await screen.findByRole("button", { name: "Creating…" })).toBeDisabled();
    expect(api.callsTo("POST /users")).toHaveLength(1);
    gate.resolve();
    await screen.findByTestId("temporary-password");
    expect(api.callsTo("POST /users")).toHaveLength(1);
  });

  it.each([
    ["a duplicate email", jsonReply(409, { detail: "A user with this email already exists" }), "A user with this email already exists"],
    ["an invalid email", jsonReply(422, { detail: [{ loc: ["body", "email"], msg: "Value error, Invalid email address" }] }), "Invalid email address"],
    ["a refusal", jsonReply(403, { detail: "Forbidden" }), /not permitted/i],
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
  ])("reports %s and creates nothing on screen", async (_label, failing, message) => {
    open("ADMIN", { "POST /users": failing });
    await fill();
    await userEvent.selectOptions(screen.getByLabelText(/Institution \(required/), "PICT Test Institute (PICT)");
    await userEvent.click(screen.getByRole("button", { name: "Create user" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByTestId("temporary-password")).not.toBeInTheDocument();
  });
});

describe("changing a user", () => {
  const edit = async (email = "asha@example.invalid") => {
    await userEvent.click(await screen.findByRole("button", { name: `Edit ${email}` }));
    return screen.getByRole("group", { name: `Edit ${email}` });
  };

  it("sends only what changed, and a name change needs no confirmation", async () => {
    const api = open("ADMIN", { "PATCH /users/:id": jsonReply(200, account({ full_name: "Asha P. Patil" })) });
    const form = within(await edit());
    const save = form.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled(); // nothing changed yet
    const name = form.getByLabelText("Full name");
    await userEvent.clear(name);
    await userEvent.type(name, "Asha P. Patil");
    await userEvent.click(save);
    await screen.findByText("Asha Patil");
    expect(api.callsTo("PATCH /users/u0000000-0000-4000-8000-000000000001")[0].body).toEqual({ full_name: "Asha P. Patil" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("asks for confirmation before deactivating or changing a role, and names the effect", async () => {
    const api = open("ADMIN", { "PATCH /users/:id": jsonReply(200, account({ is_active: false })) });
    const form = within(await edit());
    await userEvent.click(form.getByLabelText("Account is active"));
    await userEvent.click(form.getByRole("button", { name: "Save changes" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/signs them out everywhere immediately/i);
    expect(dialog).toHaveTextContent(/recorded in the audit trail under your name/i);
    expect(api.callsTo("PATCH /users/u0000000-0000-4000-8000-000000000001")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm changes" }));
    await screen.findByText("Asha Patil");
    expect(api.callsTo("PATCH /users/u0000000-0000-4000-8000-000000000001")[0].body).toEqual({ is_active: false });
  });

  it("changes role and institution together when moving between institutions", async () => {
    const api = open("ADMIN", { "PATCH /users/:id": jsonReply(200, account({ institution_id: COEP.id })) });
    const form = within(await edit());
    await userEvent.selectOptions(form.getByLabelText("Institution"), "COEP Technological University (COEP)");
    await userEvent.click(form.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Asha Patil");
    expect(api.callsTo("PATCH /users/u0000000-0000-4000-8000-000000000001")[0].body).toEqual({ institution_id: COEP.id });
  });

  it("hides the institution when the role stops being an institution user, and clears nothing it should not", async () => {
    const api = open("ADMIN", { "PATCH /users/:id": jsonReply(200, account({ role: "AUDITOR", institution_id: null })) });
    const form = within(await edit());
    await userEvent.selectOptions(form.getByLabelText("Role"), "Auditor");
    expect(form.queryByLabelText("Institution")).not.toBeInTheDocument();
    await userEvent.click(form.getByRole("button", { name: "Save changes" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm changes" }));
    await screen.findByText("Asha Patil");
    expect(api.callsTo("PATCH /users/u0000000-0000-4000-8000-000000000001")[0].body).toEqual({ role: "AUDITOR" });
  });

  it("shows the server's refusal, such as the last-administrator safeguard, and keeps the form open", async () => {
    open("ADMIN", { "PATCH /users/:id": jsonReply(409, { detail: "At least one active administrator must remain" }) });
    const form = within(await edit("root@example.invalid"));
    expect(screen.getByText(/leave the system without an active administrator/i)).toBeInTheDocument(); // own account notice
    await userEvent.click(form.getByLabelText("Account is active"));
    await userEvent.click(form.getByRole("button", { name: "Save changes" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("At least one active administrator must remain");
    expect(screen.getByRole("group", { name: "Edit root@example.invalid" })).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("cannot save an institution user without an institution", async () => {
    open("ADMIN", { "GET /users": jsonReply(200, users([account({ role: "AUDITOR", institution_id: null })])) });
    const form = within(await edit());
    await userEvent.selectOptions(form.getByLabelText("Role"), "Institution user");
    expect(form.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });
});

describe("institutions and banks", () => {
  it("lists them read-only and says why they cannot be edited", async () => {
    const api = open();
    await userEvent.click(await screen.findByRole("tab", { name: "Institutions" }));
    expect(await screen.findByText(/read-only in this version/i)).toBeInTheDocument();
    expect(screen.getByText("COEP Technological University")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create|edit|save|delete/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Banks" }));
    expect(await screen.findByText("State Bank of India")).toBeInTheDocument();
    expect(screen.getByText(/no way to create or change them/i)).toBeInTheDocument();
    expect(api.calls.filter((c) => c.method !== "GET")).toEqual([]);
  });
});

describe("error text", () => {
  it("uses the server's explanation for validation and conflict errors and generic text for the rest", () => {
    expect(describeAdminError(new ApiError("validation", 422, "x", [{ msg: "Value error, institution_id is required for INSTITUTION_USER" }]))).toBe("institution_id is required for INSTITUTION_USER");
    expect(describeAdminError(new ApiError("client", 409, "A user with this email already exists"))).toBe("A user with this email already exists");
    expect(describeAdminError(new ApiError("server", 500, "boom with internals"))).toBe("The server reported an error (HTTP 500).");
    expect(describeAdminError(new Error("secret internals"))).toBe("The request failed unexpectedly. Please try again.");
  });
});
