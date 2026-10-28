import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";

import { deferred, installFakeApi, jsonReply, textReply, type Handler } from "../test/fakeApi";
import { makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const reply = (over: Record<string, unknown> = {}) => ({
  reply: "An answer.", source: "built-in", kind: "help", notice: null, facts: [], links: [], suggestions: [], ...over,
});
const FEEDBACK_OK = jsonReply(200, { recorded: true, duplicate: false, rating: "helpful" });

async function answered(routes: Record<string, Handler | Handler[]> = {}, text = "An answer.") {
  const api = installFakeApi({
    "GET /auth/me": jsonReply(200, makeSession(makeUser({ role: "AICTE_REVIEWER" }), "csrf-chat")),
    "GET /chat/suggestions": jsonReply(200, { suggestions: ["How do I create an FDR?"] }),
    "POST /chat": jsonReply(200, reply({ reply: text })),
    "POST /chat/feedback": FEEDBACK_OK,
    ...routes,
  });
  renderApp("/");
  await screen.findByRole("navigation", { name: "Main" });
  await userEvent.click(screen.getByRole("button", { name: "Open FDR Copilot" }));
  const panel = screen.getByRole("dialog", { name: "FDR Copilot" });
  await userEvent.type(within(panel).getByLabelText("Your question"), "hello{Enter}");
  await within(panel).findByText(text);
  return { api, panel };
}

describe("FDR Copilot answer feedback", () => {
  it("offers 'Helpful' and 'Not helpful' under an answer, as a labelled group", async () => {
    const { panel } = await answered();
    const group = within(panel).getByRole("group", { name: "Was this answer helpful?" });
    expect(within(group).getByRole("button", { name: "Helpful" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Not helpful" })).toBeInTheDocument();
  });

  it("sends 'helpful' with only facts about the answer, never its text or the question", async () => {
    const { api, panel } = await answered(
      { "POST /chat": jsonReply(200, reply({ reply: "Secret answer text", kind: "records", source: "llm", provider: "gemini", language: "hi" })) },
      "Secret answer text",
    );
    await userEvent.click(within(panel).getByRole("button", { name: "Helpful" }));
    expect(await within(panel).findByText("Thank you for your feedback.")).toBeInTheDocument();
    const body = api.callsTo("POST /chat/feedback")[0].body as Record<string, unknown>;
    expect(body).toEqual({ reply_id: expect.stringMatching(/^[0-9a-f-]{36}$/), rating: "helpful", kind: "records", source: "llm", provider: "gemini", language: "hi", page: "overview" });
    expect(JSON.stringify(body)).not.toMatch(/Secret|hello/);
    expect(api.callsTo("POST /chat/feedback")[0].headers["x-csrf-token"]).toBe("csrf-chat");
  });

  it("asks for an optional fixed reason after 'Not helpful' and sends nothing until the user confirms", async () => {
    const { api, panel } = await answered();
    await userEvent.click(within(panel).getByRole("button", { name: "Not helpful" }));
    expect(api.callsTo("POST /chat/feedback")).toHaveLength(0);
    await userEvent.selectOptions(within(panel).getByLabelText("What went wrong? (optional)"), "Unclear");
    await userEvent.click(within(panel).getByRole("button", { name: "Send feedback" }));
    expect(await within(panel).findByText("Thank you for your feedback.")).toBeInTheDocument();
    expect(api.callsTo("POST /chat/feedback")[0].body).toMatchObject({ rating: "not_helpful", reason: "unclear", source: "built-in" });
  });

  it("allows 'Not helpful' without a reason", async () => {
    const { api, panel } = await answered();
    await userEvent.click(within(panel).getByRole("button", { name: "Not helpful" }));
    await userEvent.click(within(panel).getByRole("button", { name: "Send feedback" }));
    await within(panel).findByText("Thank you for your feedback.");
    expect(api.callsTo("POST /chat/feedback")[0].body).not.toHaveProperty("reason");
  });

  it("cannot be sent twice: a double click sends once, and the controls give way to the confirmation", async () => {
    const gate = deferred();
    const { api, panel } = await answered({
      "POST /chat/feedback": async () => {
        await gate.promise;
        return jsonReply(200, { recorded: true, duplicate: false, rating: "helpful" })({} as never) as Response;
      },
    });
    const helpful = within(panel).getByRole("button", { name: "Helpful" });
    await userEvent.dblClick(helpful);
    expect(api.callsTo("POST /chat/feedback")).toHaveLength(1);
    expect(helpful).toBeDisabled();
    gate.resolve();
    await within(panel).findByText("Thank you for your feedback.");
    expect(within(panel).queryByRole("button", { name: "Helpful" })).not.toBeInTheDocument();
  });

  it("gives each answer its own identifier", async () => {
    const { api, panel } = await answered();
    await userEvent.type(within(panel).getByLabelText("Your question"), "two{Enter}");
    await waitFor(() => expect(within(panel).getAllByRole("button", { name: "Helpful" })).toHaveLength(2));
    for (const button of within(panel).getAllByRole("button", { name: "Helpful" })) await userEvent.click(button);
    await waitFor(() => expect(api.callsTo("POST /chat/feedback")).toHaveLength(2));
    const ids = api.callsTo("POST /chat/feedback").map((c) => (c.body as { reply_id: string }).reply_id);
    expect(new Set(ids).size).toBe(2);
  });

  it("says plainly when the server has no storage for feedback, and claims nothing was saved", async () => {
    const { panel } = await answered({ "POST /chat/feedback": jsonReply(503, { detail: "Feedback is not available on this server yet." }) });
    await userEvent.click(within(panel).getByRole("button", { name: "Helpful" }));
    expect(await within(panel).findByText("Feedback is not available on this server yet.")).toBeInTheDocument();
    expect(within(panel).queryByText("Thank you for your feedback.")).not.toBeInTheDocument();
  });

  it("shows an error and allows another try after a failure, then the server's rate-limit message, then success", async () => {
    const { panel } = await answered({
      "POST /chat/feedback": [textReply(500), jsonReply(429, { detail: "You are sending feedback too quickly. Please wait a moment." }), FEEDBACK_OK],
    });
    await userEvent.click(within(panel).getByRole("button", { name: "Helpful" }));
    expect(await within(panel).findByRole("alert")).toHaveTextContent("Feedback could not be sent. Please try again.");
    await userEvent.click(within(panel).getByRole("button", { name: "Helpful" }));
    expect(await within(panel).findByText(/sending feedback too quickly/)).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Helpful" }));
    expect(await within(panel).findByText("Thank you for your feedback.")).toBeInTheDocument();
  });
});

describe("FDR Copilot feedback in Hindi and with the keyboard", () => {
  it("uses the interface language for its labels and sends the reply's language", async () => {
    const api = installFakeApi({
      "GET /auth/me": jsonReply(200, makeSession(makeUser({ role: "AICTE_REVIEWER" }), "csrf-chat")),
      "GET /chat/suggestions": jsonReply(200, { suggestions: ["x"] }),
      "POST /chat": jsonReply(200, reply({ reply: "यह ओवरव्यू है।", language: "hi" })),
      "POST /chat/feedback": FEEDBACK_OK,
    });
    renderApp("/");
    await screen.findByRole("navigation", { name: "Main" });
    await userEvent.click(screen.getByRole("button", { name: "Open FDR Copilot" }));
    const panel = screen.getByRole("dialog", { name: "FDR Copilot" });
    await userEvent.selectOptions(within(panel).getByRole("combobox", { name: "Language" }), "hi");
    await userEvent.click(within(panel).getByRole("button", { name: "इस पेज को समझाएँ" }));
    await within(panel).findByText("यह ओवरव्यू है।");
    await userEvent.click(within(panel).getByRole("button", { name: "उपयोगी" }));
    expect(await within(panel).findByText("आपकी प्रतिक्रिया के लिए धन्यवाद।")).toBeInTheDocument();
    expect(api.callsTo("POST /chat/feedback")[0].body).toMatchObject({ language: "hi" });
  });

  it("is keyboard operable and has no automated accessibility violations", async () => {
    const { panel } = await answered();
    const helpful = within(panel).getByRole("button", { name: "Helpful" });
    helpful.focus();
    expect(helpful).toHaveFocus();
    await userEvent.click(within(panel).getByRole("button", { name: "Not helpful" }));
    expect(await axe(panel)).toHaveNoViolations();
    await userEvent.keyboard("{Tab}");
    expect(document.activeElement).not.toBe(document.body);
  });
});
