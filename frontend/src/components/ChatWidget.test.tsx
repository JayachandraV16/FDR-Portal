import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { contextFor, MAX_MESSAGE } from "./ChatWidget";
import { deferred, installFakeApi, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { makeFdr, makeSession, makeUser } from "../test/fixtures";
import { renderApp } from "../test/render";

const ID = "3f2b8c1e-5a4d-4e6f-9a7b-1c2d3e4f5a6b";

const reply = (over: Record<string, unknown> = {}) => ({
  reply: "An FDR is a fixed deposit receipt.", source: "built-in", kind: "help", notice: null,
  facts: [], links: [], suggestions: [], ...over,
});

async function open(routes: Record<string, Handler | Handler[]> = {}, path = "/") {
  const api = installFakeApi({
    "GET /auth/me": jsonReply(200, makeSession(makeUser({ role: "AICTE_REVIEWER" }), "csrf-chat")),
    "GET /chat/suggestions": jsonReply(200, { suggestions: ["What does Accepted mean?", "Which FDRs mature soon?"] }),
    "GET /fdrs/:id": jsonReply(200, makeFdr({ id: ID })),
    "GET /fdrs/:id/history": jsonReply(200, { history: [], revisions: [] }),
    ...routes,
  });
  renderApp(path);
  await screen.findByRole("navigation", { name: "Main" });
  return api;
}

const launcher = () => screen.getByRole("button", { name: "Open FDR Copilot" });
const dialog = () => screen.getByRole("dialog", { name: "FDR Copilot" });
async function openPanel() {
  await userEvent.click(launcher());
  return dialog();
}

describe("route context", () => {
  it.each([
    ["/", { page: "overview" }],
    ["/fdrs", { page: "fdrs" }],
    ["/alerts", { page: "alerts" }],
    ["/somewhere-else", { page: "default" }],
    [`/fdrs/${ID}`, { page: "fdr_detail", fdr_id: ID }],
    [`/fdrs/${ID}/edit`, { page: "fdr_edit", fdr_id: ID }],
    [`/review/${ID}`, { page: "review_detail", fdr_id: ID }],
    [`/release/${ID}`, { page: "release_detail", fdr_id: ID }],
    ["/fdrs/not-a-uuid", { page: "fdrs" }],
    ["/fdrs/new", { page: "fdr_new" }],
    ["/calendar", { page: "calendar" }],
    ["/workload", { page: "workload" }],
    ["/security", { page: "security" }],
    ["/import", { page: "import" }],
    ["/admin", { page: "admin" }],
  ])("%s -> %j", (path, expected) => {
    expect(contextFor(path)).toEqual(expected);
  });
});

describe("FDR Copilot panel", () => {
  it("is a closed floating button first, then a labelled dialog that says what it can and cannot do", async () => {
    await open();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const panel = await openPanel();
    expect(panel).toHaveTextContent(/read-only assistant/i);
    expect(panel).toHaveTextContent(/cannot approve, release or change anything/i);
    expect(panel).toHaveTextContent(/nothing here is bank-verified/i);
  });

  it("loads page-specific suggestions only when opened, and a suggestion sends that question", async () => {
    const api = await open({ "POST /chat": jsonReply(200, reply({ reply: "Accepted means accepted after human review." })) });
    expect(api.callsTo("GET /chat/suggestions")).toHaveLength(0);
    const panel = await openPanel();
    await within(panel).findByRole("button", { name: "What does Accepted mean?" });
    expect(api.callsTo("GET /chat/suggestions")[0].query.page).toBe("overview");
    await userEvent.click(within(panel).getByRole("button", { name: "What does Accepted mean?" }));
    expect(await within(panel).findByText("Accepted means accepted after human review.")).toBeInTheDocument();
    const sent = api.callsTo("POST /chat")[0];
    expect(sent.body).toEqual({ message: "What does Accepted mean?", page: "overview" });
    expect(sent.headers["x-csrf-token"]).toBe("csrf-chat");
  });

  it("still works with built-in suggestions when the suggestions request fails", async () => {
    await open({ "GET /chat/suggestions": textReply(500) });
    const panel = await openPanel();
    expect(await within(panel).findByRole("button", { name: "How do I create an FDR?" })).toBeInTheDocument();
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends the FDR being viewed so 'this FDR' can be resolved", async () => {
    const api = await open({ "POST /chat": jsonReply(200, reply()) }, `/fdrs/${ID}`);
    await screen.findByRole("heading", { name: "FDR details" });
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "What is the status of this FDR?{Enter}");
    await waitFor(() => expect(api.callsTo("POST /chat")).toHaveLength(1));
    expect(api.callsTo("POST /chat")[0].body).toEqual({ message: "What is the status of this FDR?", page: "fdr_detail", fdr_id: ID });
  });

  it("shows facts, links and the source label; a notice marks the built-in fallback", async () => {
    await open({
      "POST /chat": jsonReply(200, reply({
        reply: "FDR-9 is Submitted.", kind: "fdr",
        facts: [{ label: "Status", value: "Submitted" }], links: [{ label: "Open FDR-9", path: "/fdrs/x" }],
        notice: "AI provider not configured: this answer comes from the built-in help and your own records.",
      })),
    });
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "status of FDR-9{Enter}");
    expect(await within(panel).findByText("FDR-9 is Submitted.")).toBeInTheDocument();
    expect(within(panel).getByLabelText("Facts from your records")).toHaveTextContent("Status: Submitted");
    expect(within(panel).getByRole("link", { name: "Open FDR-9" })).toHaveAttribute("href", "/fdrs/x");
    expect(panel).toHaveTextContent("Built-in answer");
    expect(panel).toHaveTextContent(/AI provider not configured/);
  });

  it("labels AI-assisted wording as such", async () => {
    await open({ "POST /chat": jsonReply(200, reply({ source: "llm" })) });
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "hello{Enter}");
    expect(await within(panel).findByText(/AI-assisted wording; facts come from the portal's records/)).toBeInTheDocument();
  });

  it("does not send an empty or whitespace-only message", async () => {
    const api = await open({ "POST /chat": jsonReply(200, reply()) });
    const panel = await openPanel();
    expect(within(panel).getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.type(within(panel).getByLabelText("Your question"), "   {Enter}");
    expect(api.callsTo("POST /chat")).toHaveLength(0);
  });

  it("caps the message length and shows the count", async () => {
    await open();
    const panel = await openPanel();
    const input = within(panel).getByLabelText("Your question") as HTMLTextAreaElement;
    expect(input).toHaveAttribute("maxLength", String(MAX_MESSAGE));
    await userEvent.click(input);
    await userEvent.paste("x".repeat(MAX_MESSAGE + 50));
    expect(input.value).toHaveLength(MAX_MESSAGE);
    expect(panel).toHaveTextContent(`${MAX_MESSAGE}/${MAX_MESSAGE}`);
  });

  it("Enter sends, Shift+Enter adds a line, and a second send is ignored while one is in flight", async () => {
    const gate = deferred();
    const api = await open({ "POST /chat": async () => { await gate.promise; return jsonReply(200, reply())({} as never) as Response; } });
    const panel = await openPanel();
    const input = within(panel).getByLabelText("Your question");
    await userEvent.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect((input as HTMLTextAreaElement).value).toBe("line one\nline two");
    await userEvent.type(input, "{Enter}");
    await within(panel).findByText("FDR Copilot is thinking…");
    await userEvent.type(input, "second{Enter}");
    expect(api.callsTo("POST /chat")).toHaveLength(1);
    gate.resolve();
    expect(await within(panel).findByText("An FDR is a fixed deposit receipt.")).toBeInTheDocument();
  });

  it("closes with Escape and returns focus to the launcher", async () => {
    await open();
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(launcher()).toHaveFocus();
  });

  it("clears the conversation", async () => {
    await open({ "POST /chat": jsonReply(200, reply()) });
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "hello{Enter}");
    await within(panel).findByText("An FDR is a fixed deposit receipt.");
    await userEvent.click(within(panel).getByRole("button", { name: "Clear conversation" }));
    expect(within(panel).queryByText("An FDR is a fixed deposit receipt.")).not.toBeInTheDocument();
    expect(panel).toHaveTextContent(/Hello, I'm FDR Copilot/);
  });
});

describe("FDR Copilot failures", () => {
  it.each([
    ["the network is down", networkError, /cannot reach the server/i],
    ["the assistant returns a server error", textReply(500), /assistant is unavailable right now \(HTTP 500\)/i],
  ])("%s: shows a clear error, keeps the portal usable, and retries the same message", async (_name, failing, message) => {
    const api = await open({ "POST /chat": [failing, jsonReply(200, reply({ reply: "Recovered." }))] });
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "What is an FDR?{Enter}");
    expect(await within(panel).findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Try again" }));
    expect(await within(panel).findByText("Recovered.")).toBeInTheDocument();
    expect(api.callsTo("POST /chat").map((c) => (c.body as { message: string }).message)).toEqual(["What is an FDR?", "What is an FDR?"]);
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the server's message when the rate limit is reached", async () => {
    await open({ "POST /chat": jsonReply(429, { detail: "Too many questions. Please wait a few minutes." }) });
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "hello{Enter}");
    expect(await within(panel).findByRole("alert")).toHaveTextContent(/too many questions/i);
  });
});

describe("FDR Copilot with an AI provider", () => {
  async function ask(text: string) {
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), `${text}{Enter}`);
    return panel;
  }

  it("names the provider on general guidance and says it is not verified data", async () => {
    await open({ "POST /chat": jsonReply(200, reply({ source: "llm", provider: "gemini", kind: "unknown", reply: "1. Open FDRs.\n2. Choose Create FDR." })) });
    const panel = await ask("Explain the process");
    expect(await within(panel).findByText(/AI-generated general guidance \(Gemini\)/)).toBeInTheDocument();
    expect(panel).toHaveTextContent(/not verified record data or a compliance decision/i);
    expect(panel).not.toHaveTextContent("Built-in answer");
  });

  it("names the provider on re-worded record answers", async () => {
    await open({ "POST /chat": jsonReply(200, reply({ source: "llm", provider: "gemini", kind: "records" })) });
    const panel = await ask("status of FDR-1");
    expect(await within(panel).findByText(/AI-assisted wording \(Gemini\); facts come from the portal's records/)).toBeInTheDocument();
  });

  it("never claims the AI wrote a built-in fallback, and shows the quota notice without raw provider text", async () => {
    await open({
      "POST /chat": jsonReply(200, reply({ source: "built-in", provider: null, kind: "unknown", reply: "I can't answer that from what I know.",
        notice: "The AI provider's usage limit has been reached for now. This is the built-in answer." })),
    });
    const panel = await ask("something unusual");
    expect(await within(panel).findByText(/I can't answer that/)).toBeInTheDocument();
    expect(panel).toHaveTextContent("Built-in answer");
    expect(panel).toHaveTextContent(/usage limit has been reached/i);
    expect(panel).not.toHaveTextContent(/gemini|RESOURCE_EXHAUSTED/i);
  });

  it("shows a very long answer in full inside a wrapping, scrollable conversation", async () => {
    const long = Array.from({ length: 60 }, (_, i) => `Step ${i + 1}: ${"verylongwordwithoutspaces".repeat(8)}`).join("\n");
    await open({ "POST /chat": jsonReply(200, reply({ reply: long })) });
    const panel = await ask("explain");
    const bubble = (await within(panel).findByText(/Step 60:/)).closest("p")!;
    expect(bubble.textContent).toBe(long);
    expect(bubble.className).toMatch(/break-words/);
    expect(within(panel).getByRole("log").className).toMatch(/overflow-y-auto/);
  });

  it.each([
    ["an empty answer", jsonReply(200, reply({ reply: "   " }))],
    ["a reply that is not JSON", textReply(200, "<html>oops</html>", "text/html")],
    ["a reply with no text at all", jsonReply(200, {})],
  ])("treats %s as a failure with a retry, not a blank bubble or a crash", async (_name, handler) => {
    await open({ "POST /chat": [handler, jsonReply(200, reply({ reply: "Recovered." }))] });
    const panel = await ask("hello there");
    expect(await within(panel).findByRole("alert")).toHaveTextContent(/something went wrong/i);
    expect(panel).not.toHaveTextContent(/html|oops|undefined/i);
    await userEvent.click(within(panel).getByRole("button", { name: "Try again" }));
    expect(await within(panel).findByText("Recovered.")).toBeInTheDocument();
  });

  it("renders a reply that omits its optional lists", async () => {
    await open({ "POST /chat": jsonReply(200, { reply: "Just text.", source: "built-in", kind: "help", notice: null }) });
    const panel = await ask("hi");
    expect(await within(panel).findByText("Just text.")).toBeInTheDocument();
  });

  it("uses a full-width bottom sheet on small screens and a fixed-width panel from the sm breakpoint", async () => {
    await open();
    const panel = await openPanel();
    expect(panel.className).toMatch(/inset-x-3/);
    expect(panel.className).toMatch(/sm:w-\[26rem\]/);
    expect(panel.className).toMatch(/max-h-\[80vh\]/);
  });
});

describe("FDR Copilot language and 'Explain this page'", () => {
  const pick = async (panel: HTMLElement, name: string) => userEvent.selectOptions(within(panel).getByRole("combobox", { name: /Language|भाषा/ }), name);

  it("offers English, Hindi and Marathi, each named in its own script, defaulting to English", async () => {
    await open();
    const panel = await openPanel();
    const select = within(panel).getByRole("combobox", { name: "Language" });
    expect(select).toHaveValue("en");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["English", "हिन्दी", "मराठी"]);
    expect(within(select).getByRole("option", { name: "हिन्दी" })).toHaveAttribute("lang", "hi");
  });

  it("sends the chosen language, renders the Devanagari reply with its lang attribute, and remembers the choice", async () => {
    const api = await open({ "POST /chat": jsonReply(200, reply({ reply: "FDR बनाने के लिए FDRs खोलें।", language: "hi" })) });
    const panel = await openPanel();
    await pick(panel, "hi");
    await userEvent.type(within(panel).getByLabelText("आपका प्रश्न"), "FDR कैसे बनाएं?{Enter}");
    const bubble = await within(panel).findByText("FDR बनाने के लिए FDRs खोलें।");
    expect(bubble).toHaveAttribute("lang", "hi");
    expect(api.callsTo("POST /chat")[0].body).toEqual({ message: "FDR कैसे बनाएं?", page: "overview", language: "hi" });
    expect(localStorage.getItem("fdr-copilot-language")).toBe("hi");
    expect(panel).toHaveAttribute("lang", "hi");
  });

  it("translates the panel's own labels and the suggestions, which are requested for that language", async () => {
    const api = await open({ "GET /chat/suggestions": [jsonReply(200, { suggestions: ["English one"] }), jsonReply(200, { suggestions: ["FDR कैसे बनाएं?"] })] });
    const panel = await openPanel();
    await within(panel).findByRole("button", { name: "English one" });
    await pick(panel, "mr");
    expect(await within(panel).findByRole("button", { name: "FDR कैसे बनाएं?" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "हे पान समजावा" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "पाठवा" })).toBeInTheDocument();
    expect(api.callsTo("GET /chat/suggestions").map((c) => c.query.language)).toEqual([undefined, "mr"]);
  });

  it("switching language keeps the conversation", async () => {
    await open({ "POST /chat": jsonReply(200, reply({ reply: "First answer." })) });
    const panel = await openPanel();
    await userEvent.type(within(panel).getByLabelText("Your question"), "hello{Enter}");
    await within(panel).findByText("First answer.");
    await pick(panel, "hi");
    expect(within(panel).getByText("First answer.")).toBeInTheDocument();
    expect(within(panel).getByText("hello")).toBeInTheDocument();
  });

  it("shows the server's notice when an answer exists only in English", async () => {
    await open({ "POST /chat": jsonReply(200, reply({ reply: "English only.", language: "en", notice: "इस उत्तर का हिंदी संस्करण उपलब्ध नहीं है, इसलिए यह अंग्रेज़ी में दिखाया गया है।" })) });
    const panel = await openPanel();
    await pick(panel, "hi");
    await userEvent.type(within(panel).getByLabelText("आपका प्रश्न"), "x{Enter}");
    expect(await within(panel).findByText("English only.")).toHaveAttribute("lang", "en");
    expect(panel).toHaveTextContent("उपलब्ध नहीं है");
  });

  it("'Explain this page' sends the page and record context as an explain request, with no typed message", async () => {
    const api = await open({ "POST /chat": jsonReply(200, reply({ reply: "This page shows one FDR.", kind: "help" })) }, `/fdrs/${ID}`);
    await screen.findByRole("heading", { name: "FDR details" });
    const panel = await openPanel();
    await userEvent.click(within(panel).getByRole("button", { name: "Explain this page" }));
    expect(await within(panel).findByText("This page shows one FDR.")).toBeInTheDocument();
    expect(api.callsTo("POST /chat")[0].body).toEqual({ message: "Explain this page", page: "fdr_detail", fdr_id: ID, intent: "explain_page" });
  });

  it("'Explain this page' honours the chosen language and is operable from the keyboard", async () => {
    const api = await open({ "POST /chat": jsonReply(200, reply({ reply: "यह ओवरव्यू है।", language: "hi" })) });
    const panel = await openPanel();
    await pick(panel, "hi");
    const button = within(panel).getByRole("button", { name: "इस पेज को समझाएँ" });
    button.focus();
    await userEvent.keyboard("{Enter}");
    expect(await within(panel).findByText("यह ओवरव्यू है।")).toBeInTheDocument();
    expect(api.callsTo("POST /chat")[0].body).toMatchObject({ intent: "explain_page", language: "hi", page: "overview" });
  });

  it("retries a failed 'Explain this page' with the same request", async () => {
    const api = await open({ "POST /chat": [textReply(500), jsonReply(200, reply({ reply: "Recovered help." }))] });
    const panel = await openPanel();
    await userEvent.click(within(panel).getByRole("button", { name: "Explain this page" }));
    await userEvent.click(await within(panel).findByRole("button", { name: "Try again" }));
    expect(await within(panel).findByText("Recovered help.")).toBeInTheDocument();
    expect(api.callsTo("POST /chat").map((c) => (c.body as { intent: string }).intent)).toEqual(["explain_page", "explain_page"]);
  });

  it("disables sending while offline and says so, then recovers", async () => {
    await open();
    const panel = await openPanel();
    const online = vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    window.dispatchEvent(new Event("offline"));
    expect(await within(panel).findByText(/You are offline/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Explain this page" })).toBeDisabled();
    expect(within(panel).getByRole("button", { name: "Send" })).toBeDisabled();
    online.mockReturnValue(true);
    window.dispatchEvent(new Event("online"));
    await waitFor(() => expect(within(panel).queryByText(/You are offline/)).not.toBeInTheDocument());
    expect(within(panel).getByRole("button", { name: "Explain this page" })).toBeEnabled();
  });

  it("has no automated accessibility violations with the language selector and explain button", async () => {
    const { axe } = await import("vitest-axe");
    await open();
    const panel = await openPanel();
    expect(await axe(panel)).toHaveNoViolations();
  });
});
