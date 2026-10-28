"""Bulk FDR import: parsing, validation, previews, draft-only creation, duplicates, limits, authorisation."""

import hashlib
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.api.fdr_import as module
from app.api.fdr_import import COLUMNS
from app.models import FDR, FDRStatusHistory
from tests.api_support import API, audit_events, build_client, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_write_api import create, force_status, institution_user, world  # noqa: F401

pytestmark = sqlite_decimal_warning
HEADER = ",".join(COLUMNS)


def line(number="IMP-1", bank="WR-B1", branch="Main", depositor="Depositor", purpose="Deposit", amount="100000.00", rate="7.10", issue="2026-01-15", maturity="2027-01-15"):
    return ",".join([number, bank, branch, depositor, purpose, amount, rate, issue, maturity])


def csv_text(*rows, header=HEADER):
    return "\r\n".join([header, *rows]) + "\r\n"


def post(actor, path, body, *, type_="text/csv", **headers):
    data = body.encode("utf-8") if isinstance(body, str) else body
    return actor.client.post(f"{API}/fdrs/import{path}", content=data, headers={**actor.headers, "Content-Type": type_, **headers})


def preview(actor, body):
    response = post(actor, "/preview", body)
    assert response.status_code == 200, response.text
    return response.json()


def run(actor, body, digest=None):
    data = body.encode("utf-8") if isinstance(body, str) else body
    return post(actor, "", data, **{"X-Import-Digest": digest or hashlib.sha256(data).hexdigest()})


def errors_of(report, n=0):
    return report["rows"][n]["errors"]


def count(engine, **where):
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(FDR).where(*[getattr(FDR, k) == v for k, v in where.items()]))


# ---- template ---------------------------------------------------------------------------------------------------
def test_the_template_has_exactly_the_columns_and_one_example_row_with_a_real_bank_code(engine, world):
    response = institution_user(engine, world).get("/fdrs/import/template")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="fdr-import-template.csv"' and response.headers["cache-control"] == "no-store"
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines[0] == HEADER and len(lines) == 2 and lines[1].split(",")[1] in {"WR-B1", "WR-B2"}
    assert "status" not in lines[0]


def test_the_untouched_template_row_validates(engine, world):
    owner = institution_user(engine, world)
    sample = owner.get("/fdrs/import/template").content.decode("utf-8-sig")
    assert preview(owner, sample)["valid_count"] == 1


# ---- preview: row validation ------------------------------------------------------------------------------------
def test_a_good_file_previews_every_row_as_valid_and_saves_nothing(engine, world):
    owner = institution_user(engine, world)
    body = csv_text(line("A-1"), line("A-2", bank="WR-B2", rate=""), line("A-3", branch="", depositor="", purpose=""))
    report = preview(owner, body)
    assert (report["rows_total"], report["valid_count"], report["invalid_count"]) == (3, 3, 0)
    assert report["digest"] == hashlib.sha256(body.encode()).hexdigest() and report["max_rows"] == 500 and "drafts" in report["note"]
    assert count(engine) == 0


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        (line(number=""), "fdr_number is required"), (line(bank=""), "bank_code is required"), (line(amount=""), "principal_amount is required"),
        (line(issue=""), "issue_date is required"), (line(maturity=""), "maturity_date is required"),
        (line(bank="NOPE"), "'NOPE' is not a known bank code"), (line(amount='"1,500,000.00"'), "plain number"), (line(amount="Rs 500"), "plain number"),
        (line(amount="0"), "must be greater than 0"), (line(amount="-5"), "plain number"), (line(amount="100.123"), "plain number"),
        (line(amount="1234567890123"), "plain number"), (line(rate="101"), "must be at most 100"), (line(rate="abc"), "interest_rate_pct must be a plain number"),
        (line(issue="15/01/2026"), "issue_date must be a date written YYYY-MM-DD"), (line(issue="20260115"), "issue_date must be a date"),
        (line(maturity="2026-13-40"), "maturity_date must be a date"), (line(issue="2027-01-15", maturity="2026-01-15"), "maturity_date must be after issue_date"),
        (line(issue="2026-01-15", maturity="2026-01-15"), "maturity_date must be after issue_date"), (line(depositor="x" * 301), "depositor_name is too long"),
        (line(number="N" * 65), "fdr_number is too long"), (line(branch="bad\x07bell"), "branch_name contains control characters"),
        (line() + ",extra", "more values than the header"),
    ],
)
def test_each_kind_of_invalid_row_is_rejected_with_a_message_that_says_what_to_fix(engine, world, row, fragment):
    report = preview(institution_user(engine, world), csv_text(row))
    assert report["invalid_count"] == 1 and report["valid_count"] == 0
    assert any(fragment in e for e in errors_of(report)), errors_of(report)


def test_valid_and_invalid_rows_are_reported_together_with_their_line_numbers(engine, world):
    report = preview(institution_user(engine, world), csv_text(line("OK-1"), line("BAD-2", bank="NOPE"), line("OK-3")))
    assert [(r["line"], r["valid"]) for r in report["rows"]] == [(2, True), (3, False), (4, True)]
    assert report["rows"][1]["fdr_number"] == "BAD-2"


def test_quoted_fields_with_commas_and_a_bom_and_blank_lines_are_handled(engine, world):
    body = "﻿" + HEADER + '\r\n\r\nQ-1,WR-B1,"Main, Pune","Smith, ""J""",Deposit,100.50,7.1,2026-01-15,2027-01-15\r\n\r\n'
    report = preview(institution_user(engine, world), body)
    assert report["rows_total"] == 1 and report["valid_count"] == 1


def test_a_header_in_any_case_or_order_is_accepted(engine, world):
    header = ",".join(reversed(COLUMNS)).upper()
    row = ",".join(reversed(line("ORD-1").split(",")))
    assert preview(institution_user(engine, world), csv_text(row, header=header))["valid_count"] == 1


# ---- preview: duplicates ----------------------------------------------------------------------------------------
def test_a_repeated_number_in_the_file_rejects_the_later_row_naming_the_first(engine, world):
    report = preview(institution_user(engine, world), csv_text(line("DUP-1"), line("dup- 1".replace(" ", "")), line("DUP-1", bank="WR-B2")))
    assert [r["valid"] for r in report["rows"]] == [True, False, True]  # same number at another bank is a different FDR
    assert "duplicate of line 2 in this file" in errors_of(report, 1)[0]


@pytest.mark.parametrize("status", ["DRAFT", "SUBMITTED", "ACCEPTED"])
def test_a_number_already_in_the_institution_is_rejected_with_its_status(engine, world, status):
    owner = institution_user(engine, world)
    existing = create(owner, world, fdr_number="EXIST-1")
    force_status(engine, existing["id"], status)
    report = preview(owner, csv_text(line("exist-1")))
    assert report["invalid_count"] == 1 and "already exists at this bank in your institution" in errors_of(report)[0]


@pytest.mark.parametrize("status", ["REJECTED", "WITHDRAWN"])
def test_a_rejected_or_withdrawn_record_does_not_block_its_number(engine, world, status):
    owner = institution_user(engine, world)
    force_status(engine, create(owner, world, fdr_number="OLD-1")["id"], status)
    assert preview(owner, csv_text(line("OLD-1")))["valid_count"] == 1


def test_another_institutions_record_is_never_revealed_by_the_preview(engine, world):
    other = institution_user(engine, world, "B")
    create(other, world, fdr_number="THEIRS-1")
    mine = institution_user(engine, world, "A")
    report = preview(mine, csv_text(line("THEIRS-1")))
    assert report["valid_count"] == 1 and errors_of(report) == []  # no hint that the number exists elsewhere


# ---- file-level refusals ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (csv_text(line(), header=HEADER + ",status"), "Unknown column 'status'"), (csv_text(line(), header=HEADER + ",institution_id"), "Unknown column"),
        (csv_text(line(), header=HEADER + ",id"), "Unknown column"), (csv_text(line(), header=HEADER + ",fdr_number"), "more than once"),
        (csv_text(line(), header=HEADER.replace("bank_code,", "")), "Missing column: bank_code"), ("", "empty"), (HEADER + "\r\n", "no rows"), (",,,\r\n", "Unknown column"),
    ],
)
def test_a_file_with_the_wrong_columns_or_no_rows_is_refused_outright(engine, world, body, fragment):
    response = post(institution_user(engine, world), "/preview", body)
    assert response.status_code == 422 and fragment in response.json()["detail"]


def test_a_status_column_is_refused_with_an_explanation_that_imports_make_drafts(engine, world):
    owner = institution_user(engine, world)
    response = post(owner, "/preview", csv_text(line() + ",ACCEPTED", header=HEADER + ",status"))
    assert response.status_code == 422 and "every row becomes a draft" in response.json()["detail"]
    digest = hashlib.sha256(csv_text(line() + ",ACCEPTED", header=HEADER + ",status").encode()).hexdigest()
    assert post(owner, "", csv_text(line() + ",ACCEPTED", header=HEADER + ",status"), **{"X-Import-Digest": digest}).status_code == 422
    assert count(engine) == 0


def test_a_file_that_is_not_utf8_or_contains_binary_data_is_refused(engine, world):
    owner = institution_user(engine, world)
    latin1 = (csv_text(line(depositor="Café")).encode("latin-1"))
    assert post(owner, "/preview", latin1).status_code == 422 and "UTF-8" in post(owner, "/preview", latin1).json()["detail"]
    assert post(owner, "/preview", b"a,b\x00c\r\n1,2\r\n").status_code == 422


def test_an_unterminated_quote_is_refused_as_invalid_csv(engine, world):
    response = post(institution_user(engine, world), "/preview", HEADER + '\r\nA-1,WR-B1,"unterminated,,,,,,,\r\n')
    assert response.status_code in (200, 422)
    if response.status_code == 200:  # some readers treat the rest of the file as one field; the row must then be invalid
        assert response.json()["valid_count"] == 0


def test_unicode_text_in_fields_is_accepted_as_it_is(engine, world):
    owner = institution_user(engine, world)
    report = preview(owner, csv_text(line("UNI-1", depositor="संस्था Pune", branch="Café")))
    assert report["valid_count"] == 1
    result = run(owner, csv_text(line("UNI-1", depositor="संस्था Pune", branch="Café"))).json()
    assert result["created"] == 1
    with Session(engine) as s:
        assert s.scalar(select(FDR.depositor_name).where(FDR.fdr_number == "UNI-1")) == "संस्था Pune"


def test_the_row_limit_is_enforced_and_exactly_the_limit_is_fine(engine, world):
    owner = institution_user(engine, world)
    full = csv_text(*[line(f"R-{i}") for i in range(500)])
    assert preview(owner, full)["rows_total"] == 500
    response = post(owner, "/preview", csv_text(*[line(f"R-{i}") for i in range(501)]))
    assert response.status_code == 413 and "more than 500 rows" in response.json()["detail"]


def test_the_size_limit_is_enforced_for_declared_and_streamed_bodies(engine, world):
    owner = institution_user(engine, world)
    big = csv_text(line(depositor="d" * 250)) + ("x" * 1_000_001)
    assert post(owner, "/preview", big).status_code == 413
    chunks = (b"A" * 100_000 for _ in range(11))  # no Content-Length: counted as it arrives
    streamed = owner.client.post(f"{API}/fdrs/import/preview", content=chunks, headers={**owner.headers, "Content-Type": "text/csv"})
    assert streamed.status_code == 413


@pytest.mark.parametrize("type_", ["application/json", "application/octet-stream", "image/png", "text/html"])
def test_only_csv_content_types_are_accepted(engine, world, type_):
    assert post(institution_user(engine, world), "/preview", csv_text(line()), type_=type_).status_code == 415


def test_excels_csv_content_type_is_accepted(engine, world):
    assert post(institution_user(engine, world), "/preview", csv_text(line()), type_="application/vnd.ms-excel").status_code == 200


# ---- the import itself --------------------------------------------------------------------------------------
def test_valid_rows_become_drafts_of_the_callers_institution_with_history_and_audit(engine, world):
    owner = institution_user(engine, world)
    body = csv_text(line("NEW-1", amount="250000.50"), line("NEW-2", bank="WR-B2", rate=""))
    result = run(owner, body)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    data = result.json()
    assert (data["created"], data["rejected"]) == (2, 0) and [r["outcome"] for r in data["rows"]] == ["created", "created"]
    with Session(engine) as s:
        rows = s.scalars(select(FDR).order_by(FDR.fdr_number)).all()
        assert [r.status for r in rows] == ["DRAFT", "DRAFT"] and {r.institution_id for r in rows} == {world["A"]}
        assert {r.created_by for r in rows} == {owner.user_id} and [r.row_version for r in rows] == [1, 1]
        assert str(rows[0].principal_amount) in ("250000.50", "250000.5") and rows[1].interest_rate_pct is None
        assert [r.fdr_number_normalized for r in rows] == ["NEW-1", "NEW-2"]
        history = s.scalars(select(FDRStatusHistory)).all()
        assert [(h.from_status, h.to_status, h.actor_role) for h in history] == [(None, "DRAFT", "INSTITUTION_USER")] * 2
    assert len(audit_events(engine, "fdr.create", "success")) == 2
    summary = audit_events(engine, "fdr.import", "success")[-1]
    assert summary.details == {"result_count": 2, "filters": ["rejected:0"]} and summary.actor_user_id == owner.user_id
    created = [r["fdr_id"] for r in data["rows"]]
    assert all(owner.get(f"/fdrs/{i}").status_code == 200 for i in created)


def test_an_imported_draft_is_exactly_what_the_create_form_makes(engine, world):
    owner = institution_user(engine, world)
    form = create(owner, world, fdr_number="FORM-1", branch_name="Main", depositor_name="D", purpose="P", principal_amount="100000.00", interest_rate_pct="7.10", issue_date="2026-01-15", maturity_date="2027-01-15")
    run(owner, csv_text(line("CSV-1", branch="Main", depositor="D", purpose="P")))
    imported = next(i for i in owner.get("/fdrs?q=CSV-1").json()["items"])
    for key in ("status", "row_version", "branch_name", "depositor_name", "purpose", "principal_amount", "interest_rate_pct", "issue_date", "maturity_date", "submitted_at", "decided_at"):
        assert imported[key] == form[key], key


def test_partial_files_create_the_valid_rows_and_report_every_rejected_row(engine, world):
    owner = institution_user(engine, world)
    body = csv_text(line("P-1"), line("P-2", bank="NOPE"), line("P-3"), line("P-3"), line("P-5", amount="-1"))
    data = run(owner, body).json()
    assert (data["created"], data["rejected"]) == (2, 3)
    assert [(r["line"], r["outcome"]) for r in data["rows"]] == [(2, "created"), (3, "rejected"), (4, "created"), (5, "rejected"), (6, "rejected")]
    assert all(r["errors"] for r in data["rows"] if r["outcome"] == "rejected") and all(r["fdr_id"] is None for r in data["rows"] if r["outcome"] == "rejected")
    assert count(engine) == 2


def test_importing_the_same_file_twice_creates_nothing_the_second_time(engine, world):
    owner = institution_user(engine, world)
    body = csv_text(line("TWICE-1"), line("TWICE-2"))
    assert run(owner, body).json()["created"] == 2
    again = run(owner, body).json()
    assert (again["created"], again["rejected"]) == (0, 2) and "already exists" in again["rows"][0]["errors"][0]
    assert count(engine) == 2


def test_the_digest_must_match_the_file_that_was_previewed(engine, world):
    owner = institution_user(engine, world)
    previewed = csv_text(line("DIG-1"))
    digest = preview(owner, previewed)["digest"]
    swapped = csv_text(line("DIG-1"), line("DIG-EXTRA"))
    response = run(owner, swapped, digest=digest)
    assert response.status_code == 409 and "Preview it again" in response.json()["detail"] and count(engine) == 0
    assert post(owner, "", swapped).status_code == 409  # no digest at all
    assert post(owner, "", swapped, **{"X-Import-Digest": "not-a-digest"}).status_code == 409
    assert run(owner, previewed, digest=digest).json()["created"] == 1


def test_confirming_revalidates_so_a_row_that_became_invalid_since_the_preview_is_rejected(engine, world):
    owner = institution_user(engine, world)
    body = csv_text(line("LATE-1"))
    assert preview(owner, body)["valid_count"] == 1
    create(owner, world, fdr_number="LATE-1")  # someone creates it between preview and confirmation
    data = run(owner, body).json()
    assert (data["created"], data["rejected"]) == (0, 1) and "already exists" in data["rows"][0]["errors"][0]


def test_a_database_error_on_one_row_leaves_the_others_created(engine, world, monkeypatch):
    real = module.add_draft_fdr

    def flaky(db, ctx, auth, body, institution_id, normalized):
        if normalized == "BOOM-1":
            raise IntegrityError("insert", {}, Exception("constraint"))
        return real(db, ctx, auth, body, institution_id, normalized)

    monkeypatch.setattr(module, "add_draft_fdr", flaky)
    owner = institution_user(engine, world)
    data = run(owner, csv_text(line("OK-A"), line("BOOM-1"), line("OK-B"))).json()
    assert [r["outcome"] for r in data["rows"]] == ["created", "rejected", "created"] and data["rows"][1]["errors"] == ["the database refused this row"]
    assert count(engine) == 2


def test_a_file_cannot_target_another_institution_or_set_privileged_fields(engine, world):
    owner = institution_user(engine, world, "A")
    for column in ("institution_id", "status", "created_by", "row_version", "submitted_at", "decided_at", "id"):
        body = csv_text(line("PRIV-1") + ",x", header=HEADER + f",{column}")
        assert post(owner, "/preview", body).status_code == 422, column
    assert count(engine) == 0


# ---- authorisation ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"])
def test_only_institution_users_may_use_every_import_endpoint(engine, world, role):
    actor = sign_in(engine, role=role)
    body = csv_text(line("NOPE-1"))
    assert actor.get("/fdrs/import/template").status_code == 403
    assert post(actor, "/preview", body).status_code == 403 and post(actor, "/errors", body).status_code == 403
    assert run(actor, body).status_code == 403
    assert count(engine) == 0 and audit_events(engine, "authz.denied", "denied")


def test_import_needs_a_session_the_csrf_token_and_a_permanent_password(engine, world):
    owner = institution_user(engine, world)
    body = csv_text(line("AUTH-1"))
    assert build_client(engine).get(API + "/fdrs/import/template").status_code == 401
    assert build_client(engine).post(f"{API}/fdrs/import/preview", content=body, headers={"Content-Type": "text/csv"}).status_code in (401, 403)
    no_csrf = owner.client.post(f"{API}/fdrs/import/preview", content=body, headers={"Content-Type": "text/csv"})
    assert no_csrf.status_code == 403
    temporary = sign_in(engine, role="INSTITUTION_USER", institution_id=world["A"], must_change_password=True)
    assert post(temporary, "/preview", body).status_code == 403 and count(engine) == 0


# ---- the error report -----------------------------------------------------------------------------------------
def test_the_error_report_lists_only_the_rejected_rows_and_neutralises_formulas(engine, world):
    owner = institution_user(engine, world)
    body = csv_text(line("GOOD-1"), line("=HYPERLINK(\"http://evil\")", bank="NOPE"), line("+SUM(1)", amount="x"), line("@cmd", bank="NOPE"))
    response = post(owner, "/errors", body)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="fdr-import-rejected-rows.csv"' and response.headers["cache-control"] == "no-store"
    text = response.content.decode("utf-8-sig")
    rows = [r.split(",")[0:2] for r in text.splitlines()[1:]]
    assert text.splitlines()[0] == "line,fdr_number,bank_code,problems" and len(rows) == 3 and "GOOD-1" not in text
    assert "'=HYPERLINK" in text and "'+SUM(1)" in text and "'@cmd" in text
    assert not any(cell.startswith(("=", "+", "@")) for row in text.splitlines()[1:] for cell in row.split(","))


def test_the_error_report_is_just_a_header_when_every_row_is_valid(engine, world):
    text = post(institution_user(engine, world), "/errors", csv_text(line("FINE-1"))).content.decode("utf-8-sig")
    assert text.strip() == "line,fdr_number,bank_code,problems"


def test_nothing_is_written_by_a_preview_or_an_error_report(engine, world):
    owner = institution_user(engine, world)
    before = len(audit_events(engine, "fdr.create")), len(audit_events(engine, "fdr.import"))
    body = csv_text(line("RO-1"), line("RO-2", bank="NOPE"))
    preview(owner, body)
    post(owner, "/errors", body)
    assert count(engine) == 0 and (len(audit_events(engine, "fdr.create")), len(audit_events(engine, "fdr.import"))) == before


def test_imported_drafts_follow_the_normal_visibility_rules(engine, world):
    owner = institution_user(engine, world, "A")
    other = institution_user(engine, world, "B")
    created = run(owner, csv_text(line("VIS-1"))).json()["rows"][0]["fdr_id"]
    assert owner.get(f"/fdrs/{created}").status_code == 200 and other.get(f"/fdrs/{created}").status_code == 404
    assert sign_in(engine, role="AICTE_REVIEWER").get(f"/fdrs/{created}").status_code == 404  # a draft is private to the institution
    assert uuid.UUID(created)
