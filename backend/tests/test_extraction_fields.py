"""Field parsing from text and comparison with submitted values. Pure functions, synthetic text only."""

import time
from decimal import Decimal

import pytest

from app.extraction.compare import (
    AMBIGUOUS, BANK_ALIASES, MATCH, MISMATCH, NOT_COMPARABLE, NOT_FOUND, Submitted, compare_field, fold_bank,
)
from app.extraction.fields import (
    ExtractedField, extract_fields, parse_amount, parse_date, parse_identifier, parse_rate,
)
from app.extraction.types import PageText, TextSource

RECEIPT = """State Bank of India
Fixed Deposit Receipt
Branch: Pune Camp
FDR No: 12345678901
Depositor: PICT Test Institute
Principal Amount: Rs. 15,00,000.00
Date of Deposit: 15 Jan 2026
Maturity Date: 15 Jan 2027
Rate of Interest: 7.10 % p.a."""


def fields(text: str, source: str = TextSource.EMBEDDED_TEXT, page: int = 1) -> dict[str, ExtractedField]:
    return {f.name: f for f in extract_fields([PageText(page, source, text)])}


def fields_pages(*texts: str) -> dict[str, ExtractedField]:
    return {f.name: f for f in extract_fields([PageText(i + 1, TextSource.EMBEDDED_TEXT, t) for i, t in enumerate(texts)])}


# ---- extraction ---------------------------------------------------------------------------------
def test_a_complete_receipt_yields_every_field_with_page_and_confidence():
    f = fields(RECEIPT)
    assert {k: v.value for k, v in f.items()} == {
        "bank_name": "State Bank of India", "branch_name": "Pune Camp", "fdr_number": "12345678901",
        "depositor_name": "PICT Test Institute", "principal_amount": "1500000.00", "currency": "INR",
        "issue_date": "2026-01-15", "maturity_date": "2027-01-15", "interest_rate_pct": "7.10",
    }
    assert all(v.status == "EXTRACTED" and v.page == 1 for v in f.values())
    assert {k: v.confidence for k, v in f.items() if k != "bank_name"} == {k: "HIGH" for k in f if k != "bank_name"}
    assert f["bank_name"].confidence == "MEDIUM"  # a header line, not a labelled value


@pytest.mark.parametrize(
    ("remove", "field"),
    [("Branch:", "branch_name"), ("FDR No:", "fdr_number"), ("Depositor:", "depositor_name"),
     ("Principal Amount:", "principal_amount"), ("Date of Deposit:", "issue_date"),
     ("Maturity Date:", "maturity_date"), ("Rate of Interest:", "interest_rate_pct"), ("State Bank", "bank_name")],
)
def test_a_missing_field_is_not_found_and_nothing_is_invented(remove, field):
    text = "\n".join(line for line in RECEIPT.splitlines() if not line.startswith(remove))
    result = fields(text)[field]
    assert result.status == "NOT_FOUND" and result.value is None and result.confidence is None and result.page is None
    others = [v for k, v in fields(text).items() if k not in (field, "currency")]
    assert all(v.status == "EXTRACTED" for v in others if not (field == "principal_amount" and v.name == "currency"))


def test_no_labels_at_all_means_everything_is_not_found():
    f = fields("Some unrelated paragraph about a trip to Pune in 2026 costing 15,000 rupees.")
    assert {v.status for v in f.values()} == {"NOT_FOUND"}


def test_the_page_of_each_value_is_reported():
    f = fields_pages("State Bank of India\nBranch: Pune Camp", "FDR No: 55-AB-1\nMaturity Date: 15 Jan 2027")
    assert (f["branch_name"].page, f["fdr_number"].page, f["maturity_date"].page) == (1, 2, 2)


def test_two_different_numbers_are_ambiguous_and_neither_is_chosen():
    f = fields("FDR No: 111222333\nReceipt No: 444555666")["fdr_number"]
    assert f.status == "AMBIGUOUS" and f.value is None and f.note == "multiple_values"
    assert {c["value"] for c in f.candidates} == {"111222333", "444555666"}


def test_the_same_value_repeated_is_one_value_with_the_best_confidence():
    f = fields("FDR No: 111222333\nReceipt Number\n111222333")["fdr_number"]
    assert f.status == "EXTRACTED" and f.value == "111222333" and f.confidence == "HIGH"


def test_identifiers_are_kept_exactly_as_printed():
    assert fields("FDR No: ab-12/x9")["fdr_number"].value == "ab-12/x9"  # no case folding
    assert fields("FDR No: 0012345")["fdr_number"].value == "0012345"  # no stripping of zeros
    assert parse_identifier("ABC") is None and parse_identifier("12") is None  # needs a digit and length


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [("Rs. 15,00,000.00", "1500000.00", "INR"), ("INR 1,500,000", "1500000", "INR"), ("₹ 1500000.5", "1500000.5", "INR"),
     ("Rs.15,00,000/-", "1500000", "INR"), ("USD 25,000.00", "25000.00", "USD"), ("1500000", "1500000", None),
     ("€ 2,000", "2000", "EUR")],
)
def test_amounts_parse_exactly(text, amount, currency):
    parsed = parse_amount(text)
    assert parsed == (Decimal(amount), currency)
    assert isinstance(parsed[0], Decimal)


@pytest.mark.parametrize("text", ["Rs. 1,50,0000", "1.500.000,00", "12.345", "Rs. 1,5,00,000", "abc", "", "Rs. ,500", "1,5000"])
def test_amounts_that_cannot_be_read_unambiguously_are_refused(text):
    assert parse_amount(text) is None


def test_an_unreadable_amount_is_ambiguous_not_guessed():
    f = fields("Principal Amount: Rs. 1,50,0000")["principal_amount"]
    assert f.status == "AMBIGUOUS" and f.value is None and f.note == "unreadable_amount"


def test_the_maturity_amount_is_never_taken_for_the_principal():
    f = fields("Maturity Amount: Rs. 1,600,000.00\nDeposit Amount: Rs. 1,500,000.00")
    assert f["principal_amount"].value == "1500000.00"
    assert fields("Maturity Deposit Amount: Rs. 1,600,000.00")["principal_amount"].status == "NOT_FOUND"


def test_the_currency_is_only_reported_when_the_document_states_one():
    assert fields("Principal Amount: 1500000")["currency"].status == "NOT_FOUND"
    usd = fields("Principal Amount: USD 1,500")
    assert usd["currency"].value == "USD" and usd["principal_amount"].currency == "USD"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2027-01-15", {"2027-01-15"}), ("15 Jan 2027", {"2027-01-15"}), ("15-Jan-2027", {"2027-01-15"}),
     ("15th January, 2027", {"2027-01-15"}), ("Jan 15, 2027", {"2027-01-15"}), ("27/10/2026", {"2026-10-27"}),
     ("10/27/2026", {"2026-10-27"}), ("05/05/2026", {"2026-05-05"}), ("3 Sept 2026", {"2026-09-03"}),
     ("03/04/2026", {"2026-04-03", "2026-03-04"})],
)
def test_dates_parse_explicitly_and_numeric_order_is_never_guessed(text, expected):
    assert {d.isoformat() for d in parse_date(text)} == expected


@pytest.mark.parametrize("text", ["31/02/2026", "2026-13-01", "15/01/27", "15 Foo 2027", "yesterday", "", "00/00/2026"])
def test_impossible_or_unparseable_dates_are_refused(text):
    assert parse_date(text) is None


def test_an_ambiguous_numeric_date_is_ambiguous_with_both_readings_listed():
    f = fields("Maturity Date: 03/04/2027")["maturity_date"]
    assert f.status == "AMBIGUOUS" and f.value is None and f.note == "date_order_ambiguous"
    assert sorted(c["value"] for c in f.candidates) == ["2027-03-04", "2027-04-03"]


def test_two_different_maturity_dates_are_ambiguous():
    f = fields("Maturity Date: 15 Jan 2027\nDate of Maturity: 16 Jan 2027")["maturity_date"]
    assert f.status == "AMBIGUOUS" and f.note == "multiple_values"


def test_the_issue_date_label_does_not_pick_up_the_maturity_date():
    f = fields("Maturity Date: 15 Jan 2027")
    assert f["issue_date"].status == "NOT_FOUND" and f["maturity_date"].value == "2027-01-15"


def test_an_invalid_labelled_date_is_ambiguous_not_missing():
    f = fields("Maturity Date: 31/02/2027")["maturity_date"]
    assert f.status == "AMBIGUOUS" and f.note == "unreadable_date" and f.candidates == []


@pytest.mark.parametrize(("text", "rate"), [("7.10 %", "7.10"), ("7.1% p.a.", "7.1"), ("8 per cent", "8"), ("6.50 p.a.", "6.50")])
def test_rates_need_an_explicit_marker(text, rate):
    assert parse_rate(text) == Decimal(rate)


@pytest.mark.parametrize("text", ["7.10", "abc %", "150 %", "Rs. 100", ""])
def test_rates_without_a_marker_or_out_of_range_are_not_read(text):
    assert parse_rate(text) is None


def test_an_interest_line_without_a_rate_is_not_a_rate():
    assert fields("Interest payable at maturity: Rs. 100,000")["interest_rate_pct"].status == "NOT_FOUND"


def test_a_value_on_the_next_line_is_medium_confidence():
    f = fields("FDR No\n55-AB-1\nBranch\nPune Camp")
    assert (f["fdr_number"].value, f["fdr_number"].confidence) == ("55-AB-1", "MEDIUM")
    assert f["branch_name"].value == "Pune Camp"


def test_a_label_in_the_middle_of_a_sentence_does_not_claim_the_next_line():
    f = fields("Issued by the Pune Camp Branch\nFDR No: 12345678901")
    assert f["branch_name"].status == "NOT_FOUND"


def test_values_do_not_run_into_the_next_label_on_the_same_line():
    f = fields("Depositor: PICT Test Institute Principal Amount: Rs. 100,000.00 Branch: Pune Camp")
    assert f["depositor_name"].value == "PICT Test Institute"
    assert f["principal_amount"].value == "100000.00" and f["branch_name"].value == "Pune Camp"


def test_a_label_followed_by_another_label_has_no_value():
    f = fields("Depositor:\nPrincipal Amount: Rs. 100,000.00")
    assert f["depositor_name"].status == "NOT_FOUND"


def test_branch_code_is_not_a_branch_name():
    assert fields("Branch Code: 04123")["branch_name"].status == "NOT_FOUND"


def test_header_lines_that_merely_contain_the_word_bank_are_not_bank_names():
    f = fields("Fixed Deposit Receipt - Bank Copy\nBranch: Pune Camp")
    assert f["bank_name"].status == "NOT_FOUND"


def test_a_labelled_bank_agrees_with_its_header_and_a_conflicting_header_makes_it_ambiguous():
    same = fields("HDFC Bank Limited\nBank Name: HDFC Bank Limited")["bank_name"]
    assert same.status == "EXTRACTED" and same.confidence == "HIGH"
    clash = fields("Canara Bank\nBank Name: Union Bank of India")["bank_name"]
    assert clash.status == "AMBIGUOUS"


def test_ocr_text_lowers_confidence_one_level():
    f = fields(RECEIPT, source=TextSource.OCR)
    assert f["fdr_number"].confidence == "MEDIUM" and f["fdr_number"].source == "OCR"
    assert f["bank_name"].confidence == "LOW"
    nxt = fields("FDR No\n55-AB-1", source=TextSource.OCR)["fdr_number"]
    assert nxt.confidence == "LOW"


def test_hostile_text_is_processed_quickly():
    started = time.monotonic()
    fields("FDR No: " + "1" * 60000 + "\n" + "Depositor: " + "a " * 30000 + "\n" + "Rs. " * 20000)
    fields(("Branch " * 8000) + "\n" + ("Date of Deposit: 1/1/" * 4000))
    assert time.monotonic() - started < 5


# ---- comparison -----------------------------------------------------------------------------------
SUBMITTED = Submitted(
    bank_name="State Bank of India", bank_code="SBI", fdr_number="12345678901", branch_name="Pune Camp",
    depositor_name="PICT Test Institute", principal_amount="1500000.00", interest_rate_pct="7.10",
    issue_date="2026-01-15", maturity_date="2027-01-15",
)


def cmp(name: str, extracted: ExtractedField, submitted: Submitted = SUBMITTED):
    return compare_field(extracted, submitted)


def one(name, status="EXTRACTED", value=None, **kw) -> ExtractedField:
    return ExtractedField(name, status, value, **kw)


def test_the_complete_matching_receipt_matches_everywhere():
    outcomes = {f.name: compare_field(f, SUBMITTED).outcome for f in extract_fields([PageText(1, "EMBEDDED_TEXT", RECEIPT)])}
    assert set(outcomes.values()) == {MATCH}, outcomes


@pytest.mark.parametrize("name", ["bank_name", "branch_name", "fdr_number", "depositor_name", "principal_amount",
                                  "currency", "issue_date", "maturity_date", "interest_rate_pct"])
def test_a_missing_value_is_never_a_match_even_when_nothing_was_submitted_either(name):
    blank = Submitted(*([None] * 9))
    assert compare_field(one(name, "NOT_FOUND"), SUBMITTED).outcome == NOT_FOUND
    assert compare_field(one(name, "NOT_FOUND"), blank).outcome == NOT_FOUND


@pytest.mark.parametrize("name", ["bank_name", "branch_name", "fdr_number", "depositor_name", "principal_amount",
                                  "issue_date", "maturity_date", "interest_rate_pct"])
def test_an_ambiguous_value_is_never_a_match(name):
    result = compare_field(one(name, "AMBIGUOUS", candidates=[{"value": "x", "page": 1}], note="multiple_values"), SUBMITTED)
    assert result.outcome == AMBIGUOUS


def test_nothing_submitted_means_not_comparable():
    blank = Submitted(*([None] * 9))
    assert compare_field(one("branch_name", value="Pune Camp"), blank).outcome == NOT_COMPARABLE
    assert compare_field(one("interest_rate_pct", value="7.10"), blank).outcome == NOT_COMPARABLE
    assert compare_field(one("principal_amount", value="1.00"), blank).outcome == NOT_COMPARABLE


@pytest.mark.parametrize(
    ("document", "submitted", "outcome"),
    [("1500000.00", "1500000.00", MATCH), ("1500000", "1500000.00", MATCH), ("1500000.10", "1500000.1", MATCH),
     ("1500000.01", "1500000.00", MISMATCH), ("150000.00", "1500000.00", MISMATCH), ("0.10", "0.1", MATCH)],
)
def test_amounts_compare_with_exact_decimals(document, submitted, outcome):
    sub = Submitted(None, None, None, None, None, submitted, None, None, None)
    assert compare_field(one("principal_amount", value=document, currency="INR"), sub).outcome == outcome


def test_a_foreign_currency_is_not_converted_and_not_compared():
    sub = Submitted(None, None, None, None, None, "1500000.00", None, None, None)
    assert compare_field(one("principal_amount", value="1500000.00", currency="USD"), sub).outcome == NOT_COMPARABLE
    assert compare_field(one("currency", value="USD"), sub).outcome == MISMATCH
    assert compare_field(one("currency", value="INR"), sub).outcome == MATCH


def test_an_amount_without_a_stated_currency_matches_but_says_so():
    result = compare_field(one("principal_amount", value="1500000.00"), SUBMITTED)
    assert result.outcome == MATCH and "does not state a currency" in result.explanation


@pytest.mark.parametrize(
    ("document", "outcome"),
    [("12345678901", MATCH), (" 12345678901 ", MATCH), ("12345678902", MISMATCH), ("1234 5678 901", MISMATCH)],
)
def test_fdr_numbers_match_only_when_identical(document, outcome):
    assert compare_field(one("fdr_number", value=document), SUBMITTED).outcome == outcome


def test_numbers_that_differ_only_in_case_or_spacing_are_flagged_not_merged():
    sub = Submitted(None, None, "AB-123", None, None, None, None, None, None)
    for document in ("ab-123", "AB- 123", "A B-123"):
        result = compare_field(one("fdr_number", value=document), sub)
        assert result.outcome == MISMATCH and "differ only in letter case or spacing" in result.explanation
    assert compare_field(one("fdr_number", value="AB123"), sub).explanation == "The numbers differ."  # punctuation matters


def test_dates_compare_exactly_and_a_two_way_date_is_ambiguous_even_if_one_reading_matches():
    sub = Submitted(None, None, None, None, None, None, None, "2026-04-03", "2027-01-15")
    exact = compare_field(one("issue_date", value="2026-04-03"), sub)
    assert exact.outcome == MATCH
    assert compare_field(one("issue_date", value="2026-04-04"), sub).outcome == MISMATCH
    both = one("issue_date", "AMBIGUOUS", candidates=[{"value": "2026-03-04", "page": 1}, {"value": "2026-04-03", "page": 1}], note="date_order_ambiguous")
    assert compare_field(both, sub).outcome == AMBIGUOUS
    neither = one("maturity_date", "AMBIGUOUS", candidates=[{"value": "2027-03-04", "page": 1}, {"value": "2027-04-03", "page": 1}], note="date_order_ambiguous")
    assert compare_field(neither, sub).outcome == MISMATCH
    unreadable = one("issue_date", "AMBIGUOUS", note="unreadable_date")
    assert compare_field(unreadable, sub).outcome == AMBIGUOUS


@pytest.mark.parametrize(("document", "outcome"), [("7.10", MATCH), ("7.1", MATCH), ("7.11", MISMATCH)])
def test_interest_rates_compare_exactly(document, outcome):
    assert compare_field(one("interest_rate_pct", value=document), SUBMITTED).outcome == outcome


@pytest.mark.parametrize(
    ("document", "outcome"),
    [("STATE BANK OF INDIA", MATCH), ("State Bank of India Ltd.", MATCH), ("SBI", MATCH), ("sbi", MATCH),
     ("State Bank Of India", MATCH), ("State Bank", MISMATCH), ("Bank of India", MISMATCH), ("Bank of Baroda", MISMATCH),
     ("BOB", MISMATCH)],
)
def test_bank_names_match_by_folding_or_an_explicit_alias_only(document, outcome):
    assert compare_field(one("bank_name", value=document), SUBMITTED).outcome == outcome


def test_aliases_work_in_both_directions_and_unknown_names_are_never_matched_loosely():
    mh = Submitted("Bank of Maharashtra", "BOM", None, None, None, None, None, None, None)
    assert compare_field(one("bank_name", value="BOM"), mh).outcome == MATCH
    assert compare_field(one("bank_name", value="Maharashtra Bank"), mh).outcome == MISMATCH
    sbi_long = Submitted("SBI", None, None, None, None, None, None, None, None)
    assert compare_field(one("bank_name", value="State Bank of India"), sbi_long).outcome == MATCH
    unknown = Submitted("Acme Co-operative Bank", "ACB", None, None, None, None, None, None, None)
    assert compare_field(one("bank_name", value="Acme Cooperative Bank"), unknown).outcome == MISMATCH
    assert compare_field(one("bank_name", value="ACB"), unknown).outcome == MATCH  # the bank's own registered code


def test_every_alias_maps_to_a_canonical_name_that_is_itself_listed():
    for alias, canonical in BANK_ALIASES.items():
        assert fold_bank(alias) == alias and BANK_ALIASES[canonical] == canonical


def test_names_compare_after_case_and_whitespace_folding_only():
    assert compare_field(one("depositor_name", value="pict  test institute"), SUBMITTED).outcome == MATCH
    assert compare_field(one("depositor_name", value="PICT"), SUBMITTED).outcome == MISMATCH
    assert compare_field(one("branch_name", value="Pune"), SUBMITTED).outcome == MISMATCH
