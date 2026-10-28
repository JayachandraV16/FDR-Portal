import { useEffect, useRef, useState, type FormEvent } from "react";

import { RetryButton } from "../dashboard/Panel";
import DocumentCheck from "./DocumentCheck";
import {
  ApiError,
  deleteDocument,
  downloadDocument,
  listDocuments,
  uploadDocument,
  type DocumentCategory,
  type DocumentOut,
} from "../lib/api";
import { DOCUMENT_CATEGORY_LABELS, documentCategoryLabel, formatBytes, formatDateTime } from "../lib/format";
import { saveBlob } from "../lib/download";
import { describeError, useAsync } from "../lib/useAsync";

/** Mirrors the server's limits so the common mistakes are caught before a large upload. The server
 * re-checks everything and is the only authority. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png"];
const PREVIEWABLE = new Set(["image/jpeg", "image/png"]);
const ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

/** Why a chosen file cannot be uploaded, or null if it looks fine. */
export function problemWithFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext))) return "Only PDF, JPEG and PNG files can be uploaded.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) return `This file is ${formatBytes(file.size)}. The limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`;
  return null;
}

/** User-facing text for a failed document request. The backend's own wording is used for refusals it
 * explains (type, size, status, limit); transport and server faults get generic text. */
export function describeDocumentFailure(err: unknown): string {
  if (!(err instanceof ApiError)) return "The request failed unexpectedly. Please try again.";
  switch (err.kind) {
    case "network":
      return "Cannot reach the server. Check your connection and try again.";
    case "server":
      return `The server reported an error (HTTP ${err.status}). Please try again shortly.`;
    case "forbidden":
      return "You are not permitted to do this, or the request was blocked by a security check.";
    default:
      return err.status === 413 ? err.message : err.message || `Request failed (HTTP ${err.status})`;
  }
}


interface Props {
  fdrId: string;
  /** "manage" lets an institution user upload and remove; "view" is read-only (reviewers, or a record
   * the institution can no longer change). The server enforces the same rules regardless. */
  mode: "manage" | "view";
  /** Offer each document's automatic check (extraction and comparison). Reviewers and approvers only;
   * the server enforces that regardless of this flag. */
  checks?: boolean;
}

/** Documents attached to an FDR. Uploaded documents are what the institution provided: they are not
 * verified by AICTE or the bank, and the page never says or implies that they are. */
export default function DocumentsPanel({ fdrId, mode, checks = false }: Props) {
  const { state, reload } = useAsync((signal) => listDocuments(fdrId, signal), fdrId);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ id: string; name: string; url: string } | null>(null);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview.url);
  }, [preview]);

  async function download(doc: DocumentOut) {
    setBusyId(doc.id);
    setActionError(null);
    setNotice(null);
    try {
      saveBlob(await downloadDocument(fdrId, doc.id), doc.original_filename);
    } catch (err) {
      setActionError(describeDocumentFailure(err));
    } finally {
      setBusyId(null);
    }
  }

  async function showPreview(doc: DocumentOut) {
    setBusyId(doc.id);
    setActionError(null);
    setNotice(null);
    try {
      const blob = await downloadDocument(fdrId, doc.id);
      // Only a type this page knows how to show safely, taken from the server's verified record.
      if (!PREVIEWABLE.has(doc.content_type)) throw new Error("not previewable");
      setPreview({ id: doc.id, name: doc.original_filename, url: URL.createObjectURL(new Blob([blob], { type: doc.content_type })) });
    } catch (err) {
      setActionError(describeDocumentFailure(err));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(doc: DocumentOut) {
    setBusyId(doc.id);
    setActionError(null);
    try {
      await deleteDocument(fdrId, doc.id);
      setConfirmingId(null);
      if (preview?.id === doc.id) setPreview(null);
      setNotice(`${doc.original_filename} was removed.`);
      reload();
    } catch (err) {
      setActionError(describeDocumentFailure(err));
      setConfirmingId(null);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section aria-labelledby="documents-heading" className="rounded border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <h2 id="documents-heading" className="text-sm font-semibold text-slate-900">
          Documents
        </h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Files provided by the institution. They have not been verified by AICTE or by the bank.
        </p>
      </div>
      <div className="space-y-4 p-4">
        {notice && (
          <div role="status" className="rounded border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
            {notice}
          </div>
        )}
        {actionError && (
          <div role="alert" className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {actionError}
          </div>
        )}

        {state.status === "loading" && (
          <p role="status" className="text-sm text-slate-500">
            Loading documents…
          </p>
        )}
        {state.status === "error" && (
          <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-red-800">Could not load the documents. {describeError(state.error)}.</p>
            <RetryButton onClick={reload} />
          </div>
        )}
        {state.status === "success" && state.data.items.length === 0 && (
          <p className="text-sm text-slate-600">
            {mode === "manage" ? "No documents have been uploaded yet." : "The institution has not uploaded any documents."}
          </p>
        )}
        {state.status === "success" && state.data.items.length > 0 && (
          <ul className="divide-y divide-slate-200 rounded border border-slate-200">
            {state.data.items.map((doc) => (
              <li key={doc.id} className="space-y-2 p-3 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-words font-medium text-slate-900">{doc.original_filename}</p>
                    <p className="text-xs text-slate-600">
                      {documentCategoryLabel(doc.category)} · {formatBytes(doc.size_bytes)}
                    </p>
                    <p className="text-xs text-slate-500">
                      Uploaded by {doc.uploaded_by_name} · {formatDateTime(doc.uploaded_at)}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      aria-label={`Download ${doc.original_filename}`}
                      onClick={() => download(doc)}
                      disabled={busyId !== null}
                      className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-50"
                    >
                      Download
                    </button>
                    {PREVIEWABLE.has(doc.content_type) && (
                      <button
                        type="button"
                        aria-label={`Preview ${doc.original_filename}`}
                        onClick={() => showPreview(doc)}
                        disabled={busyId !== null}
                        className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-50"
                      >
                        Preview
                      </button>
                    )}
                    {mode === "manage" && (
                      <button
                        type="button"
                        aria-label={`Remove ${doc.original_filename}`}
                        onClick={() => setConfirmingId(doc.id)}
                        disabled={busyId !== null || confirmingId === doc.id}
                        className="rounded border border-red-300 px-3 py-1.5 text-xs font-medium text-red-800 hover:bg-red-50 disabled:opacity-50"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </div>
                {checks && <DocumentCheck fdrId={fdrId} documentId={doc.id} filename={doc.original_filename} />}
                {confirmingId === doc.id && (
                  <div role="alertdialog" aria-label={`Confirm removal of ${doc.original_filename}`} className="rounded border border-red-200 bg-red-50 p-3">
                    <p className="text-sm text-red-900">Remove this document from the FDR?</p>
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        onClick={() => remove(doc)}
                        disabled={busyId !== null}
                        className="rounded bg-red-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-900 disabled:opacity-50"
                      >
                        {busyId === doc.id ? "Removing…" : "Confirm removal"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingId(null)}
                        disabled={busyId !== null}
                        className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-white disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {preview && (
          <figure aria-label={`Preview of ${preview.name}`} className="rounded border border-slate-200 p-3">
            <img src={preview.url} alt={`Preview of ${preview.name}`} className="mx-auto max-h-96 max-w-full" />
            <figcaption className="mt-2 flex items-center justify-between gap-3 text-xs text-slate-600">
              <span className="break-words">{preview.name}</span>
              <button type="button" onClick={() => setPreview(null)} className="rounded border border-slate-300 px-3 py-1 font-medium text-slate-800 hover:bg-slate-100">
                Close preview
              </button>
            </figcaption>
          </figure>
        )}

        {mode === "manage" && (
          <UploadForm
            fdrId={fdrId}
            onUploaded={(doc) => {
              setActionError(null);
              setNotice(`${doc.original_filename} was uploaded.`);
              reload();
            }}
          />
        )}
      </div>
    </section>
  );
}

function UploadForm({ fdrId, onUploaded }: { fdrId: string; onUploaded: (doc: DocumentOut) => void }) {
  const [category, setCategory] = useState<DocumentCategory>("FDR_CERTIFICATE");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const inFlight = useRef(false); // closes the gap between a click and the re-render that disables the button
  const input = useRef<HTMLInputElement>(null);

  function choose(chosen: File | null) {
    setFile(chosen);
    setError(null);
    setProblem(chosen ? problemWithFile(chosen) : null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || problem || inFlight.current) return;
    inFlight.current = true;
    setUploading(true);
    setError(null);
    try {
      const doc = await uploadDocument(fdrId, file, category);
      setFile(null);
      if (input.current) input.current.value = "";
      onUploaded(doc);
    } catch (err) {
      setError(describeDocumentFailure(err));
    } finally {
      inFlight.current = false;
      setUploading(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded border border-slate-200 bg-slate-50 p-3" aria-label="Upload a document" noValidate>
      <p className="text-sm font-medium text-slate-900">Add a document</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="document-category" className="block text-xs font-medium text-slate-700">
            Document type
          </label>
          <select
            id="document-category"
            value={category}
            onChange={(e) => setCategory(e.target.value as DocumentCategory)}
            disabled={uploading}
            className="mt-1 block w-full rounded border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            {Object.entries(DOCUMENT_CATEGORY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="document-file" className="block text-xs font-medium text-slate-700">
            Document file
          </label>
          <input
            id="document-file"
            ref={input}
            type="file"
            accept={ACCEPT}
            disabled={uploading}
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
            className="mt-1 block w-full text-sm"
          />
        </div>
      </div>
      <p className="text-xs text-slate-600">PDF, JPEG or PNG, up to {formatBytes(MAX_UPLOAD_BYTES)}.</p>
      {file && (
        <p className="text-sm text-slate-800" data-testid="selected-file">
          Selected: <span className="font-medium">{file.name}</span> ({formatBytes(file.size)})
        </p>
      )}
      {problem && (
        <p role="alert" className="text-sm text-red-800">
          {problem}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-800">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={!file || problem !== null || uploading}
        className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-500"
      >
        {uploading ? "Uploading…" : "Upload document"}
      </button>
    </form>
  );
}
