"""Extraction and comparison endpoints.

Extraction is assistance for a reviewer: these tests also pin down everything it must NOT do (change
an FDR, move a status, appear in the audit trail as text, reach roles that may not review).

Runs on SQLite and, when TEST_DATABASE_URL is set, on the dedicated PostgreSQL test database. Files
go to a pytest temporary directory; the extractor is replaced by an in-process one with a fake OCR
engine, except for one test that goes through the real sandbox. All documents are synthetic.
"""

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.extraction.jobs import ExtractionRunner, RunnerConfig
from app.extraction.sandbox import SandboxedExtractor
from app.extraction.text import LocalTextExtractor
from app.models import FDR, FDRDocumentExtraction, FDRRevision, FDRStatusHistory
from tests.api_support import API, audit_events, audit_rows, build_client, make_settings, sign_in
from tests.extraction_support import FakeOcr, make_image_pdf, make_pdf
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_documents_api import (  # noqa: F401  (fixtures are used by name)
    _private_store,
    delete,
    docs,
    draft_with_owner,
    store,
    stored_files,
    up,
)
from tests.test_fdr_review_api import decide
from tests.test_fdr_write_api import create, force_status, institution_user, row, world  # noqa: F401

pytestmark = sqlite_decimal_warning

SENTINEL = "SENTINEL-FREE-TEXT-LINE-7C21"


def receipt_lines(**over) -> list[str]:
    values = {
        "bank": "Write Bank 1 (Synthetic)", "branch": "Main Branch (Synthetic)", "number": "WR-FDR-001",
        "depositor": "Test Depositor (Synthetic)", "amount": "Rs. 15,00,000.00", "issue": "15 Jan 2026",
        "maturity": "15 Jan 2027", "rate": "7.10 % p.a.",
    }
    values.update(over)
    labels = [("Bank Name", "bank"), ("Branch", "branch"), ("FDR No", "number"), ("Depositor", "depositor"),
              ("Principal Amount", "amount"), ("Date of Deposit", "issue"), ("Maturity Date", "maturity"),
              ("Rate of Interest", "rate")]
    return [f"{label}: {values[key]}" for label, key in labels if values[key] is not None] + [SENTINEL]


def receipt_pdf(**over) -> bytes:
    return make_pdf([receipt_lines(**over)])


@pytest.fixture
def ocr():
    return FakeOcr(is_available=False)


RUNNER: ExtractionRunner | None = None


@pytest.fixture(autouse=True)
def _runner(engine, store, ocr):
    """The queue is drained explicitly by the tests (no background threads run under test), with an
    in-process extractor and a fake OCR engine unless a test says otherwise."""
    global RUNNER
    RUNNER = ExtractionRunner(lambda: Session(engine), store, LocalTextExtractor(ocr), RunnerConfig(poll_seconds=0.05))
    yield RUNNER
    RUNNER = None


def reviewer(engine, role="AICTE_REVIEWER"):
    return sign_in(engine, role=role)


def submitted_with(engine, world, pdf: bytes, name="receipt.pdf", **fdr_overrides):
    """A really submitted FDR with one uploaded document, built through the API."""
    owner = institution_user(engine, world, "A")
    fdr = create(owner, world, **fdr_overrides)
    document = up(owner, fdr["id"], pdf, name)
    assert document.status_code == 201, document.text
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code == 200
    return owner, fdr, document.json()


def path(fdr, doc, tail):
    return f"/fdrs/{fdr['id']}/documents/{doc['id']}/{tail}"


def run_extraction(who, fdr, doc):
    """Ask for a check and let a worker finish it, like the real system does in the background.
    Returns the response from reading the result, or the POST response if the request was refused."""
    r = who.post(path(fdr, doc, "extractions"))
    if r.status_code not in (200, 202):
        return r
    RUNNER.run_pending()
    return who.get(path(fdr, doc, "extraction"))


def outcomes(response, side="current"):
    return {f["field"]: f[side]["outcome"] for f in response.json()["fields"]}


def extraction_rows(engine):
    with Session(engine) as s:
        return list(s.scalars(select(FDRDocumentExtraction).order_by(FDRDocumentExtraction.requested_at, FDRDocumentExtraction.id)))


# ---- running and reading ----------------------------------------------------------------------
def test_a_reviewer_runs_extraction_and_every_field_matches_a_consistent_document(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    r = run_extraction(rev, fdr, doc)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    body = r.json()
    assert body["status"] == "COMPLETED" and body["error_code"] is None and body["error_message"] is None
    assert body["pages_total"] == 1 and body["pages_processed"] == 1 and body["ocr_pages"] == 0 and body["truncated"] is False
    assert body["extractor"].startswith("pypdf ")
    found = {f["name"]: f for f in body["fields"]}
    assert found["fdr_number"]["value"] == "WR-FDR-001" and found["fdr_number"]["confidence"] == "HIGH"
    assert found["principal_amount"]["value"] == "1500000.00" and found["currency"]["value"] == "INR"
    assert found["maturity_date"]["page"] == 1 and found["maturity_date"]["source"] == "EMBEDDED_TEXT"

    c = rev.get(path(fdr, doc, "comparison"))
    assert c.status_code == 200 and c.headers["cache-control"] == "no-store"
    assert set(outcomes(c).values()) == {"MATCH"}, outcomes(c)
    assert set(outcomes(c, "revision").values()) == {"MATCH"} and c.json()["revision_no"] == 1
    assert set(outcomes(c)) == {"bank_name", "branch_name", "fdr_number", "depositor_name", "principal_amount",
                                "currency", "issue_date", "maturity_date", "interest_rate_pct"}


def test_the_latest_run_is_returned_by_the_extraction_endpoint(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    assert rev.get(path(fdr, doc, "extraction")).status_code == 404
    first = run_extraction(rev, fdr, doc).json()
    second = run_extraction(rev, fdr, doc).json()
    assert first["id"] != second["id"] and len(extraction_rows(engine)) == 2
    assert rev.get(path(fdr, doc, "extraction")).json()["id"] == second["id"]


def test_without_a_run_the_comparison_is_empty_not_invented(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    c = reviewer(engine).get(path(fdr, doc, "comparison"))
    assert c.status_code == 200 and c.json()["extraction"] is None and c.json()["fields"] == [] and c.json()["revision_no"] == 1


@pytest.mark.parametrize(
    ("override", "field", "expected"),
    [
        ({"amount": "Rs. 16,00,000.00"}, "principal_amount", "MISMATCH"),
        ({"amount": "Rs. 15,00,000.01"}, "principal_amount", "MISMATCH"),
        ({"amount": "USD 15,00,000.00"}, "principal_amount", "NOT_COMPARABLE"),
        ({"amount": "Rs. 1,50,0000"}, "principal_amount", "AMBIGUOUS"),
        ({"number": "WR-FDR-002"}, "fdr_number", "MISMATCH"),
        ({"number": "wr-fdr-001"}, "fdr_number", "MISMATCH"),
        ({"bank": "Some Other Bank"}, "bank_name", "MISMATCH"),
        ({"branch": "Elsewhere"}, "branch_name", "MISMATCH"),
        ({"depositor": "Someone Else"}, "depositor_name", "MISMATCH"),
        ({"issue": "16 Jan 2026"}, "issue_date", "MISMATCH"),
        ({"issue": "03/04/2026"}, "issue_date", "MISMATCH"),  # neither reading is 15 Jan
        ({"maturity": "15/01/2027"}, "maturity_date", "MATCH"),
        ({"maturity": "31/02/2027"}, "maturity_date", "AMBIGUOUS"),
        ({"rate": "7.25 %"}, "interest_rate_pct", "MISMATCH"),
        ({"rate": None}, "interest_rate_pct", "NOT_FOUND"),
        ({"number": None}, "fdr_number", "NOT_FOUND"),
        ({"bank": None}, "bank_name", "NOT_FOUND"),
        ({"depositor": None}, "depositor_name", "NOT_FOUND"),
    ],
)
def test_each_kind_of_difference_is_reported_on_exactly_its_own_field(engine, world, override, field, expected):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf(**override))
    rev = reviewer(engine)
    run_extraction(rev, fdr, doc)
    result = outcomes(rev.get(path(fdr, doc, "comparison")))
    assert result[field] == expected, result
    # nothing else moved, except that the currency follows the amount
    others = {k: v for k, v in result.items() if k not in (field, "currency")}
    if field == "principal_amount":
        assert set(others.values()) == {"MATCH"}
    else:
        assert set(others.values()) == {"MATCH"}, others


def test_a_numeric_date_that_can_be_read_two_ways_is_ambiguous_even_when_one_reading_equals_the_submitted_date(engine, world):
    for printed, expected in [("03/04/2026", "AMBIGUOUS"), ("04/03/2026", "AMBIGUOUS"), ("2026-04-03", "MATCH"), ("3 Apr 2026", "MATCH")]:
        _owner, fdr, doc = submitted_with(
            engine, world, receipt_pdf(issue=printed), fdr_number=f"AMB-{printed}", issue_date="2026-04-03", maturity_date="2027-04-03",
        )
        rev = reviewer(engine)
        run_extraction(rev, fdr, doc)
        c = rev.get(path(fdr, doc, "comparison"))
        assert outcomes(c)["issue_date"] == expected, (printed, outcomes(c))
        if expected == "AMBIGUOUS":
            issue = next(f for f in c.json()["fields"] if f["field"] == "issue_date")
            assert issue["extracted"]["value"] is None and len(issue["extracted"]["candidates"]) == 2
            assert "not guessed" in issue["current"]["explanation"]


def test_a_field_the_institution_left_blank_is_not_comparable_never_a_match(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf(), interest_rate_pct=...)
    rev = reviewer(engine)
    run_extraction(rev, fdr, doc)
    result = outcomes(rev.get(path(fdr, doc, "comparison")))
    assert result["interest_rate_pct"] == "NOT_COMPARABLE"


def test_the_comparison_uses_the_frozen_snapshot_so_later_edits_cannot_rewrite_what_was_submitted(engine, world):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    assert decide(rev, "request-correction", {"id": fdr["id"], "row_version": 2}).status_code == 200
    # the institution changes the amount in the correction round; the document still says 15,00,000
    assert owner.patch(f"/fdrs/{fdr['id']}", {"row_version": 3, "principal_amount": "1600000.00"}).status_code == 200
    run_extraction(rev, fdr, doc)
    c = rev.get(path(fdr, doc, "comparison"))
    assert outcomes(c)["principal_amount"] == "MISMATCH"  # against the record as it is now
    assert outcomes(c, "revision")["principal_amount"] == "MATCH"  # against what was actually submitted
    by_field = {f["field"]: f for f in c.json()["fields"]}
    assert by_field["principal_amount"]["current"]["submitted_value"] == "1600000.00"
    assert by_field["principal_amount"]["revision"]["submitted_value"] == "1500000.00"
    # a second submission adds revision 2, and each revision can be compared on request
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 4}).status_code == 200
    latest = rev.get(path(fdr, doc, "comparison"))
    assert latest.json()["revision_no"] == 2 and outcomes(latest, "revision")["principal_amount"] == "MISMATCH"
    first = rev.get(path(fdr, doc, "comparison") + "?revision_no=1")
    assert first.json()["revision_no"] == 1 and outcomes(first, "revision")["principal_amount"] == "MATCH"
    assert rev.get(path(fdr, doc, "comparison") + "?revision_no=9").status_code == 404
    assert rev.get(path(fdr, doc, "comparison") + "?revision_no=0").status_code == 422


def test_extraction_never_changes_the_fdr_its_status_history_or_revisions(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf(amount="Rs. 99,00,000.00", number="X-9"))
    with Session(engine) as s:
        before = {c.name: getattr(s.get(FDR, uuid.UUID(fdr["id"])), c.name) for c in FDR.__table__.columns}
        history_before = s.scalar(select(FDRStatusHistory.id).order_by(FDRStatusHistory.id.desc()).limit(1))
        revisions_before = len(list(s.scalars(select(FDRRevision))))
    rev = reviewer(engine, "AICTE_APPROVER")
    for _ in range(2):
        assert run_extraction(rev, fdr, doc).status_code == 200
        assert rev.get(path(fdr, doc, "comparison")).status_code == 200
    with Session(engine) as s:
        after = {c.name: getattr(s.get(FDR, uuid.UUID(fdr["id"])), c.name) for c in FDR.__table__.columns}
        assert after == before  # not a single column, including status and row_version
        assert s.scalar(select(FDRStatusHistory.id).order_by(FDRStatusHistory.id.desc()).limit(1)) == history_before
        assert len(list(s.scalars(select(FDRRevision)))) == revisions_before
    # and the review decision is still entirely the reviewer's: nothing was decided by the run
    assert row(engine, fdr["id"]).status == "SUBMITTED"
    assert decide(rev, "accept", {"id": fdr["id"], "row_version": 2}).status_code == 200


# ---- failures and retries -----------------------------------------------------------------------
def test_a_scanned_document_without_ocr_is_recorded_as_a_failed_run_with_a_clear_message(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, make_image_pdf())
    rev = reviewer(engine)
    r = run_extraction(rev, fdr, doc)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "FAILED" and body["error_code"] == "OCR_UNAVAILABLE" and body["fields"] == []
    assert "OCR" in body["error_message"] and "nothing was extracted" in body["error_message"]
    c = rev.get(path(fdr, doc, "comparison")).json()
    assert c["fields"] == [] and c["extraction"]["error_code"] == "OCR_UNAVAILABLE"
    assert [e.action for e in audit_rows(engine) if e.action.startswith("document.extract")] == [
        "document.extract_requested", "document.extract_failed"]
    (event,) = audit_events(engine, "document.extract_failed")
    assert event.outcome == "failure" and event.details["reason"] == "OCR_UNAVAILABLE"
    assert c["job"]["status"] == "FAILED" and c["job"]["error_code"] == "OCR_UNAVAILABLE"


def test_a_retry_after_ocr_becomes_available_succeeds_and_is_marked_as_ocr(engine, world, ocr):
    _owner, fdr, doc = submitted_with(engine, world, make_image_pdf())
    rev = reviewer(engine)
    assert run_extraction(rev, fdr, doc).json()["status"] == "FAILED"
    ocr.is_available, ocr.text = True, "\n".join(receipt_lines())
    retry = run_extraction(rev, fdr, doc).json()
    assert retry["status"] == "COMPLETED" and retry["ocr_pages"] == 1
    fields = {f["name"]: f for f in retry["fields"]}
    assert fields["fdr_number"]["source"] == "OCR" and fields["fdr_number"]["confidence"] == "MEDIUM"
    assert fields["bank_name"]["confidence"] == "MEDIUM"
    assert len(extraction_rows(engine)) == 2
    assert rev.get(path(fdr, doc, "extraction")).json()["id"] == retry["id"]
    assert set(outcomes(rev.get(path(fdr, doc, "comparison"))).values()) == {"MATCH"}


@pytest.mark.parametrize(
    ("data", "code"),
    [(b"%PDF-1.4\nthis is not a pdf at all", "MALFORMED_DOCUMENT"),
     (make_pdf([[f"page {i} text"] for i in range(12)]), "TOO_MANY_PAGES"),
     (make_pdf([["--"]]), "OCR_UNAVAILABLE")],  # a page with no real text layer needs OCR, and there is none
    ids=["malformed", "too-many-pages", "blank-page"],
)
def test_unreadable_oversized_and_empty_documents_fail_with_their_own_reason(engine, world, data, code):
    _owner, fdr, doc = submitted_with(engine, world, data)
    r = run_extraction(reviewer(engine), fdr, doc)
    assert r.status_code == 200 and r.json()["status"] == "FAILED" and r.json()["error_code"] == code
    assert r.json()["error_message"] and r.json()["fields"] == []


def test_a_missing_or_altered_stored_file_is_recorded_not_analysed(engine, world, store):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    stored_files(store)[0].write_bytes(receipt_pdf(number="TAMPERED-1"))
    assert run_extraction(rev, fdr, doc).json()["error_code"] == "INTEGRITY_FAILED"
    stored_files(store)[0].unlink()
    assert run_extraction(rev, fdr, doc).json()["error_code"] == "FILE_MISSING"
    assert [r.status for r in extraction_rows(engine)] == ["FAILED", "FAILED"]


def test_the_real_sandbox_path_works_end_to_end(engine, world):
    RUNNER.extractor = SandboxedExtractor()  # a real child process, as in production
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    r = run_extraction(rev, fdr, doc)
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
    assert set(outcomes(rev.get(path(fdr, doc, "comparison"))).values()) == {"MATCH"}


# ---- authorization --------------------------------------------------------------------------------
ALL_ROLES = ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"]
ALLOWED = {"AICTE_REVIEWER", "AICTE_APPROVER"}


@pytest.mark.parametrize("role", ALL_ROLES)
def test_only_reviewers_and_approvers_may_extract_or_read_results(engine, world, role):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    if role in ALLOWED:  # prepare a result for the read endpoints to find
        assert run_extraction(reviewer(engine), fdr, doc).status_code == 200
    actor = institution_user(engine, world, "A") if role == "INSTITUTION_USER" else sign_in(engine, role=role)
    responses = [actor.post(path(fdr, doc, "extractions")), actor.get(path(fdr, doc, "extraction")), actor.get(path(fdr, doc, "comparison"))]
    expected = [202, 200, 200] if role in ALLOWED else [403, 403, 403]
    assert [r.status_code for r in responses] == expected
    if role not in ALLOWED:
        assert len(audit_events(engine, "authz.denied", "denied")) == 3
        assert extraction_rows(engine) == []


def test_the_owning_institution_cannot_see_the_review_side_comparison_of_its_own_documents(engine, world):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    assert run_extraction(reviewer(engine), fdr, doc).status_code == 200
    for r in [owner.get(path(fdr, doc, "comparison")), owner.get(path(fdr, doc, "extraction")), run_extraction(owner, fdr, doc)]:
        assert r.status_code == 403 and "mismatch" not in r.text.lower() and "WR-FDR" not in r.text


def test_documents_of_a_draft_are_unreachable_even_for_reviewers(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    doc = up(owner, fdr["id"], receipt_pdf(), "draft.pdf").json()
    rev = reviewer(engine)
    for r in [run_extraction(rev, fdr, doc), rev.get(path(fdr, doc, "extraction")), rev.get(path(fdr, doc, "comparison"))]:
        assert r.status_code == 404 and "WR-FDR" not in r.text
    assert extraction_rows(engine) == []


def test_unknown_mismatched_and_deleted_documents_are_404(engine, world):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    other = create(owner, world, fdr_number="OTHER-1")
    force_status(engine, other["id"], "SUBMITTED")
    for target_fdr, target_doc in [(fdr, {"id": str(uuid.uuid4())}), (other, doc)]:
        for tail, method in [("extractions", rev.post), ("extraction", rev.get), ("comparison", rev.get)]:
            assert method(path(target_fdr, target_doc, tail)).status_code == 404
    force_status(engine, fdr["id"], "CORRECTION_REQUESTED")
    assert delete(owner, fdr["id"], doc["id"]).status_code == 204
    assert run_extraction(rev, fdr, doc).status_code == 404 and rev.get(path(fdr, doc, "comparison")).status_code == 404


def test_unauthenticated_requests_are_refused(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    anonymous = build_client(engine)
    base = API + path(fdr, doc, "")
    assert anonymous.post(base + "extractions").status_code == 401
    assert anonymous.get(base + "extraction").status_code == 401
    assert anonymous.get(base + "comparison").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-CSRF-Token": "forged"}])
def test_starting_an_extraction_needs_a_valid_csrf_token(engine, world, headers):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    assert rev.client.post(API + path(fdr, doc, "extractions"), headers=headers).status_code == 403
    assert extraction_rows(engine) == [] and audit_events(engine, "security.csrf_rejected", "denied")


def test_an_untrusted_origin_is_refused(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    r = rev.client.post(API + path(fdr, doc, "extractions"), headers={**rev.headers, "Origin": "https://evil.example"})
    assert r.status_code == 403 and extraction_rows(engine) == []


def test_a_pending_password_change_blocks_extraction(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    blocked = sign_in(engine, role="AICTE_REVIEWER", must_change_password=True)
    assert run_extraction(blocked, fdr, doc).status_code == 403
    assert blocked.get(path(fdr, doc, "comparison")).status_code == 403


# ---- audit and privacy ----------------------------------------------------------------------------
def test_the_audit_trail_records_request_and_completion_separately_but_never_text_values_or_names(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf(number="PRIVATE-NUMBER-77"), name="Aadhaar 1234 5678.pdf")
    rev = reviewer(engine)
    body = run_extraction(rev, fdr, doc).json()
    rev.get(path(fdr, doc, "comparison"))
    assert [e.action for e in audit_rows(engine) if e.action.startswith("document.extract")] == [
        "document.extract_requested", "document.extract_completed"]
    (requested,) = audit_events(engine, "document.extract_requested")
    (completed,) = audit_events(engine, "document.extract_completed")
    for event in (requested, completed):
        assert event.outcome == "success" and event.actor_user_id == rev.user_id and event.actor_role == "AICTE_REVIEWER"
        assert event.entity_type == "extraction_job"
    assert requested.entity_id == completed.entity_id
    assert requested.details == {"target_fdr_id": fdr["id"], "target_document_id": doc["id"]}
    assert completed.details == {"target_fdr_id": fdr["id"], "target_document_id": doc["id"], "result_count": 9}
    dump = json.dumps([[e.details, e.entity_id, e.action] for e in audit_rows(engine)])
    for forbidden in [SENTINEL, "PRIVATE-NUMBER-77", "Main Branch", "Test Depositor", "1500000", "Write Bank", "Aadhaar", body["id"]]:
        assert forbidden not in dump
    with Session(engine) as s:
        assert verify_chain(s).ok


def test_stored_results_and_responses_never_contain_the_documents_other_text(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    body = run_extraction(rev, fdr, doc)
    comparison = rev.get(path(fdr, doc, "comparison"))
    stored = json.dumps([r.result for r in extraction_rows(engine)])
    assert SENTINEL not in body.text and SENTINEL not in comparison.text and SENTINEL not in stored


def test_extraction_results_are_not_returned_by_the_document_or_fdr_endpoints(engine, world):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    run_extraction(reviewer(engine), fdr, doc)
    assert not any("extract" in key for key in owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}").json())
    assert "extraction" not in json.dumps(owner.get(f"/fdrs/{fdr['id']}").json()).lower()


HINDI_LINES = [
    "बैंक का नाम: भारतीय स्टेट बैंक", "शाखा: पुणे कैंप", "एफडीआर क्रमांक: १२३४५६७८९०१", "जमाकर्ता का नाम: पिक्ट टेस्ट संस्थान",
    "जमा राशि: रु. १५,००,०००.००", "जमा की तिथि: १५ जनवरी २०२६", "परिपक्वता तिथि: १५ जनवरी २०२७", "ब्याज दर: ७.१० % प्रति वर्ष",
]


def test_a_hindi_document_is_compared_with_its_printed_values_shown_and_scripts_never_guessed(engine, world):
    from tests.extraction_support import make_unicode_pdf

    # An institution that typed its details in Latin script: amounts, dates and the rate compare normally;
    # the identifier is not converted from Devanagari digits; names in another script are not judged.
    _o, fdr, doc = submitted_with(engine, world, make_unicode_pdf([HINDI_LINES]), fdr_number="WR-FDR-001")
    rev = reviewer(engine)
    c = run_and_compare(rev, fdr, doc)
    result = outcomes(c)
    assert result["principal_amount"] == result["issue_date"] == result["maturity_date"] == result["interest_rate_pct"] == "MATCH"
    assert result["currency"] == "MATCH"
    assert result["fdr_number"] == "MISMATCH"
    assert result["branch_name"] == result["depositor_name"] == result["bank_name"] == "NOT_COMPARABLE"
    by_field = {f["field"]: f for f in c.json()["fields"]}
    assert "Devanagari digits" in by_field["fdr_number"]["current"]["explanation"]
    assert "different scripts" in by_field["branch_name"]["current"]["explanation"]
    assert by_field["principal_amount"]["extracted"]["value"] == "1500000.00"
    assert by_field["principal_amount"]["extracted"]["raw"] == "रु. १५,००,०००.००"  # the printed text, kept
    assert by_field["issue_date"]["extracted"]["raw"] == "१५ जनवरी २०२६" and by_field["interest_rate_pct"]["extracted"]["raw"] == "७.१० %"
    assert by_field["fdr_number"]["extracted"]["raw"] is None  # an identifier is already shown as printed
    assert by_field["fdr_number"]["extracted"]["confidence"] == "HIGH" and by_field["fdr_number"]["extracted"]["source"] == "EMBEDDED_TEXT"

    # An institution that typed Devanagari names and number: those compare and match.
    _o, fdr2, doc2 = submitted_with(
        engine, world, make_unicode_pdf([HINDI_LINES]), fdr_number="१२३४५६७८९०१", branch_name="पुणे कैंप",
        depositor_name="पिक्ट टेस्ट संस्थान", name="hindi-2.pdf",
    )
    result2 = outcomes(run_and_compare(rev, fdr2, doc2))
    assert result2["fdr_number"] == result2["branch_name"] == result2["depositor_name"] == "MATCH"


def run_and_compare(who, fdr, doc):
    assert run_extraction(who, fdr, doc).status_code == 200
    return who.get(path(fdr, doc, "comparison"))


def test_a_scanned_document_needing_missing_ocr_language_data_is_refused_with_the_reason(engine, world, tmp_path):
    import sys

    from app.extraction.ocr import TesseractEngine

    script = tmp_path / "fake_tesseract.py"
    script.write_text("import sys\nif '--list-langs' in sys.argv:\n    print('List of available languages (2):')\n    print('eng')\n    print('hin')\n    sys.exit(0)\nprint('x')\n")
    RUNNER.extractor = LocalTextExtractor(TesseractEngine([sys.executable, str(script)], "eng+hin+mar"))
    _o, fdr, doc = submitted_with(engine, world, make_image_pdf(), fdr_number="LANG-1")
    rev = reviewer(engine)
    body = run_extraction(rev, fdr, doc).json()
    assert body["status"] == "FAILED" and body["error_code"] == "OCR_LANGUAGE_UNAVAILABLE" and body["fields"] == []
    assert body["warnings"] == ["missing_ocr_languages:mar"] and "language data" in body["error_message"]
    c = rev.get(path(fdr, doc, "comparison")).json()
    assert c["job"]["status"] == "FAILED" and c["job"]["error_code"] == "OCR_LANGUAGE_UNAVAILABLE"
    (event,) = audit_events(engine, "document.extract_failed")
    assert event.details["reason"] == "OCR_LANGUAGE_UNAVAILABLE" and "mar" not in json.dumps(event.details)


def test_the_document_download_and_validation_behave_as_before(engine, world):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    run_extraction(reviewer(engine), fdr, doc)
    assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content").status_code == 200
    assert docs(engine)[0].status == "ACTIVE"
