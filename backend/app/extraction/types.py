"""Shared types. Plain dataclasses, JSON-serialisable, no application imports."""

import enum
from dataclasses import dataclass, field


class ErrorCode(enum.StrEnum):
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    MALFORMED_DOCUMENT = "MALFORMED_DOCUMENT"
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"  # the document needs OCR and no OCR engine is configured
    OCR_FAILED = "OCR_FAILED"
    OCR_LANGUAGE_UNAVAILABLE = "OCR_LANGUAGE_UNAVAILABLE"  # a configured OCR language is not installed
    NO_TEXT_FOUND = "NO_TEXT_FOUND"
    TIMEOUT = "TIMEOUT"
    WORKER_FAILED = "WORKER_FAILED"
    FILE_MISSING = "FILE_MISSING"
    INTEGRITY_FAILED = "INTEGRITY_FAILED"
    STORAGE_ERROR = "STORAGE_ERROR"
    DOCUMENT_UNAVAILABLE = "DOCUMENT_UNAVAILABLE"  # the document was removed before the job ran
    INTERRUPTED = "INTERRUPTED"  # the server stopped or restarted while the job was running


class TextSource(enum.StrEnum):
    EMBEDDED_TEXT = "EMBEDDED_TEXT"  # text stored in the PDF itself
    OCR = "OCR"  # recognised from pixels: may contain misread characters


@dataclass(frozen=True)
class Limits:
    max_pages: int = 10
    max_text_chars: int = 60_000
    timeout_seconds: float = 30.0
    max_image_pixels: int = 25_000_000


@dataclass
class PageText:
    page: int  # 1-based
    source: str  # TextSource value
    text: str


@dataclass
class TextResult:
    pages: list[PageText] = field(default_factory=list)
    error: str | None = None  # ErrorCode value; None means text was obtained
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    pages_total: int = 0
    engine: str = ""  # which components produced the text, e.g. "pypdf 6.21.2 + tesseract 5.5.0"
