"""The document-check summary on the reviewer queue.

It must only read stored results (never start an extraction), keep "not analyzed" neutral, count only
real MISMATCH outcomes as discrepancies, and never reach roles that cannot see extraction data.
"""

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import FDRExtractionJob
from tests.api_support import sign_in
from tests.extraction_support import make_image_pdf
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_documents_api import _private_store, delete, store, up  # noqa: F401
from tests.test_fdr_extraction_api import (  # noqa: F401  (fixtures are used by name)
    ocr,
    path,
    receipt_pdf,
    reviewer,
    submitted_with,
)
from tests.test_fdr_review_api import decide
from tests.test_fdr_write_api import create, force_status, institution_user, world  # noqa: F401
from tests.test_extraction_jobs import jobs, post, runner  # noqa: F401

pytestmark = sqlite_decimal_warning

ZERO = {"documents_analyzed": 0, "documents_pending": 0, "documents_failed": 0, "mismatch_count": 0, "ambiguous_count": 0,
        "not_found_count": 0, "not_comparable_count": 0, "match_count": 0, "mismatch_fields": []}


def queue(who):
    r = who.get("/review/queue")
    assert r.status_code == 200, r.text
    return {item["fdr_number"]: item["document_check"] for item in r.json()["items"]}


def test_an_fdr_with_no_documents_is_labelled_so(engine, world):
    owner = institution_user(engine, world, "A")
    fdr = create(owner, world, fdr_number="NODOC-1")
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code == 200
    assert queue(reviewer(engine))["NODOC-1"] == {"status": "NO_DOCUMENTS", "documents_total": 0, **ZERO}


def test_documents_nobody_has_analysed_are_neutral_and_the_queue_never_starts_an_extraction(engine, world):
    submitted_with(engine, world, receipt_pdf(number="NA-1", amount="Rs. 99,00,000.00"), fdr_number="NA-1")
    rev = reviewer(engine)
    for _ in range(3):  # looking at the queue, however often, starts nothing
        summary = queue(rev)["NA-1"]
    assert summary == {"status": "NOT_ANALYZED", "documents_total": 1, **ZERO}
    assert jobs(engine) == []
    with Session(engine) as s:
        from app.models import FDRDocumentExtraction

        assert s.scalar(select(FDRDocumentExtraction.id).limit(1)) is None


def test_a_waiting_or_running_request_shows_as_pending(engine, world, runner):
    _o, fdr, doc = submitted_with(engine, world, receipt_pdf(number="P-1"), fdr_number="P-1")
    rev = reviewer(engine)
    post(rev, fdr, doc)
    assert queue(rev)["P-1"] == {"status": "PENDING", "documents_total": 1, **{**ZERO, "documents_pending": 1}}
    seen = {}

    class Looking:
        def __init__(self, inner):
            self.inner = inner

        def extract(self, data, content_type, limits):
            seen["running"] = queue(rev)["P-1"]["status"]
            return self.inner.extract(data, content_type, limits)

    runner.extractor = Looking(runner.extractor)
    runner.run_pending()
    assert seen["running"] == "PENDING"  # RUNNING is reported the same way: a check is in progress
    assert queue(rev)["P-1"]["status"] == "ANALYZED"


def test_a_clean_document_counts_only_matches(engine, world, runner):
    _o, fdr, doc = submitted_with(engine, world, receipt_pdf(number="OK-1"), fdr_number="OK-1")
    rev = reviewer(engine)
    post(rev, fdr, doc)
    runner.run_pending()
    assert queue(rev)["OK-1"] == {"status": "ANALYZED", "documents_total": 1, **{**ZERO, "documents_analyzed": 1, "match_count": 9}}


def test_only_real_mismatches_are_counted_as_mismatches(engine, world, runner):
    # amount differs (MISMATCH); the date can be read two ways and one reading is the submitted date
    # (AMBIGUOUS); no maturity date in the document (NOT_FOUND); a rate the institution did not submit
    # (NOT_COMPARABLE); everything else agrees.
    pdf = receipt_pdf(number="MIX-1", amount="Rs. 16,00,000.00", issue="03/04/2026", maturity=None, rate="7.10 %")
    _o, fdr, doc = submitted_with(engine, world, pdf, fdr_number="MIX-1", issue_date="2026-04-03", maturity_date="2027-04-03", interest_rate_pct=...)
    rev = reviewer(engine)
    post(rev, fdr, doc)
    runner.run_pending()
    summary = queue(rev)["MIX-1"]
    assert summary["status"] == "ANALYZED"
    assert (summary["mismatch_count"], summary["ambiguous_count"], summary["not_found_count"], summary["not_comparable_count"]) == (1, 1, 1, 1)
    assert summary["match_count"] == 5 and summary["mismatch_fields"] == ["principal_amount"]
    assert sum(summary[k] for k in ("mismatch_count", "ambiguous_count", "not_found_count", "not_comparable_count", "match_count")) == 9


def test_mismatching_fields_are_named_in_a_stable_order(engine, world, runner):
    _o, fdr, doc = submitted_with(engine, world, receipt_pdf(number="WRONG-9", amount="Rs. 1,00,000.00", bank="Other Bank"), fdr_number="MM-1")
    rev = reviewer(engine)
    post(rev, fdr, doc)
    runner.run_pending()
    assert queue(rev)["MM-1"]["mismatch_fields"] == ["bank_name", "fdr_number", "principal_amount"]


def test_a_failed_check_is_failed_not_a_mismatch(engine, world, runner):
    _o, fdr, doc = submitted_with(engine, world, make_image_pdf(), fdr_number="F-1")
    rev = reviewer(engine)
    post(rev, fdr, doc)
    runner.run_pending()
    assert queue(rev)["F-1"] == {"status": "FAILED", "documents_total": 1, **{**ZERO, "documents_failed": 1}}


def test_several_documents_are_combined_and_only_active_ones_count(engine, world, runner):
    owner, fdr, good = submitted_with(engine, world, receipt_pdf(number="MULTI-1"), fdr_number="MULTI-1")
    rev = reviewer(engine)
    # a returned FDR lets the institution add documents; then it is resubmitted
    assert decide(rev, "request-correction", {"id": fdr["id"], "row_version": 2}).status_code == 200
    bad = up(owner, fdr["id"], receipt_pdf(number="MULTI-1", amount="Rs. 1,00,000.00"), "second.pdf").json()
    scan = up(owner, fdr["id"], make_image_pdf(), "scan.pdf").json()
    removed = up(owner, fdr["id"], receipt_pdf(number="MULTI-1", amount="Rs. 2,00,000.00"), "gone.pdf").json()
    for doc in (good, bad, scan, removed):
        post(rev, fdr, doc)
    runner.run_pending()
    assert delete(owner, fdr["id"], removed["id"]).status_code == 204
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 3}).status_code == 200
    s = queue(rev)["MULTI-1"]
    assert (s["status"], s["documents_total"], s["documents_analyzed"], s["documents_failed"]) == ("ANALYZED", 3, 2, 1)
    assert s["mismatch_count"] == 1 and s["match_count"] == 9 + 8 and s["mismatch_fields"] == ["principal_amount"]


def test_the_summary_compares_with_the_latest_submitted_version_not_a_stale_one(engine, world, runner):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf(number="REV-1"), fdr_number="REV-1")
    rev = reviewer(engine)
    post(rev, fdr, doc)
    runner.run_pending()
    assert queue(rev)["REV-1"]["mismatch_count"] == 0
    assert decide(rev, "request-correction", {"id": fdr["id"], "row_version": 2}).status_code == 200
    assert owner.patch(f"/fdrs/{fdr['id']}", {"row_version": 3, "principal_amount": "1600000.00"}).status_code == 200
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 4}).status_code == 200  # revision 2
    s = queue(rev)["REV-1"]
    assert s["mismatch_count"] == 1 and s["mismatch_fields"] == ["principal_amount"]


def test_only_submitted_fdrs_appear_and_the_summary_does_not_change_paging(engine, world, runner):
    submitted_with(engine, world, receipt_pdf(number="S-1"), fdr_number="S-1")
    _o, fdr2, doc2 = submitted_with(engine, world, receipt_pdf(number="S-2"), fdr_number="S-2")
    force_status(engine, fdr2["id"], "ACCEPTED")
    rev = reviewer(engine)
    body = rev.get("/review/queue?page=1&page_size=1").json()
    assert body["total"] == 1 and [i["fdr_number"] for i in body["items"]] == ["S-1"] and body["items"][0]["document_check"]["status"] == "NOT_ANALYZED"


def test_roles_without_review_access_never_see_the_summary(engine, world, runner):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf(number="PRIV-1", amount="Rs. 9,00,000.00"), fdr_number="PRIV-1")
    post(reviewer(engine), fdr, doc)
    runner.run_pending()
    for who in (owner, sign_in(engine, role="ADMIN"), sign_in(engine, role="AUDITOR")):
        r = who.get("/review/queue")
        assert r.status_code == 403 and "document_check" not in r.text and "mismatch" not in r.text
    # and the registry and the record never carry it, for anyone
    for who in (owner, reviewer(engine), sign_in(engine, role="ADMIN")):
        listing = who.get("/fdrs").json()["items"][0]
        assert "document_check" not in listing and "document_check" not in who.get(f"/fdrs/{fdr['id']}").json()


def test_the_queue_still_works_when_the_summary_cannot_be_computed(engine, world, monkeypatch):
    submitted_with(engine, world, receipt_pdf(number="BROKEN-1"), fdr_number="BROKEN-1")
    import app.api.review as review_module

    def explode(*_a, **_k):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(review_module, "document_checks", explode)
    summary = queue(reviewer(engine))["BROKEN-1"]
    assert summary["status"] == "UNAVAILABLE" and summary["mismatch_count"] == 0 and summary["documents_total"] == 0


def test_the_number_of_queries_does_not_grow_with_the_number_of_queue_items(engine, world, runner):
    rev_holder = {}

    def statements_for_queue() -> int:
        count = {"n": 0}

        def counter(*_a, **_k):
            count["n"] += 1

        event.listen(engine, "before_cursor_execute", counter)
        try:
            assert rev_holder["rev"].get("/review/queue").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", counter)
        return count["n"]

    submitted_with(engine, world, receipt_pdf(number="N-0"), fdr_number="N-0")
    rev_holder["rev"] = reviewer(engine)
    few = statements_for_queue()
    for i in range(1, 7):
        _o, fdr, doc = submitted_with(engine, world, receipt_pdf(number=f"N-{i}"), fdr_number=f"N-{i}")
        post(rev_holder["rev"], fdr, doc)
    runner.run_pending()
    many = statements_for_queue()
    assert many - few <= 3, (few, many)  # a handful of fixed queries, not one set per item
