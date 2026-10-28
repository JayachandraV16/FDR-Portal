"""PDF reports: they open, say what they cover, match the data, respect scope, and fail safely."""

import re
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.api.report_pdfs as module
from app.insights.derive import today
from app.insights.pdf import ReportSpec, Section, inr, render
from app.models import Institution
from app.timeutil import utcnow
from tests.api_support import API, audit_events, build_client, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_review_api import decide, submitted
from tests.test_fdr_write_api import create, force_status, institution_user, world  # noqa: F401

pytestmark = sqlite_decimal_warning

KINDS = ["summary", "institutions", "maturity", "pending-review", "exceptions", "audit"]


def on(days):
    return (today() + timedelta(days=days)).isoformat()


def pdf_text(response) -> str:
    assert response.status_code == 200, response.text
    reader = PdfReader(BytesIO(response.content))
    return "\n".join(page.extract_text() for page in reader.pages)


def pages(response) -> list[str]:
    return [p.extract_text() for p in PdfReader(BytesIO(response.content)).pages]


def flat(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def fdr(owner, world, number, days, amount="100000.00", **extra):
    return create(owner, world, fdr_number=number, issue_date=on(-400), maturity_date=on(days), principal_amount=amount, **extra)


def live(engine, world, owner, number, days, amount="100000.00"):
    return submitted(engine, world, owner, fdr_number=number, issue_date=on(-400), maturity_date=on(days), principal_amount=amount)[1]


# ---- the renderer ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("amount", "expected"),
    [("0", "INR 0.00"), ("12.5", "INR 12.50"), ("999.99", "INR 999.99"), ("1000.00", "INR 1,000.00"), ("123456.78", "INR 1,23,456.78"),
     ("12345678.90", "INR 1,23,45,678.90"), ("-5000.00", "INR -5,000.00"), (Decimal("2500000"), "INR 25,00,000.00")],
)
def test_amounts_use_indian_grouping_and_an_ascii_currency_label(amount, expected):
    assert inr(amount) == expected


def test_a_pdf_has_the_branding_scope_filters_summary_tables_notes_and_page_numbers():
    spec = ReportSpec(
        title="Test report", generated_at=utcnow(), scope="Your institution's FDRs.", filters=["Institution: Demo"],
        summary=[("FDRs", "3"), ("Principal", inr("300.00"))],
        sections=[Section("Things", header=["Name", "Amount"], rows=[["Alpha", "1"], ["Beta", "2"]], right=(1,), total_row=["Total", "3"])],
    )
    data = render(spec)
    assert data.startswith(b"%PDF-")
    text_ = flat("\n".join(p.extract_text() for p in PdfReader(BytesIO(data)).pages))
    for expected in ["FDR & Security Deposit Portal", "Test report", "Generated", "Your institution's FDRs.", "Institution: Demo", "Summary", "INR 300.00",
                     "Things", "Alpha", "Beta", "Total", "Page 1 of 1", "not digitally signed", "not verified with any bank"]:
        assert expected in text_, expected
    meta = PdfReader(BytesIO(data)).metadata
    assert meta.title == "Test report" and meta.author == "FDR & Security Deposit Portal"


def test_a_long_table_flows_over_pages_and_repeats_its_header_on_each():
    rows = [[f"FDR-{i:04d}", "Institution of Long Name", f"{i:,}"] for i in range(300)]
    data = render(ReportSpec(title="Long", generated_at=utcnow(), scope="s", sections=[Section("Rows", header=["FDR number", "Institution", "Amount"], rows=rows, right=(2,))]))
    reader = PdfReader(BytesIO(data))
    assert len(reader.pages) > 3
    texts = [p.extract_text() for p in reader.pages]
    assert all("FDR number" in t for t in texts)  # the header row repeats
    assert all(f"Page {n} of {len(texts)}" in t for n, t in enumerate(texts, 1))
    assert "FDR-0000" in texts[0] and "FDR-0299" in texts[-1]


def test_text_is_escaped_so_markup_in_data_is_printed_not_interpreted():
    data = render(ReportSpec(title="Escapes", generated_at=utcnow(), scope="s", sections=[Section("S", header=["A"], rows=[["<b>bold</b> & <i>x</i> &amp; <img src=x>"]])]))
    out = flat("\n".join(p.extract_text() for p in PdfReader(BytesIO(data)).pages))
    assert "<b>bold</b> & <i>x</i> &amp; <img src=x>" in out


def test_characters_the_fonts_cannot_show_are_replaced_and_the_document_says_so():
    data = render(ReportSpec(title="Unicode", generated_at=utcnow(), scope="s", sections=[Section("S", header=["Name"], rows=[["संस्था Pune"], ["Café"]])]))
    out = flat("\n".join(p.extract_text() for p in PdfReader(BytesIO(data)).pages))
    assert "Pune" in out and "Café" in out and "संस्था" not in out
    assert re.search(r"\d+ characters in the data \(for example Devanagari text\) cannot be shown in this PDF and were replaced by '\?'", out)


def test_no_note_about_replacements_appears_when_nothing_was_replaced():
    data = render(ReportSpec(title="Plain", generated_at=utcnow(), scope="s", sections=[Section("S", header=["Name"], rows=[["Pune"]])]))
    assert "replaced by" not in "\n".join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)


# ---- the endpoints -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
def test_every_report_is_a_downloadable_pdf_that_opens_and_is_not_cached(engine, world, kind):
    actor = sign_in(engine, role="ADMIN") if kind == "audit" else sign_in(engine, role="AICTE_APPROVER")
    response = actor.get(f"/reports/pdf/{kind}")
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-") and b"%%EOF" in response.content[-32:]
    assert response.headers["content-disposition"] == f'attachment; filename="fdr-{kind}-{today().isoformat()}.pdf"'
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
    assert len(PdfReader(BytesIO(response.content)).pages) >= 1


def test_the_summary_pdf_matches_the_json_report_exactly(engine, world):
    owner = institution_user(engine, world)
    live(engine, world, owner, "SUM-1", 10, "250000.50")
    live(engine, world, owner, "SUM-2", 200, "1000000.00")
    fdr(owner, world, "SUM-DRAFT", 5, "7.00")
    data = owner.get("/reports/summary").json()
    out = flat(pdf_text(owner.get("/reports/pdf/summary")))
    assert f"{data['fdr_total']:,}" in out and inr(data["principal_live_total"]) in out and inr("1250000.50") in out
    for row in data["by_status"]:
        assert row["label"] in out and inr(row["principal"]) in out
    assert data["scope"] in out and "Page 1 of" in out


def test_the_maturity_pdf_lists_the_real_records_with_exact_totals_and_prints_its_filters(engine, world):
    owner = institution_user(engine, world)
    live(engine, world, owner, "MAT-PAST", -3, "100000.00")
    live(engine, world, owner, "MAT-SOON", 20, "200000.25")
    live(engine, world, owner, "MAT-LATE", 80, "300000.00")
    live(engine, world, owner, "MAT-FAR", 400, "999999.00")
    fdr(owner, world, "MAT-DRAFT", 5, "5.00")
    out = flat(pdf_text(owner.get("/reports/pdf/maturity?days=90")))
    for listed in ("MAT-PAST", "MAT-SOON", "MAT-LATE"):
        assert listed in out
    assert "MAT-FAR" not in out and "MAT-DRAFT" not in out  # beyond the window; and a draft is not live
    assert inr("600000.25") in out and "within 90 days" in out and "Already past maturity" in out
    assert "MAT-LATE" not in flat(pdf_text(owner.get("/reports/pdf/maturity?days=30"))) and "within 30 days" in flat(pdf_text(owner.get("/reports/pdf/maturity?days=30")))
    assert out.index("MAT-PAST") < out.index("MAT-SOON") < out.index("MAT-LATE")  # soonest first


def test_the_institution_filter_narrows_a_reviewers_pdf_and_is_printed(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    live(engine, world, a, "FILT-A", 10)
    live(engine, world, b, "FILT-B", 10)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    both = flat(pdf_text(reviewer.get("/reports/pdf/maturity")))
    assert "FILT-A" in both and "FILT-B" in both
    only_a = flat(pdf_text(reviewer.get(f"/reports/pdf/maturity?institution_id={world['A']}")))
    assert "FILT-A" in only_a and "FILT-B" not in only_a and "Institution: Write Institution A" in only_a


def test_scope_comes_from_the_signed_in_user_never_from_a_filter(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    live(engine, world, a, "OWN-A", 10)
    live(engine, world, b, "OTHER-B", 10)
    out = flat(pdf_text(a.get(f"/reports/pdf/maturity?institution_id={world['B']}")))
    assert "OWN-A" in out and "OTHER-B" not in out and "Write Institution B" not in out
    assert "OTHER-B" not in flat(pdf_text(a.get("/reports/pdf/summary")))


def test_reviewers_never_get_drafts_in_a_pdf(engine, world):
    owner = institution_user(engine, world)
    live(engine, world, owner, "VIS-LIVE", 10)
    fdr(owner, world, "VIS-DRAFT", 10)
    out = flat(pdf_text(sign_in(engine, role="AICTE_REVIEWER").get("/reports/pdf/maturity")))
    assert "VIS-LIVE" in out and "VIS-DRAFT" not in out


def test_the_pending_review_pdf_lists_submitted_fdrs_oldest_first_with_waiting_time(engine, world):
    owner = institution_user(engine, world)
    first = live(engine, world, owner, "PEND-1", 100)
    live(engine, world, owner, "PEND-2", 100)
    done = live(engine, world, owner, "PEND-DONE", 100)
    sign_in(engine, role="AICTE_APPROVER")
    assert decide(sign_in(engine, role="AICTE_APPROVER"), "accept", done).status_code == 200
    out = flat(pdf_text(sign_in(engine, role="AICTE_REVIEWER").get("/reports/pdf/pending-review")))
    assert "PEND-1" in out and "PEND-2" in out and "PEND-DONE" not in out and "Days waiting" in out
    assert out.index("PEND-1") < out.index("PEND-2") and "Assignment to individual reviewers is not tracked" in out and first["fdr_number"] == "PEND-1"
    assert inr("200000.00") in out


def test_the_exceptions_pdf_lists_derived_exceptions_with_the_honest_caveats(engine, world):
    owner = institution_user(engine, world)
    live(engine, world, owner, "EXC-1", -10)  # past maturity with no approved release
    out = flat(pdf_text(sign_in(engine, role="AICTE_REVIEWER").get("/reports/pdf/exceptions")))
    assert "Exceptions and discrepancies report" in out and "EXC-1" in out and "not a finding of fraud" in out and "nothing here is bank-verified" in out


def test_the_institution_pdf_totals_match_the_rows(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    live(engine, world, a, "IN-A1", 10, "100.00")
    live(engine, world, a, "IN-A2", 10, "200.00")
    live(engine, world, b, "IN-B1", 10, "50.00")
    out = flat(pdf_text(sign_in(engine, role="AICTE_REVIEWER").get("/reports/pdf/institutions")))
    assert "Write Institution A" in out and "Write Institution B" in out and inr("350.00") in out
    assert "Write Institution B" not in flat(pdf_text(a.get("/reports/pdf/institutions")))


def test_the_audit_pdf_summarises_real_events_and_reports_the_chain_check(engine, world):
    sign_in(engine, role="AICTE_REVIEWER")  # produces auth.login events
    admin = sign_in(engine, role="ADMIN")
    out = flat(pdf_text(admin.get("/reports/pdf/audit?days=7")))
    assert "Audit trail report" in out and "auth.login" in out and "Intact" in out and "Hash chain" in out and "last 7 days" in out
    assert "@example.invalid" not in out and "127.0.0.1" not in out and "testclient" not in out.lower()  # no addresses or personal data
    assert flat(pdf_text(sign_in(engine, role="AUDITOR").get("/reports/pdf/audit"))).count("Audit trail report") >= 1


def test_the_audit_pdf_reports_a_broken_chain(engine, world):
    if engine.dialect.name != "sqlite":
        pytest.skip("the audit table is append-only on PostgreSQL, so it cannot be tampered with")
    admin = sign_in(engine, role="ADMIN")
    with Session(engine) as s:
        s.execute(text("UPDATE audit_logs SET action = 'tampered.action' WHERE seq = (SELECT MIN(seq) FROM audit_logs)"))
        s.commit()
    out = flat(pdf_text(admin.get("/reports/pdf/audit")))
    assert "BROKEN" in out


# ---- authorisation -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "role", "status"),
    [("pending-review", "INSTITUTION_USER", 403), ("exceptions", "INSTITUTION_USER", 403), ("audit", "INSTITUTION_USER", 403),
     ("audit", "AICTE_REVIEWER", 403), ("audit", "AICTE_APPROVER", 403), ("pending-review", "ADMIN", 403), ("pending-review", "AUDITOR", 403),
     ("exceptions", "AUDITOR", 403), ("audit", "ADMIN", 200), ("audit", "AUDITOR", 200), ("pending-review", "AICTE_APPROVER", 200),
     ("summary", "INSTITUTION_USER", 200), ("maturity", "AUDITOR", 200), ("institutions", "ADMIN", 200)],
)
def test_each_report_is_limited_to_the_roles_that_may_see_its_data(engine, world, kind, role, status):
    actor = institution_user(engine, world) if role == "INSTITUTION_USER" else sign_in(engine, role=role)
    assert actor.get(f"/reports/pdf/{kind}").status_code == status


@pytest.mark.parametrize("kind", KINDS)
def test_a_pdf_needs_a_session_and_a_password_that_is_not_temporary(engine, world, kind):
    assert build_client(engine).get(API + f"/reports/pdf/{kind}").status_code == 401
    assert sign_in(engine, role="ADMIN", must_change_password=True).get(f"/reports/pdf/{kind}").status_code == 403


def test_a_denied_export_is_recorded_and_exports_write_the_audit_trail(engine, world):
    owner = institution_user(engine, world)
    assert owner.get("/reports/pdf/exceptions").status_code == 403
    assert audit_events(engine, "authz.denied", "denied")
    live(engine, world, owner, "AUD-1", 10)
    assert owner.get("/reports/pdf/maturity").status_code == 200
    event = audit_events(engine, "report.export", "success")[-1]
    assert event.details == {"filters": ["maturity.pdf"], "result_count": 1}


@pytest.mark.parametrize(("path", "status"), [("maturity?days=45", 422), ("maturity?days=0", 422), ("maturity?institution_id=not-a-uuid", 422), ("audit?days=1", 422)])
def test_filters_are_validated(engine, world, path, status):
    actor = sign_in(engine, role="ADMIN")
    assert actor.get(f"/reports/pdf/{path}").status_code == status


def test_a_failure_gives_a_plain_message_and_no_internal_detail_and_no_success_event(engine, world, monkeypatch):
    def broken(spec):
        raise RuntimeError("secret internals /srv/path")

    monkeypatch.setattr(module, "render", broken)
    actor = sign_in(engine, role="AICTE_REVIEWER", raise_server_exceptions=False)
    before = len(audit_events(engine, "report.export", "success"))
    response = actor.get("/reports/pdf/maturity")
    assert response.status_code == 500 and response.json() == {"detail": "The PDF could not be generated. Please try again."}
    assert "secret" not in response.text and len(audit_events(engine, "report.export", "success")) == before


def test_an_oversized_table_is_cut_with_a_note_but_the_totals_cover_every_record(engine, world, monkeypatch):
    monkeypatch.setattr(module, "MAX_PDF_ROWS", 2)
    owner = institution_user(engine, world)
    for i in range(4):
        live(engine, world, owner, f"CUT-{i}", 10 + i, "1000.00")
    out = flat(pdf_text(owner.get("/reports/pdf/maturity")))
    assert "first 2 of 4 matching FDRs" in out and inr("4000.00") in out
    assert "CUT-0" in out and "CUT-1" in out and "CUT-3" not in out


def test_untrusted_names_and_numbers_in_data_cannot_break_or_style_the_document(engine, world):
    with Session(engine) as s:
        s.get(Institution, world["A"]).name = "Markup <b>Inst</b> & संस्था"
        s.commit()
    owner = institution_user(engine, world)
    live(engine, world, owner, "<i>X</i>-9", 10)
    out = flat(pdf_text(owner.get("/reports/pdf/maturity")))
    assert "<i>X</i>-9" in out and "Markup <b>Inst</b> &" in out and "replaced by" in out
