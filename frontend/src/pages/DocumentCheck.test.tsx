import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MAX_POLLS, POLL_MS, displayValue, warningText } from "../fdr/DocumentCheck";
import type {
  ComparisonOutcome, DocumentComparison, ExtractedFieldOut, ExtractionJob, ExtractionOut, FieldComparison, JobStatus,
} from "../lib/api";
import { deferred, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { FDR_ID, makeDocument, reviewBackend, submittedFdr } from "../test/reviewBackend";
import { renderApp } from "../test/render";

const DOC_ID = "d0000000-0000-4000-8000-000000000001";
const COMPARISON = `GET /fdrs/${FDR_ID}/documents/${DOC_ID}/comparison`;
const START = `POST /fdrs/${FDR_ID}/documents/${DOC_ID}/extractions`;

afterEach(() => vi.useRealTimers());

const extracted = (name: string, overrides: Partial<ExtractedFieldOut> = {}): ExtractedFieldOut => ({
  name, label: name, status: "EXTRACTED", value: null, currency: null, confidence: "HIGH", page: 1, source: "EMBEDDED_TEXT",
  candidates: [], note: null, raw: null, ...overrides,
});

const side = (outcome: ComparisonOutcome, submitted: string | null, explanation = `explained ${outcome}`) => ({ submitted_value: submitted, outcome, explanation });

const row = (field: string, label: string, found: Partial<ExtractedFieldOut>, current: ReturnType<typeof side>, revision: ReturnType<typeof side> | null = null): FieldComparison => ({
  field, label, extracted: extracted(field, { label, ...found }), current, revision,
});

const extraction = (overrides: Partial<ExtractionOut> = {}): ExtractionOut => ({
  id: "e0000000-0000-4000-8000-000000000001", document_id: DOC_ID, status: "COMPLETED", error_code: null, error_message: null,
  extractor: "pypdf 6.21.2", requested_at: "2026-10-27T10:30:00Z", pages_total: 1, pages_processed: 1, ocr_pages: 0,
  truncated: false, warnings: [], fields: [], ...overrides,
});

const job = (status: JobStatus, overrides: Partial<ExtractionJob> = {}): ExtractionJob => ({
  id: "j0000000-0000-4000-8000-000000000001", document_id: DOC_ID, status, requested_at: "2026-10-27T10:29:00Z",
  started_at: status === "PENDING" ? null : "2026-10-27T10:29:05Z", finished_at: status === "COMPLETED" || status === "FAILED" ? "2026-10-27T10:30:00Z" : null,
  attempts: status === "PENDING" ? 0 : 1, error_code: null, error_message: null, ...overrides,
});

const comparison = (fields: FieldComparison[], overrides: Partial<ExtractionOut> = {}, theJob: ExtractionJob | null = job("COMPLETED")): DocumentComparison => ({
  document_id: DOC_ID, extraction: extraction(overrides), revision_no: 1, revision_submitted_at: "2026-10-26T09:00:00Z", fields, job: theJob,
});
const none: DocumentComparison = { document_id: DOC_ID, extraction: null, revision_no: 1, revision_submitted_at: "2026-10-26T09:00:00Z", fields: [], job: null };

const SAMPLE = comparison([
  row("fdr_number", "FDR / receipt number", { value: "RV-FDR-1" }, side("MATCH", "RV-FDR-1")),
  row("principal_amount", "Principal amount", { value: "1600000.00", currency: "INR" }, side("MISMATCH", "1500000.00")),
  row("issue_date", "Issue date", { status: "AMBIGUOUS", candidates: [{ value: "2026-04-03", page: 1 }, { value: "2026-03-04", page: 1 }], confidence: null, page: null, source: null, note: "date_order_ambiguous" }, side("AMBIGUOUS", "2026-04-03")),
  row("maturity_date", "Maturity date", { status: "NOT_FOUND", confidence: null, page: null, source: null }, side("NOT_FOUND", "2027-01-15", "Maturity date was not found in the document.")),
  row("interest_rate_pct", "Interest rate", { value: "7.10", page: 2, confidence: "MEDIUM", source: "OCR" }, side("NOT_COMPARABLE", null, "No interest rate was submitted to compare with.")),
]);

async function open(role = "AICTE_REVIEWER", handler: Handler | Handler[] = jsonReply(200, SAMPLE), extra: Record<string, Handler | Handler[]> = {}) {
  const backend = reviewBackend(submittedFdr(), { role, documents: [makeDocument({ id: DOC_ID })], extra: { [COMPARISON]: handler, ...extra } });
  renderApp(`/review/${FDR_ID}`);
  await screen.findByText("advice.pdf");
  return backend;
}
/** The results-table row for a field label (the review page has its own "Principal amount" label too). */
const rowFor = (label: string) => within(screen.getByRole("table")).getByText(label).closest("tr")!;
const findRow = async (label: string) => {
  await screen.findByRole("table");
  return rowFor(label);
};
const expand = async () => {
  await userEvent.click(screen.getByRole("button", { name: "Show document check for advice.pdf" }));
};

describe("access and layout", () => {
  it.each(["AICTE_REVIEWER", "AICTE_APPROVER"])("%s sees a collapsed document check and nothing is requested until it is opened", async (role) => {
    const { api } = await open(role);
    expect(screen.getByRole("button", { name: "Show document check for advice.pdf" })).toHaveAttribute("aria-expanded", "false");
    expect(api.callsTo(COMPARISON)).toHaveLength(0);
    await expand();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(api.callsTo(COMPARISON)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Hide document check for advice.pdf" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("is not offered to institution users, who never reach the review side", async () => {
    reviewBackend(submittedFdr({ status: "ACCEPTED" }), { role: "INSTITUTION_USER", documents: [makeDocument({ id: DOC_ID })] });
    renderApp(`/fdrs/${FDR_ID}/edit`);
    expect(await screen.findByText("advice.pdf")).toBeInTheDocument();
    expect(screen.queryByText(/document check/i)).not.toBeInTheDocument();
  });

  it("states plainly that the result is a reading aid and not verification", async () => {
    await open();
    await expand();
    const note = await screen.findByText(/a reading aid, not a verification/i);
    expect(note.closest("p")).toHaveTextContent(/does not show that the document is genuine or that the bank issued it/i);
    expect(note.closest("p")).toHaveTextContent(/changes nothing about the FDR/i);
  });
});

describe("results", () => {
  it("shows submitted and found values side by side with an explicit result, confidence and page for every field", async () => {
    await open();
    await expand();
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
      ["Field", "Submitted", "Found in document", "Result", "Confidence", "Page"]);

    const number = within(rowFor("FDR / receipt number"));
    expect(number.getByText("Match")).toBeInTheDocument();
    expect(number.getAllByText("RV-FDR-1")).toHaveLength(2); // submitted and found
    expect(number.getByText("High")).toBeInTheDocument();
    expect(number.getByText("Page 1")).toBeInTheDocument();

    const amount = within(rowFor("Principal amount"));
    expect(amount.getByText("₹15,00,000.00")).toBeInTheDocument();
    expect(amount.getByText("₹16,00,000.00")).toBeInTheDocument();
    expect(amount.getByText("Mismatch")).toBeInTheDocument();

    const rate = within(rowFor("Interest rate"));
    expect(rate.getByText("Not provided")).toBeInTheDocument();
    expect(rate.getByText("7.10%")).toBeInTheDocument();
    expect(rate.getByText("Not comparable")).toBeInTheDocument();
    expect(rate.getByText("Medium")).toBeInTheDocument();
    expect(rate.getByText("read by OCR")).toBeInTheDocument();
    expect(rate.getByText("Page 2")).toBeInTheDocument();
  });

  it("highlights each kind of outcome on its row and says the outcome in words", async () => {
    await open();
    await expand();
    await screen.findByRole("table");
    const outcomes = Object.fromEntries(screen.getAllByRole("row").slice(1).map((r) => [r.querySelector("th")!.textContent, r.getAttribute("data-outcome")]));
    expect(outcomes).toEqual({
      "FDR / receipt number": "MATCH", "Principal amount": "MISMATCH", "Issue date": "AMBIGUOUS",
      "Maturity date": "NOT_FOUND", "Interest rate": "NOT_COMPARABLE",
    });
    expect(rowFor("Principal amount")).toHaveClass("bg-red-50");
    expect(rowFor("Issue date")).toHaveClass("bg-amber-50");
  });

  it("shows both readings of an ambiguous value and never picks one", async () => {
    await open();
    await expand();
    const issue = within(await findRow("Issue date"));
    expect(issue.getByText("Could be:")).toBeInTheDocument();
    expect(issue.getAllByText("3 Apr 2026")).toHaveLength(2); // the submitted date, and the first reading
    expect(issue.getAllByText("4 Mar 2026")).toHaveLength(1); // the second reading
    expect(issue.getByText("Ambiguous")).toBeInTheDocument();
    expect(issue.queryByText("Match")).not.toBeInTheDocument();
  });

  it("never presents a value that was not found as a match", async () => {
    await open();
    await expand();
    const maturity = within(await findRow("Maturity date"));
    expect(maturity.getByText("Not found in document")).toBeInTheDocument();
    expect(maturity.getByText("Not found")).toBeInTheDocument();
    expect(maturity.queryByText("Match")).not.toBeInTheDocument();
    expect(maturity.getByText("Maturity date was not found in the document.")).toBeInTheDocument();
  });

  it("shows how the document compares with the frozen submission when that differs from the current record", async () => {
    const data = comparison([
      row("principal_amount", "Principal amount", { value: "1500000.00" }, side("MISMATCH", "1600000.00"), side("MATCH", "1500000.00", "The amounts are equal to the paisa.")),
      row("fdr_number", "FDR / receipt number", { value: "A-1" }, side("MATCH", "A-1"), side("MATCH", "A-1")),
    ]);
    await open("AICTE_REVIEWER", jsonReply(200, data));
    await expand();
    expect(await findRow("Principal amount")).toHaveTextContent("Against the submitted version (revision 1, ₹15,00,000.00): Match. The amounts are equal to the paisa.");
    expect(within(rowFor("FDR / receipt number")).queryByText(/Against the submitted version/)).not.toBeInTheDocument();
  });

  it("describes the run: when, with what, how many pages, and what was read by OCR", async () => {
    const data = comparison(SAMPLE.fields, { pages_total: 3, pages_processed: 2, ocr_pages: 1, warnings: ["pages_without_text:2"], truncated: true });
    await open("AICTE_REVIEWER", jsonReply(200, data));
    await expand();
    expect(await screen.findByText(/Checked 27 Oct 2026, 10:30 UTC with pypdf 6.21.2\. 2 of 3 pages read, 1 by OCR\./)).toBeInTheDocument();
    expect(screen.getByText(/can contain misread characters/i)).toBeInTheDocument();
    expect(screen.getByText(/only the first part of its text was analysed/i)).toBeInTheDocument();
    expect(screen.getByText("No readable text was found on page 2.")).toBeInTheDocument();
  });

  it("formats values for reading without altering them", () => {
    expect(displayValue("principal_amount", "1500000.10")).toBe("₹15,00,000.10");
    expect(displayValue("issue_date", "2026-01-15")).toBe("15 Jan 2026");
    expect(displayValue("interest_rate_pct", "7.10")).toBe("7.10%");
    expect(displayValue("fdr_number", "ab-12/x9")).toBe("ab-12/x9");
    expect(displayValue("branch_name", null)).toBe("Not provided");
  });
});

describe("Hindi and Marathi documents", () => {
  const HINDI = comparison([
    row("fdr_number", "FDR / receipt number", { value: "१२३४५६७८९०१" }, side("MISMATCH", "12345678901", "The number is printed in Devanagari digits. Identifiers are never converted, so it is not treated as equal to the submitted number; compare them by eye.")),
    row("principal_amount", "Principal amount", { value: "1500000.00", currency: "INR", raw: "रु. १५,००,०००.००" }, side("MATCH", "1500000.00")),
    row("issue_date", "Issue date", { value: "2026-01-15", raw: "१५ जनवरी २०२६" }, side("MATCH", "2026-01-15")),
    row("maturity_date", "Maturity date", { status: "AMBIGUOUS", candidates: [{ value: "2027-03-04", page: 1 }, { value: "2027-04-03", page: 1 }], confidence: null, page: null, source: null, note: "date_order_ambiguous", raw: "०३/०४/२०२७" }, side("AMBIGUOUS", "2027-04-03")),
    row("branch_name", "Branch", { value: "पुणे कैंप" }, side("NOT_COMPARABLE", "Pune Camp", "The names are written in different scripts (Devanagari and Latin). They are not transliterated, so compare them by eye.")),
  ]);

  it("shows the value as printed next to the parsed one, and never converts an identifier", async () => {
    await open("AICTE_REVIEWER", jsonReply(200, HINDI));
    await expand();
    const amount = within(await findRow("Principal amount"));
    expect(amount.getByText("₹15,00,000.00", { selector: "strong" })).toBeInTheDocument();
    expect(amount.getByText("रु. १५,००,०००.००")).toBeInTheDocument();
    expect(amount.getByText(/As printed:/)).toBeInTheDocument();
    expect(within(rowFor("Issue date")).getByText("१५ जनवरी २०२६")).toBeInTheDocument();
    const number = within(rowFor("FDR / receipt number"));
    expect(number.getByText("१२३४५६७८९०१")).toBeInTheDocument();
    expect(number.queryByText(/As printed:/)).not.toBeInTheDocument(); // an identifier is shown as printed already
    expect(number.getByText("Mismatch")).toBeInTheDocument();
    expect(number.getByText(/printed in Devanagari digits/i)).toBeInTheDocument();
  });

  it("shows an ambiguous numeric date with both readings and the printed text, and names the script problem for names", async () => {
    await open("AICTE_REVIEWER", jsonReply(200, HINDI));
    await expand();
    const maturity = within(await findRow("Maturity date"));
    expect(maturity.getByText("4 Mar 2027")).toBeInTheDocument();
    expect(maturity.getAllByText("3 Apr 2027").length).toBeGreaterThanOrEqual(1);
    expect(maturity.getByText("०३/०४/२०२७")).toBeInTheDocument();
    expect(maturity.getByText("Ambiguous")).toBeInTheDocument();
    const branch = within(rowFor("Branch"));
    expect(branch.getByText("पुणे कैंप")).toBeInTheDocument();
    expect(branch.getByText("Not comparable")).toBeInTheDocument();
    expect(branch.getByText(/written in different scripts/i)).toBeInTheDocument();
  });

  it("tells the reviewer when OCR language data is missing, in words", async () => {
    const message = "Text recognition (OCR) is installed, but language data it was configured to use is not installed, so this document was not read. Install the language data or change OCR_LANGUAGE. Review it by eye.";
    const failed = comparison([], { status: "FAILED", error_code: "OCR_LANGUAGE_UNAVAILABLE", error_message: message, warnings: ["missing_ocr_languages:mar"] },
      job("FAILED", { error_code: "OCR_LANGUAGE_UNAVAILABLE", error_message: message }));
    await open("AICTE_REVIEWER", jsonReply(200, failed));
    await expand();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/language data it was configured to use is not installed/i);
    expect(alert).toHaveTextContent("OCR language data is not installed on the server: mar.");
    expect(alert).toHaveTextContent("Reason code: OCR_LANGUAGE_UNAVAILABLE");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("explains known warnings and ignores unknown ones rather than printing them", () => {
    expect(warningText("pages_without_text:1,3")).toBe("No readable text was found on page 1, 3.");
    expect(warningText("missing_ocr_languages:mar,hin")).toBe("OCR language data is not installed on the server: mar,hin.");
    expect(warningText("something_internal")).toBeNull();
  });
});

describe("running the check in the background", () => {
  /** A stand-in for the server's job: the test moves it along, the page has to notice. */
  function backend() {
    const state = { data: none as DocumentComparison, starts: 0 };
    const start: Handler = (req) => {
      state.starts += 1;
      if (state.data.job && (state.data.job.status === "PENDING" || state.data.job.status === "RUNNING")) return jsonReply(200, state.data.job)(req);
      state.data = { ...state.data, job: job("PENDING") };
      return jsonReply(202, state.data.job)(req);
    };
    return { state, get: ((req) => jsonReply(200, state.data)(req)) as Handler, start };
  }
  const tick = async (ms = POLL_MS) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  };

  it("accepts the request, shows queued then running, then the results, without ever showing 'Loading' again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const b = backend();
    const { api } = await open("AICTE_REVIEWER", b.get, { [START]: b.start });
    await expand();
    expect(await screen.findByText("No document check has been run for this file yet.")).toBeInTheDocument();
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Run document check" }));
    expect(await screen.findByText(/Document check queued/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check in progress" })).toBeDisabled();
    expect(api.callsTo(START)).toHaveLength(1);

    b.state.data = { ...b.state.data, job: job("RUNNING") };
    await tick();
    expect(await screen.findByText(/Document check running/)).toBeInTheDocument();
    expect(screen.queryByText("Loading the document check…")).not.toBeInTheDocument();

    b.state.data = { ...SAMPLE, job: job("COMPLETED") };
    await tick();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByText(/Document check (queued|running)/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
    const callsAfterDone = api.callsTo(COMPARISON).length;
    await tick(POLL_MS * 5);
    expect(api.callsTo(COMPARISON).length).toBe(callsAfterDone); // polling stopped on its own
    expect(api.callsTo(START)).toHaveLength(1);
  });

  it("sends nothing but the request itself: no body, a CSRF token, and no status of any kind", async () => {
    const b = backend();
    const { api } = await open("AICTE_APPROVER", b.get, { [START]: b.start });
    await expand();
    await userEvent.click(await screen.findByRole("button", { name: "Run document check" }));
    await screen.findByText(/Document check queued/);
    expect(api.callsTo(START)[0].body).toBeUndefined();
    expect(api.callsTo(START)[0].headers["x-csrf-token"]).toBe("csrf-token-for-tests");
  });

  it("follows an existing job when the server answers 'already queued' (200)", async () => {
    const b = backend();
    b.state.data = { ...none, job: job("RUNNING") };
    const { api } = await open("AICTE_REVIEWER", b.get, { [START]: b.start });
    await expand();
    expect(await screen.findByText(/Document check running/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check in progress" })).toBeDisabled(); // cannot even ask again
    expect(api.callsTo(START)).toHaveLength(0);
  });

  it("sends a double-clicked retry only once", async () => {
    const gate = deferred();
    const b = backend();
    const message = "Analysis took too long and was stopped. You can try again.";
    b.state.data = comparison([], { status: "FAILED", error_code: "TIMEOUT", error_message: message }, job("FAILED", { error_code: "TIMEOUT", error_message: message }));
    const { api } = await open("AICTE_REVIEWER", b.get, { [START]: async (req) => { await gate.promise; return b.start(req) as Response; } });
    await expand();
    const retry = await screen.findByRole("button", { name: "Retry document check" });
    await userEvent.dblClick(retry);
    expect(await screen.findByRole("button", { name: "Requesting…" })).toBeDisabled();
    expect(api.callsTo(START)).toHaveLength(1);
    gate.resolve();
    expect(await screen.findByText(/Document check queued/)).toBeInTheDocument();
    expect(api.callsTo(START)).toHaveLength(1);
  });

  it("explains a failed job with its sanitized reason and a working retry", async () => {
    const b = backend();
    const message = "The analysis was interrupted because the server stopped or restarted. You can try again.";
    b.state.data = { ...none, job: job("FAILED", { error_code: "INTERRUPTED", error_message: message }) };
    const { api } = await open("AICTE_REVIEWER", b.get, { [START]: b.start });
    await expand();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The document could not be checked.");
    expect(alert).toHaveTextContent(message);
    expect(alert).toHaveTextContent("Reason code: INTERRUPTED");
    await userEvent.click(screen.getByRole("button", { name: "Retry document check" }));
    expect(await screen.findByText(/Document check queued/)).toBeInTheDocument();
    expect(api.callsTo(START)).toHaveLength(1);
    expect(screen.queryByText("Reason code: INTERRUPTED")).not.toBeInTheDocument();
  });

  it("keeps earlier results visible, and says so, when the latest attempt failed", async () => {
    const b = backend();
    b.state.data = comparison(SAMPLE.fields, {}, job("FAILED", { error_code: "INTERRUPTED", error_message: "The analysis was interrupted because the server stopped or restarted. You can try again." }));
    await open("AICTE_REVIEWER", b.get, { [START]: b.start });
    await expand();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("The results below are from an earlier check.");
    expect(screen.getByRole("button", { name: "Retry document check" })).toBeEnabled();
  });

  it("keeps showing earlier results, marked as previous, while a new check is queued", async () => {
    const b = backend();
    b.state.data = comparison(SAMPLE.fields, {}, job("PENDING"));
    await open("AICTE_REVIEWER", b.get, { [START]: b.start });
    await expand();
    expect(await screen.findByText(/Previous check: Checked/)).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText(/Document check queued/)).toBeInTheDocument();
  });

  it.each([
    ["the queue is full", jsonReply(503, { detail: "The extraction queue is full. Try again in a moment." }), /extraction queue is full/i],
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted/i],
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
  ])("reports %s when starting, and the button is usable again", async (_label, failing, message) => {
    await open("AICTE_REVIEWER", jsonReply(200, none), { [START]: failing });
    await expand();
    await userEvent.click(await screen.findByRole("button", { name: "Run document check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Run document check" })).toBeEnabled();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows a load failure as an error with a retry, never as an empty result", async () => {
    let calls = 0;
    await open("AICTE_REVIEWER", (req) => (calls++ === 0 ? textReply(500)(req) : jsonReply(200, SAMPLE)(req)));
    await expand();
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load the document check/i);
    expect(screen.queryByText("No document check has been run for this file yet.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  });

  it("survives a failed status refresh: keeps what is shown, says so, and carries on", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const b = backend();
    b.state.data = { ...none, job: job("RUNNING") };
    let broken = false;
    await open("AICTE_REVIEWER", (req) => (broken ? networkError(req) : b.get(req)), { [START]: b.start });
    await expand();
    expect(await screen.findByText(/Document check running/)).toBeInTheDocument();
    broken = true;
    await tick();
    expect(await screen.findByText(/Could not refresh the status just now/)).toBeInTheDocument();
    expect(screen.getByText(/Document check running/)).toBeInTheDocument();
    broken = false;
    b.state.data = { ...SAMPLE, job: job("COMPLETED") };
    await tick();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByText(/Could not refresh the status/)).not.toBeInTheDocument();
  });

  it("stops polling after a while, says the check is slow, and can look again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const b = backend();
    b.state.data = { ...none, job: job("RUNNING") };
    const { api } = await open("AICTE_REVIEWER", b.get, { [START]: b.start });
    await expand();
    await screen.findByText(/Document check running/);
    for (let i = 0; i < MAX_POLLS + 2; i++) await tick();
    expect(await screen.findByText(/taking longer than usual/)).toBeInTheDocument();
    const settled = api.callsTo(COMPARISON).length;
    expect(settled).toBeLessThanOrEqual(MAX_POLLS + 2);
    await tick(POLL_MS * 4);
    expect(api.callsTo(COMPARISON).length).toBe(settled); // it stopped asking
    b.state.data = { ...SAMPLE, job: job("COMPLETED") };
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Check status again" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("stops polling when the panel is closed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const b = backend();
    b.state.data = { ...none, job: job("RUNNING") };
    const { api } = await open("AICTE_REVIEWER", b.get, { [START]: b.start });
    await expand();
    await screen.findByText(/Document check running/);
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Hide document check for advice.pdf" }));
    const before = api.callsTo(COMPARISON).length;
    await tick(POLL_MS * 5);
    expect(api.callsTo(COMPARISON).length).toBe(before);
  });

  it("keeps the review decision separate: a check never decides, navigates, or disables the decision", async () => {
    const b = backend();
    const { api, store } = await open("AICTE_APPROVER", b.get, { [START]: b.start });
    await expand();
    await userEvent.click(await screen.findByRole("button", { name: "Run document check" }));
    await screen.findByText(/Document check queued/);
    for (const action of ["request-correction", "accept", "reject"]) expect(api.callsTo(`POST /fdrs/${FDR_ID}/${action}`)).toHaveLength(0);
    expect(store.fdr?.status).toBe("SUBMITTED");
    expect(screen.getByRole("heading", { name: "Review FDR" })).toBeInTheDocument();
    const decision = screen.getByRole("heading", { name: "Decision" }).closest("section")!;
    expect(within(decision).getByRole("button", { name: "Accept" })).toBeEnabled();
    expect(within(decision).getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(decision).not.toContainElement(screen.getByText(/Document check queued/));
  });
});
