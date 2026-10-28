"""Validation of an uploaded document, independent of anything the browser claims.

An upload is accepted only if ALL of these agree: the file's leading bytes are a PDF, JPEG or PNG
signature; the sanitised filename ends in an extension that belongs to that type; and the declared
Content-Type (if the client sent a specific one) names the same type. The stored content type is the
one found in the bytes. This is a format check, not a safety or authenticity guarantee: it does not
scan for malware and does not inspect a PDF's internals. Files are therefore only ever served as
downloads with `nosniff` and a locked-down CSP (see app.api.documents).
"""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

SIGNATURE_BYTES = 16  # how much of the file start is needed to identify the type

EXTENSIONS: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
}
_TYPE_BY_EXTENSION = {ext: ctype for ctype, exts in EXTENSIONS.items() for ext in exts}
# A browser may send nothing useful for a File; these mean "no claim".
_NO_CLAIM = {"", "application/octet-stream", "binary/octet-stream"}

MAX_FILENAME_LENGTH = 150
MAX_RAW_FILENAME_LENGTH = 600
UNSUPPORTED = "Only PDF, JPEG and PNG files are accepted"


class UploadRejected(Exception):
    def __init__(self, status_code: int, message: str, reason: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.reason = reason  # machine-readable, safe to audit


@dataclass(frozen=True)
class ValidatedUpload:
    display_name: str
    content_type: str


def sniff_content_type(head: bytes) -> str | None:
    """Identify PDF/JPEG/PNG from the first bytes only. Anything else (HTML, SVG, ZIP, executables,
    scripts) returns None."""
    if head[:5] == b"%PDF-" and head[5:6].isdigit():
        return "application/pdf"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return "image/png"
    return None


def decode_filename_header(raw: str | None) -> str | None:
    """The client sends the name percent-encoded in a header (HTTP headers are not Unicode-safe)."""
    if raw is None:
        return None
    if len(raw) > MAX_RAW_FILENAME_LENGTH:
        raise UploadRejected(422, "File name is too long", "filename_too_long")
    return unquote(raw, errors="replace")


def sanitize_filename(raw: str) -> str:
    """A name safe to show and to put in a header: no directories, no control or formatting
    characters (including bidi overrides), no characters that are special to common shells or file
    systems, bounded length. It is a display label only: no path is ever built from it."""
    name = unicodedata.normalize("NFC", raw)
    name = re.split(r"[\\/]", name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r'[<>:"|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    ext = ("." + ext) if ext else ""
    if len(stem) + len(ext) > MAX_FILENAME_LENGTH:
        stem = stem[: max(1, MAX_FILENAME_LENGTH - len(ext))]
    return (stem or "document") + ext


def validate_upload(filename_header: str | None, declared_content_type: str | None, head: bytes) -> ValidatedUpload:
    raw_name = decode_filename_header(filename_header)
    if raw_name is None or not raw_name.strip():
        raise UploadRejected(422, "A file name is required", "filename_missing")
    display_name = sanitize_filename(raw_name)

    extension = "." + display_name.rsplit(".", 1)[-1].lower() if "." in display_name else ""
    by_extension = _TYPE_BY_EXTENSION.get(extension)
    if by_extension is None:
        raise UploadRejected(415, UNSUPPORTED, "extension_not_allowed")

    actual = sniff_content_type(head)
    if actual is None:
        raise UploadRejected(415, UNSUPPORTED, "signature_not_allowed")
    if actual != by_extension:
        raise UploadRejected(415, "The file's content does not match its extension", "extension_mismatch")

    declared = (declared_content_type or "").split(";")[0].strip().lower()
    if declared not in _NO_CLAIM and declared != actual:
        raise UploadRejected(415, "The file's content does not match its declared type", "content_type_mismatch")

    return ValidatedUpload(display_name=display_name, content_type=actual)
