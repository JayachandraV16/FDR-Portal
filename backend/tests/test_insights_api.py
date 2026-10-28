"""Alerts, exceptions, reports, CSV exports and the release/refund workflow.

All of these are derived from existing records (there are no new tables), so the tests build real
records through the API and check what each role is told. Runs on SQLite and, when TEST_DATABASE_URL is
set, on the dedicated PostgreSQL test database (the race tests need PostgreSQL row locks).
"""

import csv
import io
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.insights import derive
from app.insights.derive import today
from tests.api_support import API, audit_events, audit_rows, build_client, sign_in
from tests.extraction_support import make_image_pdf
from tests.fdr_support import sqlite_decimal_warning
from tests.test_extraction_jobs import post as request_extraction, runner  # noqa: F401
from tests.test_fdr_documents_api import _private_store, store, up  # noqa: F401
from tests.test_fdr_extraction_api import ocr, receipt_pdf, submitted_with  # noqa: F401
from tests.test_fdr_review_api import decide, submitted
from tests.test_fdr_write_api import create, institution_user, pg_world, row, run_together, world  # noqa: F401

pytestmark = sqlite_decimal_warning

ALL_ROLES = ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"]


def day(offset: int) -> str:
    return (today() + timedelta(days=offset)).isoformat()


def dates(maturity_offset: int) -> dict:
    return {"issue_date": day(-400), "maturity_date": day(maturity_offset)}


def who(engine, world, role, inst="A"):
    return institution_user(engine, world, inst) if role == "INSTITUTION_USER" else sign_in(engine, role=role)


def receipt(number, offset=200, **over):
    """A receipt PDF whose printed dates equal the relative dates used for the FDR (dates(offset))."""
    return receipt_pdf(number=number, **{"issue": day(-400), "maturity": day(offset), **over})


def accepted_matured(engine, world, owner=None, number="REL-1", days_ago=1, **overrides):
    """A really accepted FDR whose maturity date has passed, built through the API."""
    owner, fdr = submitted(engine, world, owner, fdr_number=number, **{**dates(-days_ago), **overrides})
    approver = sign_in(engine, role="AICTE_APPROVER")
    assert decide(approver, "accept", fdr).status_code == 200
    return owner, fdr, approver


def categories(response):
    return {c["key"]: c for c in response.json()["categories"]}


def items(response, category):
    return [i for i in response.json()["items"] if i["category"] == category]


# ---- alerts ----------------------------------------------------------------------------------------------
def test_upcoming_maturities_are_alerts_with_severity_by_how_soon(engine, world):
    owner = institution_user(engine, world, "A")
    for number, offset in [("SOON-5", 5), ("SOON-12", 12), ("SOON-25", 25), ("LATER-60", 60)]:
        submitted(engine, world, owner, fdr_number=number, **dates(offset))
    r = owner.get("/alerts")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    found = {i["fdr_number"]: i for i in items(r, "MATURITY_SOON")}
    assert set(found) == {"SOON-5", "SOON-12", "SOON-25"}  # the 60-day one is not an alert yet
    assert [found[n]["severity"] for n in ("SOON-5", "SOON-12", "SOON-25")] == ["HIGH", "MEDIUM", "LOW"]
    assert found["SOON-5"]["days"] == 5 and found["SOON-5"]["message"] == "Matures in 5 days"
    assert categories(r)["MATURITY_SOON"]["total"] == 3 and categories(r)["MATURITY_SOON"]["exact"] is True
    assert "not stored notifications" in r.json()["basis"]


def test_a_draft_is_never_an_alert(engine, world):
    owner = institution_user(engine, world, "A")
    create(owner, world, fdr_number="DRAFT-SOON", **dates(3))
    assert categories(owner.get("/alerts"))["MATURITY_SOON"]["total"] == 0


def test_overdue_fdrs_are_alerts_until_a_release_is_approved(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="OD-1", days_ago=3)
    r = owner.get("/alerts")
    (alert,) = items(r, "OVERDUE")
    assert alert["severity"] == "HIGH" and alert["days"] == -3 and "3 days past maturity" in alert["message"]
    assert owner.post(f"/fdrs/{fdr['id']}/release/request").status_code == 201
    assert len(items(owner.get("/alerts"), "OVERDUE")) == 1  # requested is not approved
    assert approver.post(f"/fdrs/{fdr['id']}/release/approve").status_code == 200
    assert items(owner.get("/alerts"), "OVERDUE") == [] and categories(owner.get("/alerts"))["OVERDUE"]["total"] == 0


def test_reviewers_are_told_what_awaits_them_and_institutions_only_about_their_own(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    submitted(engine, world, a, fdr_number="WAIT-A", **dates(200))
    submitted(engine, world, b, fdr_number="WAIT-B", **dates(200))
    reviewer_alerts = sign_in(engine, role="AICTE_REVIEWER").get("/alerts")
    assert categories(reviewer_alerts)["AWAITING_REVIEW"]["total"] == 2
    assert {i["fdr_number"] for i in items(reviewer_alerts, "AWAITING_REVIEW")} == {"WAIT-A", "WAIT-B"}
    assert items(reviewer_alerts, "AWAITING_REVIEW")[0]["link"].startswith("/review/")
    own = a.get("/alerts")
    assert {i["fdr_number"] for i in items(own, "AWAITING_REVIEW")} == {"WAIT-A"} and categories(own)["AWAITING_REVIEW"]["total"] == 1
    assert items(own, "AWAITING_REVIEW")[0]["link"].endswith("/edit")


def test_a_correction_request_is_urgent_for_the_institution_and_informational_for_reviewers(engine, world):
    owner, fdr = submitted(engine, world, None, fdr_number="CORR-1", **dates(200))
    assert decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", fdr).status_code == 200
    (mine,) = items(owner.get("/alerts"), "CORRECTION_REQUESTED")
    (theirs,) = items(sign_in(engine, role="AICTE_REVIEWER").get("/alerts"), "CORRECTION_REQUESTED")
    assert (mine["severity"], theirs["severity"]) == ("HIGH", "LOW") and "update and resubmit" in mine["message"]


def test_roles_that_do_not_review_get_only_the_alerts_that_apply_to_them(engine, world):
    submitted(engine, world, None, fdr_number="ADM-1", **dates(5))
    for role in ("ADMIN", "AUDITOR"):
        r = sign_in(engine, role=role).get("/alerts")
        assert set(categories(r)) == {"MATURITY_SOON", "OVERDUE"}
        assert items(r, "MATURITY_SOON")[0]["link"] == "/fdrs"


def test_release_requests_awaiting_a_decision_are_alerts_for_reviewers(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="RQ-1")
    owner.post(f"/fdrs/{fdr['id']}/release/request")
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    (alert,) = items(reviewer.get("/alerts"), "RELEASE_REQUESTED")
    assert "awaiting an approver" in alert["message"]
    assert "RELEASE_REQUESTED" not in categories(owner.get("/alerts"))
    approver.post(f"/fdrs/{fdr['id']}/release/approve")
    assert items(reviewer.get("/alerts"), "RELEASE_REQUESTED") == []


def test_failed_document_processing_is_an_alert_for_reviewers_only(engine, world, runner):
    owner, fdr, doc = submitted_with(engine, world, make_image_pdf(), fdr_number="FAIL-1", **dates(200))
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    request_extraction(reviewer, fdr, doc)
    runner.run_pending()
    r = reviewer.get("/alerts")
    (alert,) = items(r, "EXTRACTION_FAILED")
    assert "could not be analysed" in alert["message"] and categories(r)["EXTRACTION_FAILED"]["available"] is True
    assert "EXTRACTION_FAILED" not in categories(owner.get("/alerts"))


def test_a_category_is_unavailable_not_zero_when_the_document_migrations_are_missing(engine, world, monkeypatch):
    monkeypatch.setattr(derive, "document_tables_ready", lambda db: False)
    c = categories(sign_in(engine, role="AICTE_REVIEWER").get("/alerts"))["EXTRACTION_FAILED"]
    assert c["available"] is False and "migrations" in c["note"]


def test_totals_say_when_only_part_of_the_data_was_examined(engine, world, monkeypatch):
    owner = institution_user(engine, world, "A")
    for i in range(3):
        submitted(engine, world, owner, fdr_number=f"CAP-{i}", **dates(3 + i))
    monkeypatch.setattr(derive, "ITEMS_PER_CATEGORY", 2)
    c = categories(owner.get("/alerts"))["MATURITY_SOON"]
    assert (c["total"], c["shown"], c["exact"]) == (3, 2, True)  # a SQL count: the total is exact even though only 2 are listed
    for i in range(3):
        accepted_matured(engine, world, owner, number=f"CAPOD-{i}", days_ago=2 + i)
    monkeypatch.setattr(derive, "OVERDUE_SCAN", 2)
    c = categories(owner.get("/alerts"))["OVERDUE"]
    assert c["exact"] is False and "may be higher" in c["note"]  # a bounded scan is labelled as a lower bound


@pytest.mark.parametrize("path", ["/alerts", "/exceptions", "/reports/summary", "/reports/fdrs.csv", "/release", "/exceptions".replace("exceptions", "reports/exceptions.csv")])
def test_none_of_these_is_available_without_signing_in(engine, world, path):
    assert build_client(engine).get(API + path).status_code == 401


def test_a_pending_password_change_blocks_them(engine, world):
    blocked = sign_in(engine, role="AICTE_REVIEWER", must_change_password=True)
    for path in ("/alerts", "/exceptions", "/reports/summary", "/release"):
        assert blocked.get(path).status_code == 403


# ---- exceptions ------------------------------------------------------------------------------------------
def kinds(response):
    return {k["key"]: k for k in response.json()["kinds"]}


def of(response, kind):
    return [i for i in response.json()["items"] if i["kind"] == kind]


def test_an_incomplete_record_is_an_exception_with_the_missing_fields_named(engine, world):
    owner, fdr = submitted(engine, world, None, fdr_number="INC-1", interest_rate_pct=..., purpose=..., **dates(200))
    r = owner.get("/exceptions")
    (e,) = of(r, "INCOMPLETE_RECORD")
    assert e["severity"] == "LOW" and e["fdr_number"] == "INC-1" and "interest rate" in e["issue"] and "purpose" in e["issue"]
    assert e["status"] == "SUBMITTED" and e["institution_code"] == "WR-A" and e["link"].endswith("/edit")
    assert r.json()["complete"] is True and r.json()["scanned"] == r.json()["eligible_total"] == 1


def test_a_submitted_fdr_with_no_document_is_a_missing_document_exception_for_roles_that_can_use_documents(engine, world):
    owner, fdr = submitted(engine, world, None, fdr_number="NODOC-1", **dates(200))
    for client in (owner, sign_in(engine, role="AICTE_REVIEWER")):
        r = client.get("/exceptions")
        assert [e["fdr_number"] for e in of(r, "MISSING_DOCUMENT")] == ["NODOC-1"] and kinds(r)["MISSING_DOCUMENT"]["available"] is True
    admin = sign_in(engine, role="ADMIN").get("/exceptions")
    assert of(admin, "MISSING_DOCUMENT") == [] and kinds(admin)["MISSING_DOCUMENT"]["available"] is False
    assert kinds(admin)["MISSING_DOCUMENT"]["note"] == "Not available to your role."


def test_mismatches_ambiguity_and_failures_come_from_stored_results_and_only_for_reviewers(engine, world, runner):
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    mismatch = submitted_with(engine, world, receipt("X-1", amount="Rs. 16,00,000.00"), fdr_number="X-1", **dates(200))
    ambiguous = submitted_with(engine, world, receipt("X-2", issue="03/04/2026"), fdr_number="X-2", issue_date="2026-04-03", maturity_date=day(200))
    failed = submitted_with(engine, world, make_image_pdf(), fdr_number="X-3", **dates(200))
    clean = submitted_with(engine, world, receipt("X-4"), fdr_number="X-4", **dates(200))
    unanalysed = submitted_with(engine, world, receipt("X-5", amount="Rs. 1,00,000.00"), fdr_number="X-5", **dates(200))
    for _owner, fdr, doc in (mismatch, ambiguous, failed, clean):
        request_extraction(reviewer, fdr, doc)
    runner.run_pending()
    r = reviewer.get("/exceptions")
    assert [(e["fdr_number"], e["severity"]) for e in of(r, "FIELD_MISMATCH")] == [("X-1", "HIGH")]
    assert "principal amount" in of(r, "FIELD_MISMATCH")[0]["issue"]
    assert [e["fdr_number"] for e in of(r, "EXTRACTION_AMBIGUOUS")] == ["X-2"] and "issue date" in of(r, "EXTRACTION_AMBIGUOUS")[0]["issue"]
    assert [e["fdr_number"] for e in of(r, "EXTRACTION_FAILED")] == ["X-3"]
    flagged = {e["fdr_number"] for e in r.json()["items"] if e["kind"] in ("FIELD_MISMATCH", "EXTRACTION_AMBIGUOUS", "EXTRACTION_FAILED")}
    assert flagged == {"X-1", "X-2", "X-3"}  # a clean result, and a document nobody analysed, raise nothing
    assert all(kinds(r)[k]["available"] for k in ("FIELD_MISMATCH", "EXTRACTION_AMBIGUOUS", "EXTRACTION_FAILED"))
    # the owning institution never sees extraction-derived exceptions, and is told why
    owner_view = mismatch[0].get("/exceptions")
    assert not any(e["kind"] in ("FIELD_MISMATCH", "EXTRACTION_AMBIGUOUS", "EXTRACTION_FAILED") for e in owner_view.json()["items"])
    assert kinds(owner_view)["FIELD_MISMATCH"]["available"] is False and "reviewers and approvers only" in kinds(owner_view)["FIELD_MISMATCH"]["note"]


def test_a_matured_fdr_without_an_approved_release_is_an_overdue_exception(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="OVD-1", days_ago=5)
    (e,) = of(owner.get("/exceptions"), "OVERDUE_FDR")
    assert e["severity"] == "HIGH" and "5 days past maturity" in e["issue"]
    owner.post(f"/fdrs/{fdr['id']}/release/request")
    approver.post(f"/fdrs/{fdr['id']}/release/approve")
    assert of(owner.get("/exceptions"), "OVERDUE_FDR") == []


def test_exceptions_respect_institution_visibility(engine, world):
    submitted(engine, world, institution_user(engine, world, "A"), fdr_number="EXA", interest_rate_pct=..., **dates(200))
    submitted(engine, world, institution_user(engine, world, "B"), fdr_number="EXB", interest_rate_pct=..., **dates(200))
    assert {e["fdr_number"] for e in institution_user(engine, world, "A").get("/exceptions").json()["items"]} == {"EXA"}
    assert {e["fdr_number"] for e in sign_in(engine, role="AICTE_REVIEWER").get("/exceptions").json()["items"]} == {"EXA", "EXB"}


def test_the_scan_is_labelled_incomplete_when_it_was_bounded(engine, world, monkeypatch):
    owner = institution_user(engine, world, "A")
    for i in range(3):
        submitted(engine, world, owner, fdr_number=f"SC-{i}", interest_rate_pct=..., **dates(200 + i))
    monkeypatch.setattr(derive, "SCAN_LIMIT", 2)
    body = owner.get("/exceptions").json()
    assert (body["scanned"], body["eligible_total"], body["complete"]) == (2, 3, False)


def test_extraction_exceptions_are_unavailable_not_zero_when_the_migrations_are_missing(engine, world, monkeypatch):
    submitted(engine, world, None, fdr_number="MIG-1", interest_rate_pct=..., **dates(200))
    monkeypatch.setattr(derive, "document_tables_ready", lambda db: False)
    r = sign_in(engine, role="AICTE_REVIEWER").get("/exceptions")
    assert kinds(r)["FIELD_MISMATCH"]["available"] is False and "migrations" in kinds(r)["FIELD_MISMATCH"]["note"]
    assert len(of(r, "INCOMPLETE_RECORD")) == 1  # what does not need documents still works


# ---- reports -------------------------------------------------------------------------------------------
def test_totals_cover_the_whole_authorised_dataset_not_one_page_and_are_exact(engine, world):
    owner = institution_user(engine, world, "A")
    for i in range(27):  # more than one registry page (25)
        submitted(engine, world, owner, fdr_number=f"PG-{i:02d}", principal_amount="100.10", **dates(200))
    r = owner.get("/reports/summary")
    body = r.json()
    assert r.status_code == 200 and body["fdr_total"] == 27
    assert body["principal_live_total"] == "2702.70" and Decimal(body["principal_live_total"]) == Decimal("100.10") * 27  # exact, no float drift
    assert [(s["key"], s["count"], s["principal"]) for s in body["by_status"]] == [("SUBMITTED", 27, "2702.70")]
    assert [(i["count"], i["principal"]) for i in body["by_institution"]] == [(27, "2702.70")]
    assert owner.get("/fdrs?page_size=25").json()["total"] == 27  # the registry is paged; the report is not


def test_a_report_never_includes_what_the_caller_may_not_see(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    submitted(engine, world, a, fdr_number="RA-1", principal_amount="500.00", **dates(200))
    from tests.test_fdr_write_api import create as make

    make(b, world, fdr_number="RB-DRAFT", principal_amount="999.00")  # B's private draft
    submitted(engine, world, b, fdr_number="RB-1", principal_amount="700.00", **dates(200))
    mine = a.get("/reports/summary").json()
    assert mine["fdr_total"] == 1 and mine["principal_live_total"] == "500.00" and len(mine["by_institution"]) == 1
    everyone = sign_in(engine, role="AICTE_REVIEWER").get("/reports/summary").json()
    assert everyone["fdr_total"] == 2 and everyone["principal_live_total"] == "1200.00"  # the draft is not included
    assert {i["count"] for i in everyone["by_institution"]} == {1} and len(everyone["by_institution"]) == 2
    assert "DRAFT" not in {s["key"] for s in everyone["by_status"]}
    assert "DRAFT" in {s["key"] for s in b.get("/reports/summary").json()["by_status"]}


def test_maturity_buckets_and_release_counts(engine, world):
    owner = institution_user(engine, world, "A")
    for number, offset in [("B-OD", -4), ("B-10", 10), ("B-45", 45), ("B-80", 80), ("B-200", 200)]:
        submitted(engine, world, owner, fdr_number=number, **dates(offset))
    buckets = {m["key"]: m["count"] for m in owner.get("/reports/summary").json()["maturities"]}
    assert buckets == {"OVERDUE": 1, "D30": 1, "D60": 1, "D90": 1}
    o, fdr, approver = accepted_matured(engine, world, owner, number="B-REL", days_ago=2)
    o2, fdr2, _ = accepted_matured(engine, world, owner, number="B-REL2", days_ago=2)
    owner.post(f"/fdrs/{fdr['id']}/release/request")
    owner.post(f"/fdrs/{fdr2['id']}/release/request")
    approver.post(f"/fdrs/{fdr2['id']}/release/approve")
    release = {r["key"]: r["count"] for r in owner.get("/reports/summary").json()["release"]}
    assert release == {"NONE": 0, "REQUESTED": 1, "APPROVED": 1, "REJECTED": 0}


def test_verification_and_exception_metrics_are_unavailable_where_the_data_is_not_theirs_or_not_computable(engine, world, runner, monkeypatch):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf(number="V-1", amount="Rs. 9,00,000.00"), fdr_number="V-1", **dates(200))
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    request_extraction(reviewer, fdr, doc)
    runner.run_pending()
    r = reviewer.get("/reports/summary").json()
    assert r["verification_status"]["available"] is True
    assert {v["key"]: v["count"] for v in r["verification"]}["MISMATCH"] == 1
    mine = owner.get("/reports/summary").json()
    assert mine["verification_status"]["available"] is False and mine["verification"] == []
    assert "reviewers and approvers only" in mine["verification_status"]["note"]
    monkeypatch.setattr("app.api.reports.document_tables_ready", lambda db: False)
    assert "migrations" in reviewer.get("/reports/summary").json()["verification_status"]["note"]


def csv_rows(response):
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_the_fdr_export_contains_every_authorised_row_and_nothing_else(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    for i in range(27):
        submitted(engine, world, a, fdr_number=f"EX-A{i:02d}", **dates(200))
    submitted(engine, world, b, fdr_number="EX-B", **dates(200))
    r = a.get("/reports/fdrs.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="fdr-report-{today().isoformat()}.csv"'
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    rows = csv_rows(r)
    assert rows[0][:3] == ["FDR number", "Institution code", "Institution"] and len(rows) == 1 + 27  # all 27, not a page of 25
    assert all(row[1] == "WR-A" for row in rows[1:]) and "EX-B" not in r.text
    everyone = csv_rows(sign_in(engine, role="AICTE_REVIEWER").get("/reports/fdrs.csv"))
    assert len(everyone) == 1 + 28
    (event,) = [e for e in audit_events(engine, "report.export") if e.actor_user_id == a.user_id]
    assert event.details == {"filters": ["fdrs"], "result_count": 27} and event.entity_type == "report"
    assert "EX-A00" not in json.dumps([e.details for e in audit_rows(engine)])  # counts only, never content


def test_a_cell_that_a_spreadsheet_would_run_as_a_formula_is_neutralised(engine, world):
    owner = institution_user(engine, world, "A")
    submitted(engine, world, owner, fdr_number='=HYPERLINK("http://evil.example","x")', branch_name="+cmd|' /C calc'!A0", **dates(200))
    submitted(engine, world, owner, fdr_number="@SUM(1)", **dates(200))
    rows = csv_rows(owner.get("/reports/fdrs.csv"))
    numbers = {row[0] for row in rows[1:]}
    assert numbers == {"'=HYPERLINK(\"http://evil.example\",\"x\")", "'@SUM(1)"}
    assert any(row[4].startswith("'+cmd") for row in rows[1:])


def test_the_exceptions_export_is_for_reviewers_and_approvers_only(engine, world):
    submitted(engine, world, None, fdr_number="CSV-X", interest_rate_pct=..., **dates(200))
    for role in ("INSTITUTION_USER", "ADMIN", "AUDITOR"):
        assert who(engine, world, role).get("/reports/exceptions.csv").status_code == 403
    r = sign_in(engine, role="AICTE_APPROVER").get("/reports/exceptions.csv")
    rows = csv_rows(r)
    assert r.status_code == 200 and rows[0][0] == "Severity" and any(row[2] == "CSV-X" for row in rows[1:])
    assert audit_events(engine, "report.export")


# ---- release / refund ---------------------------------------------------------------------------------------
def test_only_an_accepted_matured_fdr_is_eligible(engine, world):
    owner, early = submitted(engine, world, None, fdr_number="E-1", **dates(200))
    approver = sign_in(engine, role="AICTE_APPROVER")
    decide(approver, "accept", early)
    owner2, pending = submitted(engine, world, owner, fdr_number="E-2", **dates(-2))  # matured but not accepted
    o3, ok, _ = accepted_matured(engine, world, owner, number="E-3")
    listing = owner.get("/release").json()
    assert [i["fdr_number"] for i in listing["items"]] == ["E-3"] and listing["total"] == 1 and "not a payment" in listing["note"] or "does not transfer funds" in listing["note"]
    for fdr, text in [(early, "Not yet matured"), (pending, "Only an accepted FDR")]:
        d = owner.get(f"/fdrs/{fdr['id']}/release").json()
        assert d["eligible"] is False and text in d["eligibility_note"] and d["can_request"] is False
        r = owner.post(f"/fdrs/{fdr['id']}/release/request")
        assert r.status_code == 409 and text in r.json()["detail"]
    assert owner.get(f"/fdrs/{ok['id']}/release").json()["can_request"] is True


def test_the_institution_requests_release_and_the_request_is_recorded_without_changing_the_fdr(engine, world):
    owner, fdr, _ = accepted_matured(engine, world, number="R-1")
    before = row(engine, fdr["id"])
    r = owner.post(f"/fdrs/{fdr['id']}/release/request")
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["state"] == "REQUESTED" and body["can_request"] is False and body["can_decide"] is False
    (event,) = body["history"]
    assert event["action"] == "release.requested" and event["actor_role"] == "INSTITUTION_USER" and event["actor_name"] and event["reason"] is None
    after = row(engine, fdr["id"])
    assert (after.status, after.row_version, after.updated_at) == (before.status, before.row_version, before.updated_at)  # the FDR is untouched
    (audit,) = audit_events(engine, "release.requested", "success")
    assert audit.actor_user_id == owner.user_id and audit.entity_type == "fdr" and audit.entity_id == fdr["id"]
    assert audit.details == {"target_fdr_id": fdr["id"]}
    assert owner.post(f"/fdrs/{fdr['id']}/release/request").status_code == 409  # not twice
    assert len(audit_events(engine, "release.requested", "success")) == 1


def test_an_approver_decides_and_each_decision_is_final_for_its_request(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="R-2")
    assert approver.post(f"/fdrs/{fdr['id']}/release/approve").status_code == 409  # nothing to decide yet
    owner.post(f"/fdrs/{fdr['id']}/release/request")
    d = approver.get(f"/fdrs/{fdr['id']}/release").json()
    assert d["can_decide"] is True and d["can_request"] is False
    r = approver.post(f"/fdrs/{fdr['id']}/release/approve")
    assert r.status_code == 200 and r.json()["state"] == "APPROVED"
    assert [(h["action"], h["actor_role"]) for h in r.json()["history"]] == [("release.requested", "INSTITUTION_USER"), ("release.approved", "AICTE_APPROVER")]
    for call in (lambda: approver.post(f"/fdrs/{fdr['id']}/release/approve"), lambda: owner.post(f"/fdrs/{fdr['id']}/release/request"),
                 lambda: approver.post(f"/fdrs/{fdr['id']}/release/reject", {"reason": "OTHER"})):
        assert call().status_code == 409
    assert row(engine, fdr["id"]).status == "ACCEPTED"  # still just an accepted FDR: no money moved, nothing claimed


def test_a_rejected_request_records_a_fixed_reason_and_may_be_made_again(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="R-3")
    owner.post(f"/fdrs/{fdr['id']}/release/request")
    assert approver.post(f"/fdrs/{fdr['id']}/release/reject", {"reason": "free text is not accepted"}).status_code == 422
    assert approver.post(f"/fdrs/{fdr['id']}/release/reject", {}).status_code == 422
    assert approver.post(f"/fdrs/{fdr['id']}/release/reject", {"reason": "OTHER", "comment": "x"}).status_code == 422
    r = approver.post(f"/fdrs/{fdr['id']}/release/reject", {"reason": "INCOMPLETE_SUPPORTING_INFORMATION"})
    assert r.status_code == 200 and r.json()["state"] == "REJECTED" and r.json()["can_request"] is False  # for the approver
    assert owner.get(f"/fdrs/{fdr['id']}/release").json()["can_request"] is True  # but the institution may ask again
    assert r.json()["history"][-1]["reason"] == "INCOMPLETE_SUPPORTING_INFORMATION"
    assert owner.post(f"/fdrs/{fdr['id']}/release/request").json()["state"] == "REQUESTED"
    assert approver.post(f"/fdrs/{fdr['id']}/release/approve").json()["state"] == "APPROVED"
    assert [h["action"] for h in approver.get(f"/fdrs/{fdr['id']}/release").json()["history"]] == [
        "release.requested", "release.rejected", "release.requested", "release.approved"]


def test_who_may_do_what(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="R-4")
    owner.post(f"/fdrs/{fdr['id']}/release/request")
    url = f"/fdrs/{fdr['id']}/release"
    for role in ("AICTE_REVIEWER", "ADMIN", "AUDITOR", "AICTE_APPROVER"):
        assert who(engine, world, role).post(url + "/request").status_code == 403
    for role in ("INSTITUTION_USER", "AICTE_REVIEWER", "ADMIN", "AUDITOR"):
        actor = who(engine, world, role)
        assert actor.post(url + "/approve").status_code == 403
        assert actor.post(url + "/reject", {"reason": "OTHER"}).status_code == 403
    for role in ALL_ROLES:  # everyone who may see the record may read its release state
        assert who(engine, world, role).get(url).status_code == 200
    assert row(engine, fdr["id"]).status == "ACCEPTED" and len(audit_events(engine, "release.approved", "success")) == 0
    assert audit_events(engine, "authz.denied", "denied")


def test_another_institution_cannot_see_or_touch_the_release(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="R-5")
    intruder = institution_user(engine, world, "B")
    assert intruder.get(f"/fdrs/{fdr['id']}/release").status_code == 404
    assert intruder.post(f"/fdrs/{fdr['id']}/release/request").status_code == 404
    assert intruder.get("/release").json()["total"] == 0
    assert audit_events(engine, "authz.scope_denied", "denied")
    assert audit_events(engine, "release.requested", "success") == []


def test_a_refused_attempt_is_audited_but_never_changes_the_state(engine, world):
    owner, fdr, approver = accepted_matured(engine, world, number="R-6")
    owner.post(f"/fdrs/{fdr['id']}/release/request")
    owner.post(f"/fdrs/{fdr['id']}/release/request")  # refused: already open
    denied = audit_events(engine, "release.requested", "denied")
    assert len(denied) == 1 and denied[0].details["reason"] == "invalid_release_state"
    assert owner.get(f"/fdrs/{fdr['id']}/release").json()["state"] == "REQUESTED"
    assert len(owner.get(f"/fdrs/{fdr['id']}/release").json()["history"]) == 1  # the denial is not a release event
    with Session(engine) as s:
        assert verify_chain(s).ok


def test_the_release_list_filters_by_state_and_pages(engine, world):
    owner = institution_user(engine, world, "A")
    approver = sign_in(engine, role="AICTE_APPROVER")
    made = [accepted_matured(engine, world, owner, number=f"L-{i}", days_ago=10 - i)[1] for i in range(4)]
    owner.post(f"/fdrs/{made[0]['id']}/release/request")
    owner.post(f"/fdrs/{made[1]['id']}/release/request")
    approver.post(f"/fdrs/{made[1]['id']}/release/approve")
    states = {i["fdr_number"]: i["state"] for i in approver.get("/release").json()["items"]}
    assert states == {"L-0": "REQUESTED", "L-1": "APPROVED", "L-2": "NONE", "L-3": "NONE"}
    assert [i["fdr_number"] for i in approver.get("/release?state=NONE").json()["items"]] == ["L-2", "L-3"]
    page = approver.get("/release?state=NONE&page_size=1&page=2").json()
    assert page["total"] == 2 and page["total_pages"] == 2 and [i["fdr_number"] for i in page["items"]] == ["L-3"]
    assert approver.get("/release?state=BOGUS").status_code == 422


@pytest.mark.parametrize("method_path", [("post", "/request"), ("post", "/approve"), ("post", "/reject")])
def test_release_changes_need_a_valid_session_csrf_token_and_origin(engine, world, method_path):
    owner, fdr, approver = accepted_matured(engine, world, number="R-7")
    suffix = method_path[1]
    actor = owner if suffix == "/request" else approver
    url = API + f"/fdrs/{fdr['id']}/release{suffix}"
    body = {"reason": "OTHER"} if suffix == "/reject" else None
    assert build_client(engine).post(url, json=body).status_code == 401
    assert actor.client.post(url, json=body).status_code == 403  # no CSRF token
    assert actor.client.post(url, json=body, headers={**actor.headers, "Origin": "https://evil.example"}).status_code == 403
    assert audit_events(engine, "release.requested", "success") == []


# ---- the whole demonstration, end to end ---------------------------------------------------------------------------
def test_the_complete_demo_flow_ends_consistent(engine, world, runner):
    from tests.test_fdr_write_api import history, revisions
    from tests.test_fdr_documents_api import docs as stored_documents

    owner = institution_user(engine, world, "A")
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    approver = sign_in(engine, role="AICTE_APPROVER")

    # 1-3. The institution creates a draft, attaches the supporting document (documents can only change while
    # the record is a draft or returned), and submits it.
    fdr = create(owner, world, fdr_number="E2E-1", **dates(-1))
    upload = up(owner, fdr["id"], receipt("E2E-1", offset=-1), "receipt.pdf")
    assert upload.status_code == 201
    doc = upload.json()
    submit = owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1})
    assert submit.status_code == 200 and submit.json()["status"] == "SUBMITTED"
    assert up(owner, fdr["id"], receipt("E2E-1", offset=-1), "late.pdf").status_code == 409  # frozen once submitted

    # 4-6. A reviewer runs the extraction in the background and reads the field-level comparison.
    path_to = f"/fdrs/{fdr['id']}/documents/{doc['id']}"
    accepted = reviewer.post(path_to + "/extractions")
    assert accepted.status_code == 202 and accepted.json()["status"] == "PENDING"
    assert reviewer.get(path_to + "/comparison").json()["job"]["status"] == "PENDING"
    runner.run_pending()
    comparison = reviewer.get(path_to + "/comparison").json()
    assert comparison["job"]["status"] == "COMPLETED"
    assert {f["current"]["outcome"] for f in comparison["fields"]} == {"MATCH"}  # a consistent document: and still not "verified"
    assert "FIELD_MISMATCH" not in {e["kind"] for e in reviewer.get("/exceptions").json()["items"]}

    # 7-9. The reviewer asks for corrections; the institution is alerted, corrects and resubmits.
    assert decide(reviewer, "request-correction", {"id": fdr["id"], "row_version": 2}).status_code == 200
    assert [a["fdr_number"] for a in items(owner.get("/alerts"), "CORRECTION_REQUESTED")] == ["E2E-1"]
    assert owner.patch(f"/fdrs/{fdr['id']}", {"row_version": 3, "purpose": "Security deposit (corrected)"}).status_code == 200
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 4}).status_code == 200
    assert items(owner.get("/alerts"), "CORRECTION_REQUESTED") == []
    assert [a["fdr_number"] for a in items(reviewer.get("/alerts"), "AWAITING_REVIEW")] == ["E2E-1"]

    # 10. An approver accepts it. The FDR has matured, so it is now overdue for release.
    assert decide(approver, "accept", {"id": fdr["id"], "row_version": 5}).status_code == 200
    assert [a["fdr_number"] for a in items(owner.get("/alerts"), "OVERDUE")] == ["E2E-1"]
    assert "OVERDUE_FDR" in {e["kind"] for e in owner.get("/exceptions").json()["items"]}

    # 11. The institution requests release; the approver authorises it. Nothing about the FDR changes.
    frozen = row(engine, fdr["id"])
    assert owner.post(f"/fdrs/{fdr['id']}/release/request").json()["state"] == "REQUESTED"
    assert [a["fdr_number"] for a in items(reviewer.get("/alerts"), "RELEASE_REQUESTED")] == ["E2E-1"]
    assert approver.post(f"/fdrs/{fdr['id']}/release/approve").json()["state"] == "APPROVED"
    after = row(engine, fdr["id"])
    assert (after.status, after.row_version) == (frozen.status, frozen.row_version) == ("ACCEPTED", 6)
    assert items(owner.get("/alerts"), "OVERDUE") == [] and "OVERDUE_FDR" not in {e["kind"] for e in owner.get("/exceptions").json()["items"]}

    # 12-14. The report reflects exactly this, and the history and audit trail are consistent.
    report = approver.get("/reports/summary").json()
    assert report["fdr_total"] == 1 and {s["key"]: s["count"] for s in report["by_status"]} == {"ACCEPTED": 1}
    assert {r["key"]: r["count"] for r in report["release"]} == {"NONE": 0, "REQUESTED": 0, "APPROVED": 1, "REJECTED": 0}
    assert [h.to_status for h in history(engine, fdr["id"])] == ["DRAFT", "SUBMITTED", "CORRECTION_REQUESTED", "SUBMITTED", "ACCEPTED"]
    assert len(revisions(engine, fdr["id"])) == 2 and [d.status for d in stored_documents(engine, fdr["id"])] == ["ACTIVE"]
    actions = [e.action for e in audit_rows(engine)]
    for expected in ("fdr.create", "document.upload", "fdr.submit", "document.extract_requested", "document.extract_completed",
                     "fdr.request_correction", "fdr.update", "fdr.resubmit", "fdr.accept", "release.requested", "release.approved"):
        assert expected in actions, expected
    assert actions.index("fdr.accept") < actions.index("release.requested") < actions.index("release.approved")
    with Session(engine) as s:
        assert verify_chain(s).ok


# ---- concurrency (PostgreSQL only) -------------------------------------------------------------------------------
def test_simultaneous_requests_and_decisions_succeed_exactly_once(pg_head, pg_world):
    owners = [institution_user(pg_head, pg_world, email=f"rel-{i}@example.invalid") for i in range(4)]
    approvers = [sign_in(pg_head, role="AICTE_APPROVER") for _ in range(4)]
    owner, fdr = submitted(pg_head, pg_world, owners[0], fdr_number="RACE-1", **dates(-1))
    assert decide(approvers[0], "accept", fdr).status_code == 200
    statuses = run_together([lambda o=o: o.post(f"/fdrs/{fdr['id']}/release/request").status_code for o in owners])
    assert sorted(statuses) == [201, 409, 409, 409], statuses
    statuses = run_together([lambda a=a: a.post(f"/fdrs/{fdr['id']}/release/approve").status_code for a in approvers])
    assert sorted(statuses) == [200, 409, 409, 409], statuses
    assert len(audit_events(pg_head, "release.requested", "success")) == 1 and len(audit_events(pg_head, "release.approved", "success")) == 1
    with Session(pg_head) as s:
        assert verify_chain(s).ok
