import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_UPLOAD_BYTES, problemWithFile } from "../fdr/DocumentsPanel";
import { formatBytes } from "../lib/format";
import { deferred, jsonReply, networkError, textReply, type Handler } from "../test/fakeApi";
import { FDR_ID, entry, makeDocument, reviewBackend, submittedFdr, submittedHistory } from "../test/reviewBackend";
import { renderApp } from "../test/render";

const LIST = `GET /fdrs/${FDR_ID}/documents`;
const UPLOAD = `POST /fdrs/${FDR_ID}/documents`;
const DOC_ID = "d0000000-0000-4000-8000-000000000001";
const CONTENT = `GET /fdrs/${FDR_ID}/documents/${DOC_ID}/content`;
const REMOVE = `DELETE /fdrs/${FDR_ID}/documents/${DOC_ID}`;

const pdf = (name = "advice.pdf", size = 2048) => {
  const file = new File(["%PDF-1.4 synthetic"], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: size });
  return file;
};
const user = () => userEvent.setup({ applyAccept: false }); // the server, not the picker, is the authority

let created: string[];
let revoked: string[];
let anchors: Array<{ href: string; download: string }>;

beforeEach(() => {
  created = [];
  revoked = [];
  anchors = [];
  let n = 0;
  vi.stubGlobal("URL", Object.assign(URL, {
    createObjectURL: vi.fn(() => { created.push(`blob:test-${++n}`); return created.at(-1)!; }),
    revokeObjectURL: vi.fn((url: string) => { revoked.push(url); }),
  }));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    anchors.push({ href: this.href, download: this.download });
  });
});
afterEach(() => vi.restoreAllMocks());

const draftFdr = (status = "DRAFT") => submittedFdr({ status, row_version: 1, submitted_at: null });

async function openEdit(options: Parameters<typeof reviewBackend>[1] = {}, status = "DRAFT") {
  const backend = reviewBackend(draftFdr(status), { role: "INSTITUTION_USER", ...options });
  renderApp(`/fdrs/${FDR_ID}/edit`);
  await screen.findByRole("heading", { name: "Documents" });
  return backend;
}

const choose = async (file: File, category?: string) => {
  const u = user();
  if (category) await u.selectOptions(screen.getByLabelText("Document type"), category);
  await u.upload(screen.getByLabelText("Document file"), file);
  return u;
};

describe("listing", () => {
  it("shows category, name, size, uploader and upload date, and states that nothing is verified", async () => {
    await openEdit({
      documents: [makeDocument({ category: "SECURITY_DEPOSIT_PROOF", original_filename: "proof.png", content_type: "image/png", size_bytes: 1536, uploaded_by_name: "Asha Patil", uploaded_at: "2026-10-26T08:30:00Z" })],
    });
    const item = (await screen.findByText("proof.png")).closest("li")!;
    expect(within(item).getByText("Security deposit proof · 1.5 KB")).toBeInTheDocument();
    expect(within(item).getByText("Uploaded by Asha Patil · 26 Oct 2026, 08:30 UTC")).toBeInTheDocument();
    expect(screen.getByText(/have not been verified by aicte or by the bank/i)).toBeInTheDocument();
  });

  it("has an honest empty state", async () => {
    await openEdit();
    expect(await screen.findByText("No documents have been uploaded yet.")).toBeInTheDocument();
  });

  it("shows a load failure as an error with a retry, never as an empty list", async () => {
    let calls = 0;
    await openEdit({ extra: { [LIST]: (req) => (calls++ === 0 ? textReply(500)(req) : jsonReply(200, { items: [makeDocument()] })(req)) } });
    expect(await screen.findByText(/could not load the documents/i)).toBeInTheDocument();
    expect(screen.queryByText("No documents have been uploaded yet.")).not.toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("heading", { name: "Documents" }).closest("section")!).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("advice.pdf")).toBeInTheDocument();
  });
});

describe("uploading", () => {
  it("sends the file as the raw body with its category and encoded name, then refreshes the list", async () => {
    const { api, store } = await openEdit();
    await screen.findByText("No documents have been uploaded yet.");
    const file = pdf("Résumé 1.pdf");
    await choose(file, "SUPPORTING_LETTER");
    expect(screen.getByTestId("selected-file")).toHaveTextContent("Selected: Résumé 1.pdf (2.0 KB)");
    await userEvent.click(screen.getByRole("button", { name: "Upload document" }));

    expect(await screen.findByText("Résumé 1.pdf was uploaded.")).toBeInTheDocument();
    const [call] = api.callsTo(UPLOAD);
    expect(call.query).toEqual({ category: "SUPPORTING_LETTER" });
    expect(call.rawBody).toBe(file);
    expect(call.body).toBeUndefined(); // not JSON, not multipart
    expect(call.headers["x-filename"]).toBe("R%C3%A9sum%C3%A9%201.pdf");
    expect(call.headers["content-type"]).toBe("application/pdf");
    expect(call.headers["x-csrf-token"]).toBe("csrf-token-for-tests");
    expect(call.credentials).toBe("same-origin");
    // the list was fetched again and now shows the stored document
    expect(api.callsTo(LIST).length).toBe(2);
    expect(await screen.findByText("Supporting letter · 2.0 KB")).toBeInTheDocument();
    expect(store.documents).toHaveLength(1);
    // the selection is cleared and the form is ready for another file
    expect(screen.queryByTestId("selected-file")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload document" })).toBeDisabled();
  });

  it("shows a loading state, locks the form, and sends a double click only once", async () => {
    const gate = deferred();
    const { api } = await openEdit({
      extra: { [UPLOAD]: async (req) => { await gate.promise; return jsonReply(201, makeDocument({ original_filename: "slow.pdf" }))(req); } },
    });
    const u = await choose(pdf("slow.pdf"));
    const button = screen.getByRole("button", { name: "Upload document" });
    await u.dblClick(button);
    expect(await screen.findByRole("button", { name: "Uploading…" })).toBeDisabled();
    expect(screen.getByLabelText("Document file")).toBeDisabled();
    expect(screen.getByLabelText("Document type")).toBeDisabled();
    expect(api.callsTo(UPLOAD)).toHaveLength(1);
    gate.resolve();
    expect(await screen.findByText("slow.pdf was uploaded.")).toBeInTheDocument();
    expect(api.callsTo(UPLOAD)).toHaveLength(1);
  });

  it.each([
    ["an unsupported type", jsonReply(415, { detail: "Only PDF, JPEG and PNG files are accepted" }), /only pdf, jpeg and png files are accepted/i],
    ["a mismatched file", jsonReply(415, { detail: "The file's content does not match its extension" }), /does not match its extension/i],
    ["a file the server finds too large", jsonReply(413, { detail: "The file is larger than 10 MB" }), /larger than 10 mb/i],
    ["a record that has moved on", jsonReply(409, { detail: "Documents can only be changed while the FDR is a draft or has been returned for correction" }), /draft or has been returned/i],
    ["a full record", jsonReply(409, { detail: "An FDR can have at most 20 documents" }), /at most 20 documents/i],
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted/i],
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
  ])("reports %s and lets the user try again", async (_label, failing, message) => {
    const { api } = await openEdit({ extra: { [UPLOAD]: [failing, (req) => jsonReply(201, makeDocument())(req)] } });
    await choose(pdf());
    await userEvent.click(screen.getByRole("button", { name: "Upload document" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText(/was uploaded/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload document" })).toBeEnabled();
    expect(screen.getByTestId("selected-file")).toBeInTheDocument(); // the choice is kept
    await userEvent.click(screen.getByRole("button", { name: "Upload document" }));
    expect(await screen.findByText("advice.pdf was uploaded.")).toBeInTheDocument();
    expect(api.callsTo(UPLOAD)).toHaveLength(2);
  });

  it.each([
    ["notes.txt", 100, /only pdf, jpeg and png/i],
    ["setup.exe", 100, /only pdf, jpeg and png/i],
    ["picture.svg", 100, /only pdf, jpeg and png/i],
    ["advice.pdf", 0, /empty/i],
    ["huge.pdf", MAX_UPLOAD_BYTES + 1, /limit is 10\.0 MB/i],
  ])("catches %s before uploading anything", async (name, size, message) => {
    const { api } = await openEdit();
    await choose(pdf(name, size));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Upload document" })).toBeDisabled();
    expect(api.callsTo(UPLOAD)).toHaveLength(0);
  });

  it("accepts the supported extensions in any letter case", () => {
    for (const name of ["a.pdf", "A.PDF", "b.jpg", "b.JPEG", "c.png", "scan.final.Png"]) expect(problemWithFile(pdf(name))).toBeNull();
    expect(problemWithFile(pdf("a.pdf", MAX_UPLOAD_BYTES))).toBeNull();
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("clears the earlier problem when a valid file is chosen", async () => {
    await openEdit();
    const u = await choose(pdf("notes.txt"));
    await screen.findByRole("alert");
    await u.upload(screen.getByLabelText("Document file"), pdf("advice.pdf"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload document" })).toBeEnabled();
  });

  it("offers every category", async () => {
    await openEdit();
    const options = within(screen.getByLabelText("Document type")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["FDR certificate / receipt", "Security deposit proof", "Supporting letter", "Other supporting document"]);
  });
});

describe("downloading and previewing", () => {
  const withFile = (type: string, _name: string, bytes = "synthetic bytes"): Handler => () =>
    new Response(new Blob([bytes], { type }), { status: 200, headers: { "Content-Type": type } });

  it("downloads through the API and hands the browser a blob named after the document", async () => {
    const { api } = await openEdit({ documents: [makeDocument()], extra: { [CONTENT]: withFile("application/pdf", "advice.pdf") } });
    await userEvent.click(await screen.findByRole("button", { name: "Download advice.pdf" }));
    await waitFor(() => expect(anchors).toHaveLength(1));
    expect(anchors[0]).toEqual({ href: "blob:test-1", download: "advice.pdf" });
    expect(api.callsTo(CONTENT)).toHaveLength(1);
    expect(api.callsTo(CONTENT)[0].headers.accept).toBe("*/*");
    await waitFor(() => expect(revoked).toEqual(["blob:test-1"]));
    expect(document.querySelector("a[download]")).toBeNull(); // the temporary link is removed
  });

  it("shows a missing stored file as a clear error", async () => {
    await openEdit({ documents: [makeDocument()], extra: { [CONTENT]: jsonReply(404, { detail: "The stored file for this document is unavailable" }) } });
    await userEvent.click(await screen.findByRole("button", { name: "Download advice.pdf" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The stored file for this document is unavailable");
    expect(anchors).toHaveLength(0);
  });

  it.each([
    ["a permission error", jsonReply(403, { detail: "Forbidden" }), /not permitted/i],
    ["a server error", textReply(500), /HTTP 500/],
    ["an unreachable server", networkError, /cannot reach the server/i],
  ])("reports %s while downloading", async (_label, failing, message) => {
    await openEdit({ documents: [makeDocument()], extra: { [CONTENT]: failing } });
    await userEvent.click(await screen.findByRole("button", { name: "Download advice.pdf" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("previews images in the page, and closes the preview and releases the blob", async () => {
    const png = makeDocument({ id: DOC_ID, original_filename: "receipt.png", content_type: "image/png" });
    await openEdit({ documents: [png], extra: { [CONTENT]: withFile("image/png", "receipt.png") } });
    await userEvent.click(await screen.findByRole("button", { name: "Preview receipt.png" }));
    const image = await screen.findByRole("img", { name: "Preview of receipt.png" });
    expect(image).toHaveAttribute("src", "blob:test-1");
    await userEvent.click(screen.getByRole("button", { name: "Close preview" }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    await waitFor(() => expect(revoked).toContain("blob:test-1"));
  });

  it("offers a preview for images only; a PDF can only be downloaded", async () => {
    await openEdit({ documents: [makeDocument(), makeDocument({ id: "d0000000-0000-4000-8000-000000000002", original_filename: "scan.jpg", content_type: "image/jpeg" })] });
    expect(await screen.findByRole("button", { name: "Preview scan.jpg" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Preview advice.pdf" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download advice.pdf" })).toBeInTheDocument();
  });

  it("disables the other buttons while one transfer is running", async () => {
    const gate = deferred();
    await openEdit({
      documents: [makeDocument()],
      extra: { [CONTENT]: async (req) => { await gate.promise; return withFile("application/pdf", "advice.pdf")(req); } },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Download advice.pdf" }));
    expect(screen.getByRole("button", { name: "Download advice.pdf" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove advice.pdf" })).toBeDisabled();
    gate.resolve();
    await waitFor(() => expect(screen.getByRole("button", { name: "Download advice.pdf" })).toBeEnabled());
  });
});

describe("removing", () => {
  it("asks first, sends nothing until confirmed, then refreshes the list", async () => {
    const { api, store } = await openEdit({ documents: [makeDocument()], extra: { [REMOVE]: () => { store.documents.length = 0; return new Response(null, { status: 204 }); } } });
    await userEvent.click(await screen.findByRole("button", { name: "Remove advice.pdf" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(api.callsTo(REMOVE)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api.callsTo(REMOVE)).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Remove advice.pdf" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm removal" }));
    expect(await screen.findByText("advice.pdf was removed.")).toBeInTheDocument();
    expect(api.callsTo(REMOVE)).toHaveLength(1);
    expect(await screen.findByText("No documents have been uploaded yet.")).toBeInTheDocument();
  });

  it("keeps the document and explains when removal is refused", async () => {
    await openEdit({ documents: [makeDocument()], extra: { [REMOVE]: jsonReply(409, { detail: "Documents can only be changed while the FDR is a draft or has been returned for correction" }) } });
    await userEvent.click(await screen.findByRole("button", { name: "Remove advice.pdf" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm removal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/draft or has been returned/i);
    expect(screen.getByText("advice.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});

describe("who can change documents", () => {
  it("lets the institution manage documents on a returned record", async () => {
    await openEdit({ documents: [makeDocument()], history: [...submittedHistory(), entry("CORRECTION_REQUESTED", "SUBMITTED", "AICTE_REVIEWER", "Attach the advice.")] }, "CORRECTION_REQUESTED");
    expect(await screen.findByRole("form", { name: "Upload a document" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove advice.pdf" })).toBeInTheDocument();
  });

  it.each(["SUBMITTED", "ACCEPTED", "REJECTED", "WITHDRAWN"])("shows a %s record's documents read-only to the institution", async (status) => {
    const { api } = await openEdit({ documents: [makeDocument()] }, status);
    expect(await screen.findByText("advice.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download advice.pdf" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload a document" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(api.callsTo(UPLOAD)).toHaveLength(0);
  });

  it.each(["AICTE_REVIEWER", "AICTE_APPROVER"])("shows a %s the documents to review, with no way to change them", async (role) => {
    const backend = reviewBackend(submittedFdr(), { role, documents: [makeDocument(), makeDocument({ id: "d0000000-0000-4000-8000-000000000002", original_filename: "scan.jpg", content_type: "image/jpeg" })] });
    renderApp(`/review/${FDR_ID}`);
    await screen.findByText("Submitted details");
    expect(await screen.findByText("advice.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download scan.jpg" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview scan.jpg" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload a document" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(backend.api.callsTo(UPLOAD)).toHaveLength(0);
  });

  it("tells a reviewer when no documents were uploaded, and a failed load does not block the decision", async () => {
    reviewBackend(submittedFdr(), { role: "AICTE_APPROVER", extra: { [LIST]: textReply(500) } });
    renderApp(`/review/${FDR_ID}`);
    await screen.findByText("Submitted details");
    expect(await screen.findByText(/could not load the documents/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();
  });

  it("says so plainly when the institution attached nothing", async () => {
    reviewBackend(submittedFdr(), { role: "AICTE_REVIEWER" });
    renderApp(`/review/${FDR_ID}`);
    expect(await screen.findByText("The institution has not uploaded any documents.")).toBeInTheDocument();
  });
});

describe("creating a draft", () => {
  it("tells the user documents can be attached once the draft exists, and offers no upload yet", async () => {
    reviewBackend(null, { role: "INSTITUTION_USER" });
    renderApp("/fdrs/new");
    expect(await screen.findByText(/attach supporting documents once the draft is saved/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Document file")).not.toBeInTheDocument();
  });
});
