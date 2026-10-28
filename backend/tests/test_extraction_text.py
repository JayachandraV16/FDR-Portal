"""Text extraction: embedded PDF text, scanned pages, images, limits, and the sandbox boundary."""

import io
import json
import os
import shutil
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from app.extraction.analysis import ERROR_MESSAGES, analyse
from app.extraction.ocr import TesseractEngine, discover_command
from app.extraction.sandbox import Busy, SandboxedExtractor, _SLOTS
from app.extraction.text import LocalTextExtractor
from app.extraction.types import ErrorCode, Limits
from tests.extraction_support import (
    STANDARD_LINES, FakeOcr, bitmap_text_png, make_image_pdf, make_pdf, make_receipt_pdf, png_with_size, tiny_jpeg,
)

PDF, PNG, JPEG = "application/pdf", "image/png", "image/jpeg"
LIMITS = Limits()


def run(data: bytes, content_type: str = PDF, ocr: FakeOcr | None = None, limits: Limits = LIMITS):
    return LocalTextExtractor(ocr).extract(data, content_type, limits)


# ---- embedded text ----------------------------------------------------------------------------
def test_embedded_pdf_text_is_extracted_without_any_ocr():
    ocr = FakeOcr(text="should never be used")
    result = run(make_receipt_pdf(), ocr=ocr)
    assert result.error is None and result.pages_total == 1
    assert [p.source for p in result.pages] == ["EMBEDDED_TEXT"] and ocr.calls == []
    assert "FDR No: 12345678901" in result.pages[0].text and result.engine.startswith("pypdf ")


def test_pages_keep_their_numbers():
    result = run(make_pdf([["Page one text here"], ["Page two text here"], ["Page three text here"]]))
    assert [(p.page, p.text.strip().splitlines()[0]) for p in result.pages] == [
        (1, "Page one text here"), (2, "Page two text here"), (3, "Page three text here")]


def test_analysis_turns_a_receipt_into_fields():
    analysis = analyse(make_receipt_pdf(), PDF, LocalTextExtractor(), LIMITS)
    assert analysis.status == "COMPLETED" and analysis.error_code is None
    by_name = {f["name"]: f for f in analysis.result["fields"]}
    assert by_name["fdr_number"]["value"] == "12345678901" and by_name["principal_amount"]["value"] == "1500000.00"
    assert analysis.result["pages_processed"] == 1 and analysis.result["ocr_pages"] == 0
    assert "12345678901 " not in json.dumps({k: v for k, v in analysis.result.items() if k != "fields"})  # only values, no raw text


def test_stored_results_never_contain_the_documents_text():
    sentinel = "SENTINEL-FREE-TEXT-LINE-9F3A"
    pdf = make_pdf([[*STANDARD_LINES, sentinel, "A long unrelated paragraph that is not a field."]])
    analysis = analyse(pdf, PDF, LocalTextExtractor(), LIMITS)
    dumped = json.dumps(analysis.result)
    assert sentinel not in dumped and "unrelated paragraph" not in dumped and "Fixed Deposit Receipt" not in dumped


# ---- scanned pages and images -----------------------------------------------------------------
def test_a_scanned_pdf_without_ocr_says_ocr_is_unavailable_and_extracts_nothing():
    result = run(make_image_pdf())
    assert result.error == ErrorCode.OCR_UNAVAILABLE and result.pages == []
    analysis = analyse(make_image_pdf(), PDF, LocalTextExtractor(), LIMITS)
    assert analysis.status == "FAILED" and analysis.error_code == "OCR_UNAVAILABLE"
    assert analysis.result["fields"] == []  # nothing fabricated
    assert "OCR" in ERROR_MESSAGES[ErrorCode.OCR_UNAVAILABLE]


def test_a_scanned_pdf_with_an_ocr_engine_is_read_and_marked_as_ocr():
    ocr = FakeOcr(text="FDR No: 998877\nMaturity Date: 15 Jan 2027")
    analysis = analyse(make_image_pdf(), PDF, LocalTextExtractor(ocr), LIMITS)
    assert analysis.status == "COMPLETED" and ocr.calls == [".jpg"] and analysis.result["ocr_pages"] == 1
    f = {x["name"]: x for x in analysis.result["fields"]}
    assert (f["fdr_number"]["value"], f["fdr_number"]["confidence"], f["fdr_number"]["source"]) == ("998877", "MEDIUM", "OCR")
    assert f["branch_name"]["status"] == "NOT_FOUND"


def test_a_pdf_with_both_text_and_scanned_pages_reports_the_unread_ones():
    both = make_pdf([["FDR No: 12345678901", "Depositor: PICT Test Institute"]])
    result = run(both, ocr=FakeOcr())
    assert result.error is None and result.warnings == []
    result = run(make_pdf([["ab"], ["Another page with enough text"]]), ocr=FakeOcr())
    assert result.error is None and result.warnings == ["pages_without_text:1"]


def test_ocr_that_fails_or_times_out_is_reported_not_hidden():
    assert run(make_image_pdf(), ocr=FakeOcr(fail="failed")).error == ErrorCode.OCR_FAILED
    assert run(make_image_pdf(), ocr=FakeOcr(fail="timeout")).error == ErrorCode.TIMEOUT
    assert run(make_image_pdf(), ocr=FakeOcr(text="  ")).error in (ErrorCode.NO_TEXT_FOUND, ErrorCode.OCR_FAILED, ErrorCode.OCR_UNAVAILABLE)


def test_a_scanned_page_using_an_encoding_that_needs_an_image_library_is_not_pretended_to_be_read():
    flate = make_image_pdf().replace(b"/DCTDecode", b"/FlateDecode")
    result = run(flate, ocr=FakeOcr(text="FDR No: 12345678901"))
    assert result.error in (ErrorCode.OCR_UNAVAILABLE, ErrorCode.MALFORMED_DOCUMENT) and result.pages == []


@pytest.mark.parametrize("content_type", [PNG, JPEG])
def test_images_are_read_by_ocr_only(content_type):
    data = bitmap_text_png("FDR NO 4821937") if content_type == PNG else tiny_jpeg()
    assert run(data, content_type).error == ErrorCode.OCR_UNAVAILABLE
    ocr = FakeOcr(text="FDR No: 4821937")
    result = run(data, content_type, ocr)
    assert result.error is None and result.pages[0].source == "OCR" and ocr.calls == [".png" if content_type == PNG else ".jpg"]


def test_a_huge_image_is_refused_before_ocr_sees_it():
    ocr = FakeOcr(text="x" * 20)
    result = run(png_with_size(20000, 20000), PNG, ocr)
    assert result.error == ErrorCode.IMAGE_TOO_LARGE and ocr.calls == []
    assert run(png_with_size(5000, 5000), PNG, FakeOcr(text="FDR No: 1234"), Limits(max_image_pixels=1_000_000)).error == ErrorCode.IMAGE_TOO_LARGE


def test_an_unreadable_image_header_is_malformed():
    assert run(b"\x89PNG\r\n\x1a\n" + b"garbage", PNG, FakeOcr(text="abcdefgh")).error == ErrorCode.MALFORMED_DOCUMENT
    assert run(b"\xff\xd8\xff", JPEG, FakeOcr(text="abcdefgh")).error == ErrorCode.MALFORMED_DOCUMENT


# ---- malformed and oversized input -----------------------------------------------------------
@pytest.mark.parametrize(
    "data",
    [b"%PDF-1.4\nthis is not a pdf at all", b"%PDF-1.7\n%%EOF", make_receipt_pdf()[:300], b"%PDF-1.4\n" + os.urandom(2048)],
)
def test_malformed_pdfs_fail_cleanly(data):
    result = run(data)
    assert result.error in (ErrorCode.MALFORMED_DOCUMENT, ErrorCode.NO_TEXT_FOUND) and result.pages == []


def test_an_encrypted_pdf_is_refused():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(make_receipt_pdf())).pages:
        writer.add_page(page)
    try:
        writer.encrypt("owner-pass", algorithm="RC4-128")
    except Exception:
        pytest.skip("this pypdf build cannot encrypt without an extra package")
    out = io.BytesIO()
    writer.write(out)
    assert run(out.getvalue()).error == ErrorCode.ENCRYPTED_PDF


def test_too_many_pages_are_refused_not_truncated():
    pdf = make_pdf([[f"Page {i} has some text"] for i in range(11)])
    result = run(pdf, limits=Limits(max_pages=10))
    assert result.error == ErrorCode.TOO_MANY_PAGES and result.pages_total == 11 and result.pages == []
    assert run(pdf, limits=Limits(max_pages=11)).error is None


def test_long_text_is_truncated_and_flagged():
    pdf = make_pdf([["FDR No: 12345678901"] + [f"Line {i} of filler text for length" for i in range(40)]])
    result = run(pdf, limits=Limits(max_text_chars=200))
    assert result.error is None and result.truncated is True
    assert sum(len(p.text) for p in result.pages) <= 200 and "FDR No: 12345678901" in result.pages[0].text
    assert run(pdf).truncated is False


def test_an_expired_time_budget_stops_the_work():
    assert run(make_receipt_pdf(), limits=Limits(timeout_seconds=-1)).error == ErrorCode.TIMEOUT


def test_unsupported_types_are_refused():
    for content_type in ("text/html", "application/zip", "image/svg+xml", ""):
        assert run(b"<html></html>", content_type).error == ErrorCode.UNSUPPORTED_TYPE


def test_a_pdf_that_embeds_javascript_or_a_launch_action_is_only_read_never_run():
    pdf = make_receipt_pdf().replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert\\(1\\)) >>")
    result = run(pdf)
    assert result.error is None and "FDR No: 12345678901" in result.pages[0].text


# ---- the OCR adapter --------------------------------------------------------------------------
def test_discovery_uses_only_an_explicit_path_or_the_path_variable(tmp_path, monkeypatch):
    assert discover_command(str(tmp_path / "missing.exe")) is None
    present = tmp_path / "tesseract-stand-in"
    present.write_text("x")
    assert discover_command(str(present)) == [str(present)]
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert discover_command(None) is None
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    assert discover_command(None) == ["/usr/bin/tesseract"]


def test_the_ocr_language_cannot_carry_extra_arguments():
    for bad in ("eng --tessdata-dir /x", "../eng", "eng;ls", ""):
        with pytest.raises(ValueError):
            TesseractEngine(["tesseract"], bad)
    TesseractEngine(["tesseract"], "eng+hin")


# A stand-in for `tesseract --list-langs`, so a fake engine looks like a real one with English installed.
LIST_LANGS_AWARE = (
    "import sys\nif '--list-langs' in sys.argv:\n    print('List of available languages (1):')\n    print('eng')\n    sys.exit(0)\n"
)


def _ocr_script(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "fake_ocr.py"
    script.write_text(body)
    return [sys.executable, str(script)]


def test_the_adapter_runs_the_command_on_a_temp_file_and_reads_stdout(tmp_path):
    command = _ocr_script(tmp_path, "import sys\nprint('FDR No: 4821937 from ' + sys.argv[1].split('.')[-1] + ' ' + sys.argv[2] + ' ' + sys.argv[4])\n")
    engine = TesseractEngine(command, "eng")
    out = engine.recognize(tiny_jpeg(), ".jpg", 20)
    assert "FDR No: 4821937 from jpg stdout eng" in out
    from app.extraction.ocr import OcrError

    failing = TesseractEngine(_ocr_script(tmp_path, "import sys\nsys.exit(3)\n"), "eng")
    with pytest.raises(OcrError) as exc:
        failing.recognize(tiny_jpeg(), ".jpg", 20)
    assert exc.value.reason == "failed"
    slow = TesseractEngine(_ocr_script(tmp_path, "import time\ntime.sleep(30)\n"), "eng")
    with pytest.raises(OcrError) as exc:
        slow.recognize(tiny_jpeg(), ".jpg", 0.5)
    assert exc.value.reason == "timeout"


# ---- the sandbox ------------------------------------------------------------------------------
def test_the_sandbox_gives_the_same_answer_as_the_in_process_extractor():
    data = make_receipt_pdf()
    inside = LocalTextExtractor().extract(data, PDF, LIMITS)
    outside = SandboxedExtractor().extract(data, PDF, LIMITS)
    assert outside.error is None and [(p.page, p.source, p.text) for p in outside.pages] == [(p.page, p.source, p.text) for p in inside.pages]
    assert outside.engine == inside.engine and outside.pages_total == 1


def test_the_sandbox_reports_failures_the_same_way():
    sandbox = SandboxedExtractor()
    assert sandbox.extract(make_image_pdf(), PDF, LIMITS).error == ErrorCode.OCR_UNAVAILABLE
    assert sandbox.extract(b"%PDF-1.4\nnot a pdf", PDF, LIMITS).error == ErrorCode.MALFORMED_DOCUMENT
    assert sandbox.extract(b"x", "text/html", LIMITS).error == ErrorCode.UNSUPPORTED_TYPE
    assert sandbox.extract(make_pdf([["Some text here"]] * 3), PDF, Limits(max_pages=2)).error == ErrorCode.TOO_MANY_PAGES


def test_the_sandbox_can_use_an_ocr_command(tmp_path):
    command = _ocr_script(tmp_path, LIST_LANGS_AWARE + "print('FDR No: 998877 read by the stand-in')\n")
    result = SandboxedExtractor(command).extract(make_image_pdf(), PDF, LIMITS)
    assert result.error is None and result.pages[0].source == "OCR" and "998877" in result.pages[0].text


def test_the_child_process_receives_no_application_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2@db/x")
    monkeypatch.setenv("SECRET_KEY", "super-secret-key-value")
    seen = {}
    real = subprocess.run

    def spy(command, **kwargs):
        seen["env"], seen["command"] = kwargs["env"], command
        return real(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    assert SandboxedExtractor().extract(make_receipt_pdf(), PDF, LIMITS).error is None
    assert "DATABASE_URL" not in seen["env"] and "SECRET_KEY" not in seen["env"]
    assert not any("hunter2" in str(v) or "super-secret" in str(v) for v in seen["env"].values())
    assert seen["command"][1:4] == ["-E", "-s", "-m"]  # isolated interpreter mode; no shell


def test_a_child_that_overruns_is_killed_and_reported_as_a_timeout(monkeypatch):
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    assert SandboxedExtractor().extract(make_receipt_pdf(), PDF, LIMITS).error == ErrorCode.TIMEOUT


@pytest.mark.parametrize("stdout", [b"", b"not json", b'{"pages": 1}', "oversized"])
def test_a_child_that_returns_garbage_is_a_worker_failure(monkeypatch, stdout):
    if stdout == "oversized":
        stdout = b"x" * (9 * 1024 * 1024)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, b""))
    assert SandboxedExtractor().extract(make_receipt_pdf(), PDF, LIMITS).error == ErrorCode.WORKER_FAILED


def test_a_child_that_crashes_is_a_worker_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"Traceback with document text"))
    result = SandboxedExtractor().extract(make_receipt_pdf(), PDF, LIMITS)
    assert result.error == ErrorCode.WORKER_FAILED and "Traceback" not in repr(result)


def test_when_every_slot_is_busy_the_request_is_refused_not_queued():
    held = []
    try:
        while _SLOTS.acquire(blocking=False):
            held.append(1)
        with pytest.raises(Busy):
            SandboxedExtractor().extract(make_receipt_pdf(), PDF, LIMITS)
    finally:
        for _ in held:
            _SLOTS.release()
    assert SandboxedExtractor().extract(make_receipt_pdf(), PDF, LIMITS).error is None


# ---- a real OCR engine, when one is installed (skipped otherwise) ------------------------------
def _real_tesseract() -> list[str] | None:
    candidates = [os.environ.get("OCR_COMMAND"), shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe"]
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return [candidate]
    return None


@pytest.mark.skipif(_real_tesseract() is None, reason="Tesseract is not installed")
def test_real_tesseract_reads_a_synthetic_image():
    engine = TesseractEngine(_real_tesseract(), "eng")
    assert engine.available() and engine.version() != "unknown"
    image = bitmap_text_png("FDR 4821937", scale=4)
    # A crude synthetic font: the engine is expected to read most characters, not all. That is the
    # reason OCR-derived fields are never reported above MEDIUM confidence.
    digits = "".join(ch for ch in engine.recognize(image, ".png", 60) if ch.isdigit())
    assert SequenceMatcher(None, digits, "4821937").ratio() >= 0.7, digits
    result = SandboxedExtractor(_real_tesseract()).extract(image, PNG, Limits(timeout_seconds=60))
    assert result.error is None and result.pages[0].source == "OCR" and "tesseract" in result.engine
