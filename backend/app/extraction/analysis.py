"""From document bytes to the record that is stored: text extraction, then field extraction.

No database or web imports here, so it can be exercised directly in tests with any TextExtractor.
"""

import dataclasses
from dataclasses import dataclass

from app.extraction.fields import ExtractedField, extract_fields
from app.extraction.text import TextExtractor
from app.extraction.types import ErrorCode, Limits, TextSource

# What a person is told for each failure. Fixed wording, never text from the document.
ERROR_MESSAGES: dict[str, str] = {
    ErrorCode.UNSUPPORTED_TYPE: "This type of file cannot be analysed.",
    ErrorCode.MALFORMED_DOCUMENT: "The file could not be read. It may be damaged or not a valid document.",
    ErrorCode.ENCRYPTED_PDF: "The PDF is password-protected, so its text cannot be read.",
    ErrorCode.TOO_MANY_PAGES: "The PDF has more pages than can be analysed.",
    ErrorCode.IMAGE_TOO_LARGE: "The image is too large to analyse.",
    ErrorCode.OCR_UNAVAILABLE: (
        "This document has no readable text layer and text recognition (OCR) is not available on the server, "
        "so nothing was extracted. Review the document by eye."
    ),
    ErrorCode.OCR_FAILED: "Text recognition (OCR) failed on this document. Review it by eye.",
    ErrorCode.OCR_LANGUAGE_UNAVAILABLE: (
        "Text recognition (OCR) is installed, but language data it was configured to use is not installed, so "
        "this document was not read. Install the language data or change OCR_LANGUAGE. Review it by eye."
    ),
    ErrorCode.NO_TEXT_FOUND: "No readable text was found in the document. Review it by eye.",
    ErrorCode.TIMEOUT: "Analysis took too long and was stopped. You can try again.",
    ErrorCode.WORKER_FAILED: "The analysis could not be completed. You can try again.",
    ErrorCode.FILE_MISSING: "The stored file for this document is unavailable.",
    ErrorCode.INTEGRITY_FAILED: "The stored file no longer matches what was uploaded, so it was not analysed.",
    ErrorCode.STORAGE_ERROR: "The stored file could not be read. You can try again.",
    ErrorCode.DOCUMENT_UNAVAILABLE: "The document was removed before it could be analysed.",
    ErrorCode.INTERRUPTED: "The analysis was interrupted because the server stopped or restarted. You can try again.",
}


@dataclass
class Analysis:
    status: str  # COMPLETED | FAILED
    error_code: str | None
    extractor: str
    result: dict  # JSON-ready: fields, warnings, truncated, pages_total, pages_processed, ocr_pages


def failed(code: str, *, extractor: str = "none", pages_total: int = 0, warnings: list[str] | None = None) -> Analysis:
    return Analysis("FAILED", str(code), extractor, {
        "fields": [], "warnings": list(warnings or []), "truncated": False, "pages_total": pages_total, "pages_processed": 0, "ocr_pages": 0,
    })


def analyse(data: bytes, content_type: str, extractor: TextExtractor, limits: Limits) -> Analysis:
    text = extractor.extract(data, content_type, limits)
    label = text.engine or "none"
    if text.error is not None:
        return failed(text.error, extractor=label, pages_total=text.pages_total, warnings=text.warnings)
    fields = extract_fields(text.pages)
    return Analysis("COMPLETED", None, label, {
        "fields": [dataclasses.asdict(f) for f in fields],
        "warnings": list(text.warnings),
        "truncated": text.truncated,
        "pages_total": text.pages_total,
        "pages_processed": len(text.pages),
        "ocr_pages": sum(1 for p in text.pages if p.source == TextSource.OCR),
    })


def fields_from_result(result: dict) -> list[ExtractedField]:
    return [ExtractedField(**f) for f in result.get("fields", [])]
