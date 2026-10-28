import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { FdrOut } from "../lib/api";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const ID = "f0000000-0000-4000-8000-0000000000aa";
const INSTITUTION = { id: "a0000000-0000-4000-8000-000000000001", code: "PICT", name: "PICT Test Institute" };
const BANKS = [
  { id: "b0000000-0000-4000-8000-000000000001", code: "BOM", name: "Bank of Maharashtra" },
  { id: "b0000000-0000-4000-8000-000000000002", code: "SBI", name: "State Bank of India" },
];
const banksEndpoint = jsonReply(200, { items: BANKS, page: 1, page_size: 500, total: 2, total_pages: 1 });

const draft = (overrides: Partial<FdrOut> = {}): FdrOut =>
  makeFdr({ id: ID, status: "DRAFT", row_version: 1, institution: INSTITUTION, bank: BANKS[0], fdr_number: "WF-DRAFT-1",
    branch_name: "Main Branch", depositor_name: "Test Depositor", purpose: "Security deposit", submitted_at: null, ...overrides });

/** A small stateful stand-in for the backend's FDR endpoints, following the real contract: the server
 * assigns institution, status and version; PATCH/submit need the current row_version; only drafts
 * can be edited or submitted. */
function workflowBackend(initial: FdrOut | null = null, extra: Record<string, Handler | Handler[]> = {}, role = "INSTITUTION_USER") {
  const state = { fdr: initial };
  const user = makeUser({ role, institution_id: role === "INSTITUTION_USER" ? INSTITUTION.id : null });
  const list: Handler = (req) =>
    jsonReply(200, { items: state.fdr ? [state.fdr] : [], page: 1, page_size: 25, total: state.fdr ? 1 : 0, total_pages: state.fdr ? 1 : 0 })(req);
  const api = installFakeApi(
    {
      "GET /auth/me": jsonReply(200, makeSession(user)),
      "GET /banks": banksEndpoint,
      "GET /institutions": jsonReply(200, { items: [INSTITUTION], total: 1, limit: 200, offset: 0 }),
      "GET /fdrs": list,
      "POST /fdrs": (req) => {
        const b = req.body as Record<string, string | null>;
        state.fdr = draft({
          fdr_number: b.fdr_number!, bank: BANKS.find((x) => x.id === b.bank_id)!, branch_name: b.branch_name, depositor_name: b.depositor_name,
          purpose: b.purpose, principal_amount: Number(b.principal_amount).toFixed(2), interest_rate_pct: b.interest_rate_pct,
          issue_date: b.issue_date!, maturity_date: b.maturity_date!,
        });
        return jsonReply(201, state.fdr)(req);
      },
      [`GET /fdrs/${ID}`]: (req) => (state.fdr ? jsonReply(200, state.fdr)(req) : jsonReply(404, { detail: "FDR not found" })(req)),
      [`PATCH /fdrs/${ID}`]: (req) => {
        const { row_version, ...fields } = req.body as { row_version: number } & Record<string, string | null>;
        if (!state.fdr || state.fdr.status !== "DRAFT") return jsonReply(409, { detail: "Only draft FDRs can be edited" })(req);
        if (row_version !== state.fdr.row_version) return jsonReply(409, { detail: "This FDR was changed since you loaded it. Reload it and try again." })(req);
        state.fdr = { ...state.fdr, ...(fields as Partial<FdrOut>), row_version: state.fdr.row_version + 1 };
        return jsonReply(200, state.fdr)(req);
      },
      [`POST /fdrs/${ID}/submit`]: (req) => {
        const { row_version } = req.body as { row_version: number };
        if (!state.fdr || state.fdr.status !== "DRAFT") return jsonReply(409, { detail: "Only draft FDRs can be submitted" })(req);
        if (row_version !== state.fdr.row_version) return jsonReply(409, { detail: "This FDR was changed since you loaded it. Reload it and try again." })(req);
        state.fdr = { ...state.fdr, status: "SUBMITTED", row_version: row_version + 1, submitted_at: "2026-10-27T09:00:00Z" };
        return jsonReply(200, state.fdr)(req);
      },
      [`GET /fdrs/${ID}/documents`]: jsonReply(200, { items: [] }), // the panel is covered in FdrDocuments.test.tsx
      ...extra,
    },
    { dashboardDefaults: false },
  );
  return { api, state };
}

const location = () => screen.getByTestId("location");
const field = (label: RegExp | string) => screen.getByLabelText(label);

async function fillForm(overrides: Partial<Record<"number" | "amount" | "rate" | "issue" | "maturity" | "branch" | "depositor" | "purpose", string>> = {}) {
  const v = { number: "  NEW-FDR-1  ", amount: "1500000.5", rate: "7.1", issue: "2026-01-15", maturity: "2027-01-15", branch: "Main Branch", depositor: "Test Depositor", purpose: "", ...overrides };
  await screen.findByRole("option", { name: "Bank of Maharashtra" }); // banks loaded
  await userEvent.selectOptions(field("Bank"), "Bank of Maharashtra");
  await userEvent.type(field("FDR number"), v.number);
  await userEvent.type(field(/principal amount/i), v.amount);
  if (v.rate) await userEvent.type(field(/interest rate/i), v.rate);
  fireEvent.change(field("Issue date"), { target: { value: v.issue } });
  fireEvent.change(field("Maturity date"), { target: { value: v.maturity } });
  if (v.branch) await userEvent.type(field("Branch name"), v.branch);
  if (v.depositor) await userEvent.type(field("Depositor name"), v.depositor);
  if (v.purpose) await userEvent.type(field("Purpose"), v.purpose);
}
const saveDraft = () => userEvent.click(screen.getByRole("button", { name: "Save as draft" }));

describe("create form", () => {
  it("renders every field the backend takes, and none it decides", async () => {
    workflowBackend();
    renderApp("/fdrs/new");
    expect(await screen.findByRole("heading", { name: "Create FDR" })).toBeInTheDocument();
    for (const label of ["Bank", "FDR number", /principal amount/i, /interest rate/i, "Issue date", "Maturity date", "Branch name", "Depositor name", "Purpose"]) {
      expect(field(label)).toBeInTheDocument();
    }
    expect(screen.queryByLabelText(/institution/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^status/i)).not.toBeInTheDocument();
    expect(screen.getByText(/saved as a draft in your institution/i)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/demonstration environment/i);
    expect(await screen.findByRole("option", { name: "State Bank of India" })).toBeInTheDocument();
  });

  it("validates on the client and sends nothing while the form is invalid", async () => {
    const { api } = workflowBackend();
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await saveDraft();
    expect(await screen.findByText("Choose a bank.")).toBeInTheDocument();
    expect(screen.getByText("Enter the FDR number.")).toBeInTheDocument();
    expect(screen.getByText("Enter the principal amount.")).toBeInTheDocument();
    expect(screen.getByText("Enter the issue date.")).toBeInTheDocument();
    expect(screen.getByText("Enter the maturity date.")).toBeInTheDocument();
    expect(field("FDR number")).toHaveAttribute("aria-invalid", "true");
    expect(api.callsTo("POST /fdrs")).toHaveLength(0);
  });

  it.each([
    [{ amount: "0" }, /greater than zero/i], [{ amount: "1.234" }, /use digits with at most two decimal places/i], [{ amount: "abc" }, /use digits with at most two decimal places/i],
    [{ rate: "101" }, /cannot exceed 100/i], [{ maturity: "2026-01-15" }, /after the issue date/i],
  ])("rejects %j without calling the API", async (overrides, message) => {
    const { api } = workflowBackend();
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm(overrides);
    await saveDraft();
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(api.callsTo("POST /fdrs")).toHaveLength(0);
  });

  it("saves a draft with exactly the documented body and opens it for editing", async () => {
    const { api } = workflowBackend();
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm();
    await saveDraft();

    expect(await screen.findByRole("heading", { name: "Edit FDR" })).toBeInTheDocument();
    expect(location()).toHaveTextContent(`/fdrs/${ID}/edit`);
    expect(await screen.findByText(/draft saved/i)).toBeInTheDocument(); // shown once the draft has loaded
    const [post] = api.callsTo("POST /fdrs");
    expect(post.body).toEqual({
      bank_id: BANKS[0].id, fdr_number: "NEW-FDR-1", branch_name: "Main Branch", depositor_name: "Test Depositor", purpose: null,
      principal_amount: "1500000.5", interest_rate_pct: "7.1", issue_date: "2026-01-15", maturity_date: "2027-01-15",
    });
    expect(post.headers["x-csrf-token"]).toBe("csrf-token-for-tests");
    expect(await screen.findByDisplayValue("NEW-FDR-1")).toBeInTheDocument();
  });

  it("shows backend field errors next to their fields and keeps what the user typed", async () => {
    workflowBackend(null, {
      "POST /fdrs": jsonReply(422, { detail: [{ loc: ["body", "fdr_number"], msg: "String should have at most 64 characters" }] }),
    });
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm();
    await saveDraft();
    const message = await screen.findByText("String should have at most 64 characters");
    expect(message.id).toBe(field("FDR number").getAttribute("aria-describedby"));
    expect(field("FDR number")).toHaveValue("  NEW-FDR-1  ");
    expect(screen.getByRole("button", { name: "Save as draft" })).toBeEnabled();
  });

  it.each([
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted/i],
    ["an unknown bank", jsonReply(422, { detail: "Unknown bank" }), /unknown bank/i],
  ])("reports %s clearly without losing the form", async (_label, failing, message) => {
    workflowBackend(null, { "POST /fdrs": failing });
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm();
    await saveDraft();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(field("FDR number")).toHaveValue("  NEW-FDR-1  ");
    expect(screen.getByRole("heading", { name: "Create FDR" })).toBeInTheDocument();
  });

  it("does not leak server internals into the message", async () => {
    workflowBackend(null, { "POST /fdrs": jsonReply(500, { detail: "Traceback: psycopg.OperationalError secret" }) });
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm();
    await saveDraft();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/traceback|psycopg|secret/i);
  });

  it("prevents a double submit while saving", async () => {
    const pending = deferred();
    const { api } = workflowBackend(null, {
      "POST /fdrs": async (req) => { await pending.promise; return jsonReply(201, draft())(req) as Response; },
    });
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm();
    await saveDraft();
    const busy = await screen.findByRole("button", { name: "Saving…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    expect(api.callsTo("POST /fdrs")).toHaveLength(1);
    pending.resolve();
  });

  it("returns to sign-in when the session has expired", async () => {
    workflowBackend(null, { "POST /fdrs": jsonReply(401, { detail: "Not authenticated" }) });
    renderApp("/fdrs/new");
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm();
    await saveDraft();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/session has expired/i);
  });
});

describe("role-aware access", () => {
  it.each(["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"])("%s sees no Create FDR action and no form", async (role) => {
    workflowBackend(null, {}, role);
    renderApp("/fdrs");
    expect(await screen.findByRole("heading", { name: "FDR Registry" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create FDR" })).not.toBeInTheDocument();
    expect(screen.getByText(/this list is read-only/i)).toBeInTheDocument();
  });

  it.each(["/fdrs/new", `/fdrs/${ID}/edit`])("a reviewer opening %s sees an explanation, not a form, and sends no write", async (path) => {
    const { api } = workflowBackend(draft({ status: "SUBMITTED" }), {}, "AICTE_REVIEWER");
    renderApp(path);
    expect(await screen.findByRole("alert")).toHaveTextContent(/only institution users/i);
    expect(screen.queryByLabelText("FDR number")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save|submit/i })).not.toBeInTheDocument();
    expect(api.calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("institution users get a Create FDR link, and Edit draft links only on their draft rows", async () => {
    workflowBackend(draft());
    renderApp("/fdrs");
    const create = await screen.findByRole("link", { name: "Create FDR" });
    expect(create).toHaveAttribute("href", "/fdrs/new");
    const row = (await screen.findByText("WF-DRAFT-1")).closest("tr")!;
    expect(within(row).getByRole("link", { name: "Edit draft" })).toHaveAttribute("href", `/fdrs/${ID}/edit`);
  });

  it("offers no edit link for a submitted FDR", async () => {
    workflowBackend(draft({ status: "SUBMITTED" }));
    renderApp("/fdrs");
    await screen.findByText("WF-DRAFT-1");
    expect(screen.queryByRole("link", { name: "Edit draft" })).not.toBeInTheDocument();
  });

  it("keeps the create and edit pages behind sign-in", async () => {
    installFakeApi({ "GET /auth/me": jsonReply(401, { detail: "Not authenticated" }) }, { dashboardDefaults: false });
    renderApp("/fdrs/new");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByLabelText("FDR number")).not.toBeInTheDocument();
  });
});

describe("editing a draft", () => {
  const open = async (initial = draft(), extra: Record<string, Handler | Handler[]> = {}) => {
    const backend = workflowBackend(initial, extra);
    renderApp(`/fdrs/${ID}/edit`);
    await screen.findByDisplayValue(initial.fdr_number).catch(() => undefined);
    return backend;
  };
  const save = () => userEvent.click(screen.getByRole("button", { name: "Save changes" }));

  it("loads the draft from the server into the form", async () => {
    await open();
    expect(await screen.findByRole("heading", { name: "Edit FDR" })).toBeInTheDocument();
    expect(field("FDR number")).toHaveValue("WF-DRAFT-1");
    expect(field("Branch name")).toHaveValue("Main Branch");
    expect(field(/principal amount/i)).toHaveValue("1500000.00");
    expect(field("Bank")).toHaveValue(BANKS[0].id);
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it("saves changes with the current row_version and then uses the new one", async () => {
    const { api } = await open();
    await userEvent.clear(field("Purpose"));
    await userEvent.type(field("Purpose"), "Updated purpose");
    await save();
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    await userEvent.clear(field("Purpose"));
    await userEvent.type(field("Purpose"), "Second change");
    await save();
    await waitFor(() => expect(api.callsTo(`PATCH /fdrs/${ID}`)).toHaveLength(2));
    const [first, second] = api.callsTo(`PATCH /fdrs/${ID}`);
    expect(first.body).toMatchObject({ row_version: 1, purpose: "Updated purpose" });
    expect(second.body).toMatchObject({ row_version: 2, purpose: "Second change" });
    expect(Object.keys(first.body as object)).not.toContain("status");
    expect(Object.keys(first.body as object)).not.toContain("institution_id");
  });

  it("validates before saving", async () => {
    const { api } = await open();
    await userEvent.clear(field(/principal amount/i));
    await userEvent.type(field(/principal amount/i), "1.234");
    await save();
    expect(await screen.findByText(/use digits with at most two decimal places/i)).toBeInTheDocument();
    expect(api.callsTo(`PATCH /fdrs/${ID}`)).toHaveLength(0);
  });

  it("explains a stale version and reloads the latest copy on request", async () => {
    let first = true;
    const { api, state } = await open(draft(), {
      [`PATCH /fdrs/${ID}`]: (req) => {
        if (first) {
          first = false;
          state.fdr = draft({ purpose: "Changed elsewhere", row_version: 5 });
          return jsonReply(409, { detail: "This FDR was changed since you loaded it. Reload it and try again." })(req);
        }
        return jsonReply(200, state.fdr)(req);
      },
    });
    await userEvent.clear(field("Purpose"));
    await userEvent.type(field("Purpose"), "My change");
    await save();
    expect(await screen.findByRole("alert")).toHaveTextContent(/changed since you loaded/i);
    await userEvent.click(screen.getByRole("button", { name: "Reload the latest version" }));
    expect(await screen.findByDisplayValue("Changed elsewhere")).toBeInTheDocument();
    expect(api.callsTo(`GET /fdrs/${ID}`).length).toBeGreaterThanOrEqual(2);
  });

  it.each(["SUBMITTED", "ACCEPTED", "REJECTED", "WITHDRAWN"])("shows a %s FDR read-only with no form", async (status) => {
    await open(draft({ status }));
    expect(await screen.findByText(/only drafts and fdrs returned for correction can be edited/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("FDR number")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save|submit/i })).not.toBeInTheDocument();
  });

  it("says plainly when the FDR does not exist or cannot be loaded, and retries", async () => {
    workflowBackend(null);
    renderApp(`/fdrs/${ID}/edit`);
    expect(await screen.findByRole("alert")).toHaveTextContent("This FDR was not found.");
  });

  it("offers a retry when loading fails", async () => {
    let calls = 0;
    workflowBackend(draft(), { [`GET /fdrs/${ID}`]: (req) => (calls++ === 0 ? textReply(500)(req) : jsonReply(200, draft())(req)) });
    renderApp(`/fdrs/${ID}/edit`);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load this fdr/i);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByDisplayValue("WF-DRAFT-1")).toBeInTheDocument();
  });
});

describe("submitting for review", () => {
  const open = async (initial = draft(), extra: Record<string, Handler | Handler[]> = {}) => {
    const backend = workflowBackend(initial, extra);
    renderApp(`/fdrs/${ID}/edit`);
    await screen.findByDisplayValue(initial.fdr_number);
    return backend;
  };
  const submitButton = () => screen.getByRole("button", { name: "Submit for review" });

  it("asks for confirmation first and sends nothing until it is given", async () => {
    const { api } = await open();
    await userEvent.click(submitButton());
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/can no longer be edited/i);
    expect(dialog).toHaveTextContent(/does not verify the fdr with the bank/i);
    expect(api.callsTo(`POST /fdrs/${ID}/submit`)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api.callsTo(`POST /fdrs/${ID}/submit`)).toHaveLength(0);
  });

  it("is blocked while there are unsaved changes", async () => {
    await open();
    await userEvent.type(field("Purpose"), " more");
    expect(submitButton()).toBeDisabled();
    expect(screen.getByText(/unsaved changes\. save them before submitting/i)).toBeInTheDocument();
  });

  it("submits with the current version, then lands on the registry with a confirmation and fresh data", async () => {
    const { api } = await open();
    await userEvent.click(submitButton());
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm submission" }));

    expect(await screen.findByRole("heading", { name: "FDR Registry" })).toBeInTheDocument();
    expect(location()).toHaveTextContent("/fdrs");
    expect(screen.getByText("WF-DRAFT-1 was submitted for review. It can no longer be edited.")).toBeInTheDocument();
    const [post] = api.callsTo(`POST /fdrs/${ID}/submit`);
    expect(post.body).toEqual({ row_version: 1 });
    expect(post.headers["x-csrf-token"]).toBe("csrf-token-for-tests");
    // The registry lists what the server now says, and the draft's edit link is gone.
    const row = (await screen.findByText("WF-DRAFT-1")).closest("tr")!;
    expect(within(row).getByText("Submitted")).toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: "Edit draft" })).not.toBeInTheDocument();
    expect(api.callsTo("GET /fdrs").length).toBeGreaterThanOrEqual(1);
  });

  it("sends the submission once even if confirmed repeatedly", async () => {
    const pending = deferred();
    const { api, state } = await open(draft(), {
      [`POST /fdrs/${ID}/submit`]: async (req) => { await pending.promise; state.fdr = draft({ status: "SUBMITTED", row_version: 2 }); return jsonReply(200, state.fdr)(req) as Response; },
    });
    await userEvent.click(submitButton());
    const confirm = within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm submission" });
    await userEvent.click(confirm);
    const busy = await screen.findByRole("button", { name: "Submitting…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    expect(api.callsTo(`POST /fdrs/${ID}/submit`)).toHaveLength(1);
    pending.resolve();
    await screen.findByRole("heading", { name: "FDR Registry" });
  });

  it("explains missing required details and stays on the page", async () => {
    await open(draft({ branch_name: null }), {
      [`POST /fdrs/${ID}/submit`]: jsonReply(422, { detail: "Required before submission: branch_name" }),
    });
    await userEvent.click(submitButton());
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm submission" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Required before submission: branch_name");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edit FDR" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeEnabled();
  });

  it.each([
    ["a conflict", jsonReply(409, { detail: "Only draft FDRs can be submitted" }), /only draft fdrs can be submitted/i],
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
  ])("reports %s and keeps the draft editable", async (_label, failing, message) => {
    await open(draft(), { [`POST /fdrs/${ID}/submit`]: failing });
    await userEvent.click(submitButton());
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm submission" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("heading", { name: "Edit FDR" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "FDR Registry" })).not.toBeInTheDocument();
  });

  it("returns to sign-in if the session expired during submission", async () => {
    await open(draft(), { [`POST /fdrs/${ID}/submit`]: jsonReply(401, { detail: "Not authenticated" }) });
    await userEvent.click(submitButton());
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm submission" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("whole workflow", () => {
  it("create -> edit -> submit, ending with a registry built only from server data", async () => {
    const { api, state } = workflowBackend();
    renderApp("/fdrs");
    await screen.findByText("No FDR records are available to you yet.");

    await userEvent.click(screen.getByRole("link", { name: "Create FDR" }));
    await screen.findByRole("heading", { name: "Create FDR" });
    await fillForm({ number: "FLOW-1" });
    await saveDraft();
    await screen.findByRole("heading", { name: "Edit FDR" });
    expect(state.fdr?.status).toBe("DRAFT");

    await screen.findByLabelText("Purpose"); // the page shows its heading while the draft is still loading
    await userEvent.clear(field("Purpose"));
    await userEvent.type(field("Purpose"), "Final purpose");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Changes saved.");
    await userEvent.click(screen.getByRole("button", { name: "Submit for review" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm submission" }));

    await screen.findByText(/FLOW-1 was submitted for review/);
    const row = (await screen.findByText("FLOW-1")).closest("tr")!;
    expect(within(row).getByText("Submitted")).toBeInTheDocument();
    expect(state.fdr).toMatchObject({ status: "SUBMITTED", purpose: "Final purpose", row_version: 3 });
    expect(api.callsTo("POST /fdrs")).toHaveLength(1);
    expect(api.callsTo(`POST /fdrs/${ID}/submit`)).toHaveLength(1);
  });
});
