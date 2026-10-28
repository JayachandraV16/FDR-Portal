"""Turning a stored PDF/JPEG/PNG into text, with hard limits.

`TextExtractor` is the provider-independent interface: bytes in, `TextResult` out. `LocalTextExtractor`
reads embedded PDF text with pypdf (which never executes JavaScript, launches anything or fetches
resources) and falls back to an `OcrEngine` for pages that have no text layer. Replacing either
piece, or the whole class, changes nothing downstream.

Limits: page count (refused, not truncated), total text length (truncated and flagged), image pixels
(refused before decoding), and a wall-clock deadline checked between steps; the sandbox (sandbox.py)
adds a hard kill on top of the deadline.
"""

import io
import re
import time
from typing import Protocol

from app.extraction.images import image_dimensions
from app.extraction.ocr import NoOcr, OcrEngine, OcrError, TesseractEngine
from app.extraction.types import ErrorCode, Limits, PageText, TextResult, TextSource

# A page whose text layer has fewer visible characters than this is treated as having none.
MIN_PAGE_CHARS = 5
MAX_IMAGES_PER_PAGE = 3
_PDF = "application/pdf"
_IMAGE_SUFFIX = {"image/jpeg": ".jpg", "image/png": ".png"}


class TextExtractor(Protocol):
    def extract(self, data: bytes, content_type: str, limits: Limits) -> TextResult: ...


def _visible(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _page_image_streams(page) -> list:
    """The image XObjects directly on a page. Read through pypdf's object model, so no image library
    is needed; nothing is decoded here."""
    resources = page.get("/Resources")
    xobjects = resources.get_object().get("/XObject") if resources is not None else None
    if xobjects is None:
        return []
    found = []
    for reference in xobjects.get_object().values():
        obj = reference.get_object()
        if obj.get("/Subtype") == "/Image":
            found.append(obj)
    return found


class LocalTextExtractor:
    def __init__(self, ocr: OcrEngine | None = None):
        self.ocr: OcrEngine = ocr or NoOcr()

    def _engine_label(self, used_pdf: bool) -> str:
        parts = []
        if used_pdf:
            import pypdf

            parts.append(f"pypdf {pypdf.__version__}")
        if isinstance(self.ocr, TesseractEngine) and self.ocr.available():
            parts.append(f"tesseract {self.ocr.version()}")
        elif self.ocr.available():
            parts.append(self.ocr.name)
        return " + ".join(parts) or "none"

    def extract(self, data: bytes, content_type: str, limits: Limits) -> TextResult:
        deadline = time.monotonic() + limits.timeout_seconds
        if content_type == _PDF:
            result = self._pdf(data, limits, deadline)
        elif content_type in _IMAGE_SUFFIX:
            result = self._image(data, content_type, limits, deadline)
        else:
            return TextResult(error=ErrorCode.UNSUPPORTED_TYPE)
        result.engine = self._engine_label(content_type == _PDF)
        return result

    def _missing_languages(self) -> list[str]:
        """['missing_ocr_languages:mar'] when the OCR engine lacks a configured language, else []."""
        check = getattr(self.ocr, "missing_languages", None)
        missing = check() if callable(check) else []
        return [f"missing_ocr_languages:{','.join(missing)}"] if missing else []

    # ---- images ------------------------------------------------------------------------------
    def _image(self, data: bytes, content_type: str, limits: Limits, deadline: float) -> TextResult:
        size = image_dimensions(data)
        if size is None:
            return TextResult(error=ErrorCode.MALFORMED_DOCUMENT, pages_total=1)
        if size[0] <= 0 or size[1] <= 0 or size[0] * size[1] > limits.max_image_pixels:
            return TextResult(error=ErrorCode.IMAGE_TOO_LARGE, pages_total=1)
        if not self.ocr.available():
            return TextResult(error=ErrorCode.OCR_UNAVAILABLE, pages_total=1)
        missing = self._missing_languages()
        if missing:
            return TextResult(error=ErrorCode.OCR_LANGUAGE_UNAVAILABLE, pages_total=1, warnings=missing)
        try:
            text = self.ocr.recognize(data, _IMAGE_SUFFIX[content_type], deadline - time.monotonic())
        except OcrError as exc:
            return TextResult(error=ErrorCode.TIMEOUT if exc.reason == "timeout" else ErrorCode.OCR_FAILED, pages_total=1)
        return self._finish([PageText(1, TextSource.OCR, text)], limits, pages_total=1, warnings=[])

    # ---- PDFs --------------------------------------------------------------------------------
    def _pdf(self, data: bytes, limits: Limits, deadline: float) -> TextResult:
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                return TextResult(error=ErrorCode.ENCRYPTED_PDF)
            total = len(reader.pages)
        except Exception:
            return TextResult(error=ErrorCode.MALFORMED_DOCUMENT)
        if total == 0:
            return TextResult(error=ErrorCode.MALFORMED_DOCUMENT)
        if total > limits.max_pages:
            return TextResult(error=ErrorCode.TOO_MANY_PAGES, pages_total=total)

        pages: list[PageText] = []
        warnings: list[str] = []
        needs_ocr: list[int] = []
        for index in range(total):
            if time.monotonic() > deadline:
                return TextResult(error=ErrorCode.TIMEOUT, pages_total=total)
            try:
                text = reader.pages[index].extract_text() or ""
            except Exception:
                return TextResult(error=ErrorCode.MALFORMED_DOCUMENT, pages_total=total)
            if _visible(text) >= MIN_PAGE_CHARS:
                pages.append(PageText(index + 1, TextSource.EMBEDDED_TEXT, text))
            else:
                needs_ocr.append(index)

        ocr_failure: str | None = None
        if needs_ocr and self.ocr.available():
            missing = self._missing_languages()
            if missing:
                return TextResult(error=ErrorCode.OCR_LANGUAGE_UNAVAILABLE, pages_total=total, warnings=missing)
        for index in needs_ocr:
            if time.monotonic() > deadline:
                return TextResult(error=ErrorCode.TIMEOUT, pages_total=total)
            recognised, failure = self._ocr_pdf_page(reader, index, limits, deadline)
            if recognised is not None:
                pages.append(PageText(index + 1, TextSource.OCR, recognised))
            elif failure is not None:
                ocr_failure = ocr_failure or failure
        pages.sort(key=lambda p: p.page)

        covered = {p.page - 1 for p in pages}
        missing = [i + 1 for i in range(total) if i not in covered]
        if not pages:
            return TextResult(error=ocr_failure or ErrorCode.NO_TEXT_FOUND, pages_total=total)
        if missing:
            warnings.append("pages_without_text:" + ",".join(map(str, missing)))
        return self._finish(pages, limits, pages_total=total, warnings=warnings)

    def _ocr_pdf_page(self, reader, index: int, limits: Limits, deadline: float):
        """Recognise embedded JPEG images on a page that has no text layer (no image library is used,
        so other image encodings are not supported). Returns (text or None, failure code or None)."""
        if not self.ocr.available():
            return None, ErrorCode.OCR_UNAVAILABLE
        try:
            streams = _page_image_streams(reader.pages[index])
        except Exception:
            return None, ErrorCode.MALFORMED_DOCUMENT
        chunks: list[str] = []
        failure: str | None = None
        usable = 0
        for stream in streams:
            try:
                encoded = str(stream.get("/Filter"))
                width, height = int(stream.get("/Width", 0)), int(stream.get("/Height", 0))
            except Exception:
                continue
            if "DCTDecode" not in encoded or "," in encoded or "[" in encoded:
                failure = failure or ErrorCode.OCR_UNAVAILABLE  # an encoding that needs an image library
                continue
            if width <= 0 or height <= 0 or width * height > limits.max_image_pixels:
                failure = failure or ErrorCode.IMAGE_TOO_LARGE
                continue
            if usable >= MAX_IMAGES_PER_PAGE:
                break
            usable += 1
            try:
                raw = stream.get_data()  # a DCTDecode stream is the JPEG file itself
            except Exception:
                failure = failure or ErrorCode.MALFORMED_DOCUMENT
                continue
            if image_dimensions(raw) is None:
                failure = failure or ErrorCode.MALFORMED_DOCUMENT
                continue
            try:
                chunks.append(self.ocr.recognize(raw, ".jpg", deadline - time.monotonic()))
            except OcrError as exc:
                failure = ErrorCode.TIMEOUT if exc.reason == "timeout" else ErrorCode.OCR_FAILED
        text = "\n".join(chunks)
        if _visible(text) >= MIN_PAGE_CHARS:
            return text, None
        return None, failure

    # ---- shared ------------------------------------------------------------------------------
    @staticmethod
    def _finish(pages: list[PageText], limits: Limits, *, pages_total: int, warnings: list[str]) -> TextResult:
        remaining = limits.max_text_chars
        kept: list[PageText] = []
        truncated = False
        for position, page in enumerate(pages):
            text = page.text
            if len(text) > remaining:
                text, truncated = text[:remaining], True
            remaining -= len(text)
            kept.append(PageText(page.page, page.source, text))
            if remaining <= 0:
                truncated = truncated or position < len(pages) - 1
                break
        if not any(_visible(p.text) >= MIN_PAGE_CHARS for p in kept):
            return TextResult(error=ErrorCode.NO_TEXT_FOUND, pages_total=pages_total)
        return TextResult(pages=kept, warnings=warnings, truncated=truncated, pages_total=pages_total)
