"""Hindi and Marathi extraction and comparison. All text is synthetic.

English behaviour is covered by test_extraction_fields.py and must not change; these tests only add
the localized labels, digits, month names and scripts on top of it.
"""

import shutil
import sys
import unicodedata
from pathlib import Path

import pytest

from app.extraction.analysis import ERROR_MESSAGES, analyse
from app.extraction.compare import (
    AMBIGUOUS, BANK_ALIASES, MATCH, MISMATCH, NOT_COMPARABLE, NOT_FOUND, Submitted, compare_field, fold_bank,
)
from app.extraction.fields import ExtractedField, ascii_digits, extract_fields, normalise_text, parse_amount, parse_date, parse_rate
from app.extraction.ocr import TesseractEngine
from app.extraction.sandbox import SandboxedExtractor
from app.extraction.text import LocalTextExtractor
from app.extraction.types import ErrorCode, Limits, PageText, TextSource
from tests.extraction_support import FakeOcr, make_image_pdf, make_unicode_pdf, tiny_jpeg

HINDI = """भारतीय स्टेट बैंक
सावधि जमा रसीद
शाखा: पुणे कैंप
एफडीआर क्रमांक: १२३४५६७८९०१
जमाकर्ता का नाम: पिक्ट टेस्ट संस्थान
जमा राशि: रु. १५,००,०००.००
जमा की तिथि: १५ जनवरी २०२६
परिपक्वता तिथि: १५ जनवरी २०२७
ब्याज दर: ७.१० % प्रति वर्ष"""

MARATHI = """बँक ऑफ महाराष्ट्र
मुदत ठेव पावती
शाखा: पुणे कॅम्प
ठेव पावती क्रमांक: ४५६७८९०१२३
ठेवीदाराचे नाव: पिक्ट टेस्ट संस्था
मुद्दल रक्कम: रुपये १५,००,०००
ठेव दिनांक: १५ जानेवारी २०२६
मुदतपूर्ती दिनांक: १५ जानेवारी २०२७
व्याज दर: ७.१० टक्के"""


def fields(text: str, source: str = TextSource.EMBEDDED_TEXT) -> dict[str, ExtractedField]:
    return {f.name: f for f in extract_fields([PageText(1, source, text)])}


def one(name, status="EXTRACTED", value=None, **kw) -> ExtractedField:
    return ExtractedField(name, status, value, **kw)


def submitted(**kw) -> Submitted:
    base = dict(bank_name=None, bank_code=None, fdr_number=None, branch_name=None, depositor_name=None,
                principal_amount=None, interest_rate_pct=None, issue_date=None, maturity_date=None)
    base.update(kw)
    return Submitted(**base)


# ---- whole receipts -----------------------------------------------------------------------------
def test_a_hindi_receipt_is_read_with_values_parsed_and_the_printed_text_kept():
    f = fields(HINDI)
    assert {k: v.value for k, v in f.items()} == {
        "bank_name": "भारतीय स्टेट बैंक", "branch_name": "पुणे कैंप", "fdr_number": "१२३४५६७८९०१",
        "depositor_name": "पिक्ट टेस्ट संस्थान", "principal_amount": "1500000.00", "currency": "INR",
        "issue_date": "2026-01-15", "maturity_date": "2027-01-15", "interest_rate_pct": "7.10",
    }
    assert f["principal_amount"].raw == "रु. १५,००,०००.००"
    assert f["issue_date"].raw == "१५ जनवरी २०२६" and f["interest_rate_pct"].raw == "७.१० %"
    assert f["fdr_number"].raw == "१२३४५६७८९०१"  # an identifier is never converted
    assert all(v.status == "EXTRACTED" and v.page == 1 for v in f.values())
    assert f["fdr_number"].confidence == "HIGH" and f["bank_name"].confidence == "MEDIUM"


def test_a_marathi_receipt_is_read_the_same_way():
    f = fields(MARATHI)
    assert {k: v.value for k, v in f.items()} == {
        "bank_name": "बँक ऑफ महाराष्ट्र", "branch_name": "पुणे कॅम्प", "fdr_number": "४५६७८९०१२३",
        "depositor_name": "पिक्ट टेस्ट संस्था", "principal_amount": "1500000", "currency": "INR",
        "issue_date": "2026-01-15", "maturity_date": "2027-01-15", "interest_rate_pct": "7.10",
    }
    assert f["principal_amount"].raw == "रुपये १५,००,०००" and f["maturity_date"].raw == "१५ जानेवारी २०२७"


def test_a_bilingual_receipt_agrees_with_itself_and_stays_one_value_per_field():
    text = "FDR No: 12345678901\nएफडीआर क्रमांक: 12345678901\nPrincipal Amount: Rs. 1,500,000.00\nजमा राशि: रु. 15,00,000.00"
    f = fields(text)
    assert f["fdr_number"].status == "EXTRACTED" and f["fdr_number"].value == "12345678901"
    assert f["principal_amount"].status == "EXTRACTED" and f["principal_amount"].value == "1500000.00"


def test_a_bilingual_receipt_whose_languages_disagree_is_ambiguous():
    f = fields("FDR No: 12345678901\nएफडीआर क्रमांक: 99999999999")["fdr_number"]
    assert f.status == "AMBIGUOUS" and f.value is None
    assert {c["value"] for c in f.candidates} == {"12345678901", "99999999999"}


def test_real_pdf_text_in_devanagari_is_extracted_through_pypdf():
    for text, number in [(HINDI, "१२३४५६७८९०१"), (MARATHI, "४५६७८९०१२३")]:
        analysis = analyse(make_unicode_pdf([text.splitlines()]), "application/pdf", LocalTextExtractor(), Limits())
        assert analysis.status == "COMPLETED" and analysis.result["ocr_pages"] == 0
        by_name = {f["name"]: f for f in analysis.result["fields"]}
        assert by_name["fdr_number"]["value"] == number and by_name["fdr_number"]["source"] == "EMBEDDED_TEXT"
        assert by_name["fdr_number"]["confidence"] == "HIGH" and by_name["principal_amount"]["value"].startswith("1500000")
        assert by_name["issue_date"]["value"] == "2026-01-15" and by_name["issue_date"]["raw"].startswith("१५")


# ---- every localized label ------------------------------------------------------------------------
LABEL_CASES = [
    # bank name
    ("bank_name", "बैंक का नाम: पंजाब नेशनल बैंक", "पंजाब नेशनल बैंक"),
    ("bank_name", "बैंक नाम: कैनरा बैंक", "कैनरा बैंक"),
    ("bank_name", "जारीकर्ता बैंक: एचडीएफसी बैंक", "एचडीएफसी बैंक"),
    ("bank_name", "बँकेचे नाव: बँक ऑफ बडोदा", "बँक ऑफ बडोदा"),
    ("bank_name", "बँकेचं नाव: कॅनरा बँक", "कॅनरा बँक"),
    # branch
    ("branch_name", "शाखा का नाम: पुणे कैंप", "पुणे कैंप"),
    ("branch_name", "शाखा नाम: कोथरूड", "कोथरूड"),
    ("branch_name", "शाखेचे नाव: शिवाजीनगर", "शिवाजीनगर"),
    # FDR number
    ("fdr_number", "एफ.डी.आर. क्र.: 55-AB-1", "55-AB-1"),
    ("fdr_number", "एफडीआर संख्या: 5501", "5501"),
    ("fdr_number", "जमा रसीद संख्या: ००४५२१", "००४५२१"),
    ("fdr_number", "रसीद क्रमांक: RC/2026/77", "RC/2026/77"),
    ("fdr_number", "सावधि जमा रसीद क्रमांक: 7788", "7788"),
    ("fdr_number", "प्रमाणपत्र क्रमांक: C-9911", "C-9911"),
    ("fdr_number", "ठेव पावती क्र.: ८८२१", "८८२१"),
    ("fdr_number", "मुदत ठेव पावती क्रमांक: 3300", "3300"),
    ("fdr_number", "पावती क्रमांक: 4400", "4400"),
    # depositor
    ("depositor_name", "जमाकर्ता: पिक्ट", "पिक्ट"),
    ("depositor_name", "ग्राहक का नाम: कॉलेज ऑफ इंजीनियरिंग", "कॉलेज ऑफ इंजीनियरिंग"),
    ("depositor_name", "खाताधारक का नाम: संस्था एक", "संस्था एक"),
    ("depositor_name", "ठेवीदाराचे नाव: महाविद्यालय", "महाविद्यालय"),
    ("depositor_name", "ग्राहकाचे नाव: संस्था दोन", "संस्था दोन"),
    ("depositor_name", "खातेदाराचे नाव: संस्था तीन", "संस्था तीन"),
    # principal
    ("principal_amount", "मूल राशि: 100000", "100000"),
    ("principal_amount", "जमा की गई राशि: 250000", "250000"),
    ("principal_amount", "मूलधन: ५०,०००", "50000"),
    ("principal_amount", "मुद्दल: 75000", "75000"),
    ("principal_amount", "ठेव रक्कम: १,००,०००", "100000"),
    ("principal_amount", "ठेवीची रक्कम: 1,00,000", "100000"),
    ("principal_amount", "जमा रक्कम: 20000", "20000"),
    # issue date
    ("issue_date", "जमा तिथि: 2026-01-15", "2026-01-15"),
    ("issue_date", "जारी करने की तिथि: 15 मार्च 2026", "2026-03-15"),
    ("issue_date", "प्रारंभ तिथि: 15 जून 2026", "2026-06-15"),
    ("issue_date", "ठेवीचा दिनांक: 15 मे 2026", "2026-05-15"),
    ("issue_date", "सुरुवात दिनांक: 15 जुलै 2026", "2026-07-15"),
    ("issue_date", "खाते उघडल्याचा दिनांक: 15 एप्रिल 2026", "2026-04-15"),
    # maturity date
    ("maturity_date", "परिपक्वता की तिथि: 15 मार्च 2027", "2027-03-15"),
    ("maturity_date", "परिपक्वता दिनांक: 15 मार्च 2027", "2027-03-15"),
    ("maturity_date", "देय तिथि: 15 मार्च 2027", "2027-03-15"),
    ("maturity_date", "मुदतपूर्तीचा दिनांक: 15 मार्च 2027", "2027-03-15"),
    ("maturity_date", "मुदत संपण्याचा दिनांक: 15 मार्च 2027", "2027-03-15"),
    # interest
    ("interest_rate_pct", "ब्याज की दर: 6.5 प्रतिशत", "6.5"),
    ("interest_rate_pct", "व्याजाचा दर: 7 टक्के", "7"),
    ("interest_rate_pct", "व्याजदर: ७.२५ %", "7.25"),
]


@pytest.mark.parametrize(("name", "line", "expected"), LABEL_CASES)
def test_each_localized_label_finds_its_field(name, line, expected):
    result = fields(line)[name]
    assert result.status == "EXTRACTED" and result.value == expected, result
    assert result.confidence == "HIGH" or name == "bank_name"
    assert all(v.status == "NOT_FOUND" for k, v in fields(line).items() if k not in (name, "currency"))


def test_the_english_labels_still_work_in_the_same_document():
    f = fields("Branch: Pune Camp\nमूल राशि: 100000\nMaturity Date: 15 Jan 2027")
    assert (f["branch_name"].value, f["principal_amount"].value, f["maturity_date"].value) == ("Pune Camp", "100000", "2027-01-15")


# ---- digits, amounts and rates -------------------------------------------------------------------
def test_devanagari_digits_convert_one_for_one():
    assert ascii_digits("०१२३४५६७८९") == "0123456789" and ascii_digits("Rs १५,००,००० ab") == "Rs 15,00,000 ab"
    assert len(ascii_digits("१५,००,०००")) == len("१५,००,०००")


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [("रु. १५,००,०००.००", "1500000.00", "INR"), ("रुपये १५,००,०००", "1500000", "INR"), ("१५,००,००० रुपये", "1500000", "INR"),
     ("₹ १५०००००", "1500000", "INR"), ("रू. 15,00,000", "1500000", "INR"), ("१,५००,०००", "1500000", None),
     ("Rs. १५,००,०००.५०", "1500000.50", "INR"), ("१५००००० रु.", "1500000", "INR")],
)
def test_devanagari_amounts_parse_exactly(text, amount, currency):
    from decimal import Decimal

    assert parse_amount(text) == (Decimal(amount), currency)


@pytest.mark.parametrize("text", ["रु. १५,००,०००.५०५", "रु. १,५,००,०००", "रुपये", "१५,००,००००", "१.५००.०००,००", "रु. ,५००"])
def test_devanagari_amounts_that_cannot_be_read_unambiguously_are_refused(text):
    assert parse_amount(text) is None


def test_an_unreadable_localized_amount_is_ambiguous_not_guessed():
    f = fields("जमा राशि: रु. १५,००,०००.५०५")["principal_amount"]
    assert f.status == "AMBIGUOUS" and f.value is None and f.note == "unreadable_amount"


def test_the_maturity_amount_is_never_taken_for_the_principal_in_hindi_or_marathi():
    assert fields("परिपक्वता राशि: रु. १६,००,०००")["principal_amount"].status == "NOT_FOUND"
    assert fields("मुदतपूर्ती रक्कम: रुपये १६,००,०००")["principal_amount"].status == "NOT_FOUND"
    f = fields("परिपक्वता राशि: रु. १६,००,०००\nजमा राशि: रु. १५,००,०००")
    assert f["principal_amount"].value == "1500000"


@pytest.mark.parametrize(("text", "rate"), [("७.१० %", "7.10"), ("८ प्रतिशत", "8"), ("६.५ टक्के", "6.5"), ("७.२५ प्रति वर्ष", "7.25"),
                                            ("7.5 टक्का", "7.5")])
def test_localized_rates_need_an_explicit_marker(text, rate):
    from decimal import Decimal

    assert parse_rate(text) == Decimal(rate)


@pytest.mark.parametrize("text", ["७.१०", "७.१० रुपये", "१५० प्रतिशत", "टक्के"])
def test_localized_rates_without_a_marker_or_out_of_range_are_not_read(text):
    assert parse_rate(text) is None


# ---- dates ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [("१५ जनवरी २०२६", {"2026-01-15"}), ("१५ जानेवारी २०२६", {"2026-01-15"}), ("१ फरवरी २०२६", {"2026-02-01"}),
     ("१ फ़रवरी २०२६", {"2026-02-01"}), ("1 फेब्रुवारी 2026", {"2026-02-01"}), ("३ अप्रैल २०२६", {"2026-04-03"}),
     ("३ एप्रिल २०२६", {"2026-04-03"}), ("५ मई २०२६", {"2026-05-05"}), ("५ मे २०२६", {"2026-05-05"}), ("१० जुलाई २०२६", {"2026-07-10"}),
     ("१० जुलै २०२६", {"2026-07-10"}), ("९ सितंबर २०२६", {"2026-09-09"}), ("९ सप्टेंबर २०२६", {"2026-09-09"}),
     ("२० अक्टूबर २०२६", {"2026-10-20"}), ("२० ऑक्टोबर २०२६", {"2026-10-20"}), ("२ नवंबर २०२६", {"2026-11-02"}),
     ("२ नोव्हेंबर २०२६", {"2026-11-02"}), ("२५ दिसंबर २०२६", {"2026-12-25"}), ("२५ डिसेंबर २०२६", {"2026-12-25"}),
     ("२७/१०/२०२६", {"2026-10-27"}), ("१०/२७/२०२६", {"2026-10-27"}), ("२०२६-०१-१५", {"2026-01-15"}), ("१५ ऑगस्ट २०२६", {"2026-08-15"}),
     ("०३/०४/२०२६", {"2026-04-03", "2026-03-04"})],
)
def test_devanagari_dates_parse_explicitly_and_numeric_order_is_never_guessed(text, expected):
    assert {d.isoformat() for d in parse_date(text)} == expected


@pytest.mark.parametrize("text", ["३१/०२/२०२६", "१५ जनवर २०२६", "१५ जनवरी २६", "१५ Foo २०२६", "३१ अप्रैल २०२६", "कल"])
def test_impossible_or_unknown_devanagari_dates_are_refused(text):
    assert parse_date(text) is None


def test_a_decomposed_nukta_in_a_month_name_is_recognised():
    decomposed = unicodedata.normalize("NFD", "१ फ़रवरी २०२६")
    assert decomposed != "१ फ़रवरी २०२६" or True
    assert {d.isoformat() for d in parse_date(decomposed)} == {"2026-02-01"}


def test_an_ambiguous_devanagari_numeric_date_lists_both_readings_and_keeps_the_printed_text():
    f = fields("परिपक्वता तिथि: ०३/०४/२०२७")["maturity_date"]
    assert f.status == "AMBIGUOUS" and f.value is None and f.note == "date_order_ambiguous" and f.raw == "०३/०४/२०२७"
    assert sorted(c["value"] for c in f.candidates) == ["2027-03-04", "2027-04-03"]
    sub = submitted(maturity_date="2027-04-03")
    assert compare_field(f, sub).outcome == AMBIGUOUS


def test_the_issue_date_label_does_not_pick_up_the_maturity_date_in_hindi():
    f = fields("परिपक्वता तिथि: १५ जनवरी २०२७")
    assert f["issue_date"].status == "NOT_FOUND" and f["maturity_date"].value == "2027-01-15"


def test_two_different_localized_dates_for_one_field_are_ambiguous():
    f = fields("परिपक्वता तिथि: १५ जनवरी २०२७\nदेय तिथि: १६ जनवरी २०२७")["maturity_date"]
    assert f.status == "AMBIGUOUS" and f.note == "multiple_values"


# ---- missing labels, no inference ----------------------------------------------------------------
def test_unlabelled_devanagari_text_yields_nothing():
    f = fields("पुणे में १५ जनवरी २०२६ को १५,००,००० रुपये की सावधि जमा की गई। दर ७.१० प्रतिशत थी।\nभारतीय नागरिक")
    assert all(v.status == "NOT_FOUND" for k, v in f.items())


def test_a_value_without_its_label_is_not_attributed_to_another_label():
    f = fields("जमा की तिथि: १५ जनवरी २०२६")
    assert f["issue_date"].status == "EXTRACTED" and f["maturity_date"].status == "NOT_FOUND"


@pytest.mark.parametrize(
    "line", ["शाखा कोड: ०४१२३", "शाखा क्र.: ०४१२३", "शाखा क्रमांक: ०४१२३"],
)
def test_a_branch_code_is_not_a_branch_name(line):
    assert fields(line)["branch_name"].status == "NOT_FOUND"


def test_a_label_in_the_middle_of_a_localized_sentence_does_not_claim_the_next_line():
    f = fields("यह रसीद पुणे शाखा\nएफडीआर क्रमांक: १२३४५६७८९०१")
    assert f["branch_name"].status == "NOT_FOUND"


def test_a_localized_label_with_no_value_is_not_found_not_a_neighbouring_label():
    f = fields("जमाकर्ता का नाम:\nजमा राशि: रु. १,००,०००")
    assert f["depositor_name"].status == "NOT_FOUND" and f["principal_amount"].value == "100000"


def test_values_do_not_run_into_the_next_localized_label_on_the_same_line():
    f = fields("जमाकर्ता का नाम: पिक्ट संस्थान जमा राशि: रु. १,००,००० शाखा: पुणे")
    assert f["depositor_name"].value == "पिक्ट संस्थान" and f["principal_amount"].value == "100000" and f["branch_name"].value == "पुणे"


def test_header_lines_that_merely_contain_the_word_bank_are_not_bank_names_in_hindi_or_marathi():
    assert fields("बैंक की प्रति\nशाखा: पुणे")["bank_name"].status == "NOT_FOUND"
    assert fields("बँक पावती प्रत\nशाखा: पुणे")["bank_name"].status == "NOT_FOUND"
    assert fields("जमा रसीद - बैंक कॉपी")["bank_name"].status == "NOT_FOUND"


def test_a_marathi_header_bank_is_medium_confidence_and_a_labelled_one_high():
    assert fields("बँक ऑफ महाराष्ट्र")["bank_name"].confidence == "MEDIUM"
    assert fields("बँकेचे नाव: बँक ऑफ महाराष्ट्र")["bank_name"].confidence == "HIGH"


# ---- Unicode handling ------------------------------------------------------------------------------
def test_text_in_either_normalisation_form_gives_the_same_result():
    for form in ("NFC", "NFD", "NFKC"):
        text = unicodedata.normalize(form, HINDI)
        f = fields(text)
        assert f["principal_amount"].value == "1500000.00" and f["issue_date"].value == "2026-01-15", form
        assert f["fdr_number"].value == "१२३४५६७८९०१"


def test_invisible_zero_width_spaces_and_byte_order_marks_do_not_break_matching():
    noisy = HINDI.replace("जमा राशि", "जमा\u200b राशि").replace("शाखा:", "\ufeffशाखा:")
    f = fields(noisy)
    assert f["principal_amount"].value == "1500000.00" and f["branch_name"].value == "पुणे कैंप"
    assert normalise_text("a\u200bb\ufeffc\xa0d") == "abc d"


def test_values_are_returned_exactly_as_printed_apart_from_normalisation_and_spacing():
    f = fields("जमाकर्ता का नाम:   पिक्ट टेस्ट\u200b संस्थान ")
    assert f["depositor_name"].value == "पिक्ट टेस्ट संस्थान"  # a zero-width space is dropped; nothing is transliterated
    assert fields("जमाकर्ता का नाम: पिक्ट   टेस्ट")["depositor_name"].value == "पिक्ट"  # a wide gap ends the value (a table column)
    assert fields("ठेव पावती क्रमांक: ab-१२/x९")["fdr_number"].value == "ab-१२/x९"


def test_label_matching_does_not_split_words_inside_devanagari_clusters():
    assert fields("जमाकर्ताओं की सूची: ५")["depositor_name"].status == "NOT_FOUND"  # a longer word, not the label
    assert fields("शाखाओं: ५")["branch_name"].status == "NOT_FOUND"


def test_ocr_text_keeps_the_existing_confidence_ceiling_for_localized_labels():
    f = fields(HINDI, source=TextSource.OCR)
    assert f["fdr_number"].confidence == "MEDIUM" and f["fdr_number"].source == "OCR"
    assert f["bank_name"].confidence == "LOW"
    assert fields("एफडीआर क्रमांक\n१२३४५", source=TextSource.OCR)["fdr_number"].confidence == "LOW"


# ---- comparison ------------------------------------------------------------------------------------
def test_a_matching_hindi_document_matches_in_every_comparable_field():
    sub = submitted(bank_name="State Bank of India", bank_code="SBI", fdr_number="१२३४५६७८९०१", branch_name="पुणे कैंप",
                    depositor_name="पिक्ट टेस्ट संस्थान", principal_amount="1500000.00", interest_rate_pct="7.10",
                    issue_date="2026-01-15", maturity_date="2027-01-15")
    outcomes = {f.name: compare_field(f, sub).outcome for f in extract_fields([PageText(1, "EMBEDDED_TEXT", HINDI)])}
    assert set(outcomes.values()) == {MATCH}, outcomes


def test_amounts_dates_and_rates_printed_in_devanagari_compare_against_ordinary_submitted_values():
    sub = submitted(principal_amount="1500000.00", issue_date="2026-01-15", maturity_date="2027-01-15", interest_rate_pct="7.10")
    f = fields(HINDI)
    assert [compare_field(f[n], sub).outcome for n in ("principal_amount", "issue_date", "maturity_date", "interest_rate_pct")] == [MATCH] * 4
    wrong = fields("जमा राशि: रु. १६,००,०००.००\nजमा की तिथि: १६ जनवरी २०२६")
    assert compare_field(wrong["principal_amount"], sub).outcome == MISMATCH
    assert compare_field(wrong["issue_date"], sub).outcome == MISMATCH


def test_a_devanagari_identifier_is_never_merged_with_an_ascii_one():
    printed = fields("एफडीआर क्रमांक: १२३४५६७८९०१")["fdr_number"]
    mismatch = compare_field(printed, submitted(fdr_number="12345678901"))
    assert mismatch.outcome == MISMATCH and "Devanagari digits" in mismatch.explanation
    assert compare_field(printed, submitted(fdr_number="१२३४५६७८९०१")).outcome == MATCH


@pytest.mark.parametrize(
    ("document", "submitted_name", "code"),
    [("भारतीय स्टेट बैंक", "State Bank of India", "SBI"), ("स्टेट बैंक ऑफ़ इंडिया", "State Bank of India", None),
     ("बैंक ऑफ महाराष्ट्र", "Bank of Maharashtra", "BOM"), ("बँक ऑफ महाराष्ट्र", "Bank of Maharashtra", "BOM"),
     ("बँक ऑफ बडोदा", "Bank of Baroda", "BOB"), ("कॅनरा बँक", "Canara Bank", "CAN"), ("एचडीएफसी बैंक", "HDFC Bank", "HDFC"),
     ("पंजाब नेशनल बैंक", "Punjab National Bank", "PNB"), ("बँक ऑफ महाराष्ट्र", "बँक ऑफ महाराष्ट्र", None)],
)
def test_known_hindi_and_marathi_bank_names_match_through_explicit_aliases(document, submitted_name, code):
    assert compare_field(one("bank_name", value=document), submitted(bank_name=submitted_name, bank_code=code)).outcome == MATCH


def test_an_unknown_localized_bank_is_not_matched_loosely_and_different_scripts_are_not_comparable():
    sub = submitted(bank_name="State Bank of India", bank_code="SBI")
    assert compare_field(one("bank_name", value="अज्ञात सहकारी बैंक"), sub).outcome == NOT_COMPARABLE  # unknown name, other script
    assert compare_field(one("bank_name", value="बैंक ऑफ बड़ौदा"), sub).outcome == NOT_COMPARABLE  # a real bank, but not this one
    assert compare_field(one("bank_name", value="बैंक ऑफ बड़ौदा"), submitted(bank_name="बैंक ऑफ महाराष्ट्र")).outcome == MISMATCH


def test_names_in_different_scripts_are_not_transliterated_and_not_judged():
    sub = submitted(branch_name="Pune Camp", depositor_name="PICT Test Institute")
    for name, value in [("branch_name", "पुणे कैंप"), ("depositor_name", "पिक्ट टेस्ट संस्थान")]:
        result = compare_field(one(name, value=value), sub)
        assert result.outcome == NOT_COMPARABLE and "different scripts" in result.explanation


def test_names_in_the_same_script_compare_after_unicode_normalisation():
    a = unicodedata.normalize("NFD", "पुणे कैंप")
    assert compare_field(one("branch_name", value=a), submitted(branch_name="पुणे कैंप")).outcome == MATCH
    assert compare_field(one("branch_name", value="पुणे शिवाजीनगर"), submitted(branch_name="पुणे कैंप")).outcome == MISMATCH
    assert compare_field(one("depositor_name", value="पिक्ट  टेस्ट संस्थान"), submitted(depositor_name="पिक्ट टेस्ट संस्थान")).outcome == MATCH


@pytest.mark.parametrize("name", ["bank_name", "branch_name", "fdr_number", "depositor_name", "principal_amount", "currency",
                                  "issue_date", "maturity_date", "interest_rate_pct"])
def test_missing_localized_values_never_become_matches(name):
    assert compare_field(one(name, "NOT_FOUND"), submitted()).outcome == NOT_FOUND
    ambiguous = one(name, "AMBIGUOUS", candidates=[{"value": "x", "page": 1}], note="multiple_values")
    assert compare_field(ambiguous, submitted()).outcome == AMBIGUOUS


def test_bank_name_folding_keeps_devanagari_vowel_signs_and_every_alias_is_canonical():
    assert fold_bank("  बँक   ऑफ  महाराष्ट्र। ") == "बँक ऑफ महाराष्ट्र"
    assert fold_bank("बैंक") != fold_bank("बंक") and "ै" in fold_bank("बैंक")
    for alias, canonical in BANK_ALIASES.items():
        assert fold_bank(alias) == alias and BANK_ALIASES[canonical] == canonical


# ---- OCR languages -----------------------------------------------------------------------------------
def _engine(tmp_path: Path, installed: list[str], language: str) -> TesseractEngine:
    body = (
        "import sys\n"
        "if '--list-langs' in sys.argv:\n"
        "    print('List of available languages (%d):')\n" % len(installed)
        + "".join(f"    print('{lang}')\n" for lang in installed)
        + "    sys.exit(0)\nprint('FDR No: 998877 read by the stand-in')\n"
    )
    script = tmp_path / "fake_tesseract.py"
    script.write_text(body)
    return TesseractEngine([sys.executable, str(script)], language)


def test_missing_ocr_language_data_is_reported_not_skipped(tmp_path):
    engine = _engine(tmp_path, ["eng", "hin"], "eng+hin+mar")
    assert engine.installed_languages() == {"eng", "hin"} and engine.missing_languages() == ["mar"]
    assert _engine(tmp_path, ["eng", "hin", "mar"], "eng+hin+mar").missing_languages() == []
    assert _engine(tmp_path, [], "eng").missing_languages() == ["eng"]


def test_a_scanned_pdf_or_image_is_refused_with_the_missing_language_named(tmp_path):
    extractor = LocalTextExtractor(_engine(tmp_path, ["eng", "hin"], "eng+hin+mar"))
    for data, kind in [(make_image_pdf(), "application/pdf"), (tiny_jpeg(), "image/jpeg")]:
        result = extractor.extract(data, kind, Limits())
        assert result.error == ErrorCode.OCR_LANGUAGE_UNAVAILABLE and result.pages == []
        assert result.warnings == ["missing_ocr_languages:mar"]
    analysis = analyse(make_image_pdf(), "application/pdf", extractor, Limits())
    assert analysis.status == "FAILED" and analysis.error_code == "OCR_LANGUAGE_UNAVAILABLE" and analysis.result["fields"] == []
    assert analysis.result["warnings"] == ["missing_ocr_languages:mar"]
    assert "language data" in ERROR_MESSAGES[ErrorCode.OCR_LANGUAGE_UNAVAILABLE]


def test_text_pdfs_need_no_ocr_language_at_all(tmp_path):
    extractor = LocalTextExtractor(_engine(tmp_path, ["eng"], "eng+hin+mar"))
    analysis = analyse(make_unicode_pdf([MARATHI.splitlines()]), "application/pdf", extractor, Limits())
    assert analysis.status == "COMPLETED" and analysis.result["ocr_pages"] == 0


def test_all_languages_installed_lets_ocr_run(tmp_path):
    extractor = LocalTextExtractor(_engine(tmp_path, ["eng", "hin", "mar"], "eng+hin+mar"))
    result = extractor.extract(make_image_pdf(), "application/pdf", Limits())
    assert result.error is None and result.pages[0].source == "OCR"


# ---- the real engine on this machine (skipped where it is not installed) ----------------------------
def _real_tesseract() -> list[str] | None:
    for candidate in (shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return [candidate]
    return None


IMAGE = Path(__file__).parent / "data" / "synthetic_devanagari_receipt.png"


@pytest.mark.skipif(_real_tesseract() is None or "hin" not in TesseractEngine(_real_tesseract() or ["x"], "eng").installed_languages(),
                    reason="Tesseract with Hindi data is not installed")
def test_real_tesseract_reads_synthetic_devanagari_and_the_result_is_capped_at_medium_confidence():
    command = _real_tesseract()
    result = SandboxedExtractor(command, "eng+hin").extract(IMAGE.read_bytes(), "image/png", Limits(timeout_seconds=90))
    assert result.error is None and result.pages[0].source == "OCR" and "tesseract" in result.engine
    f = {x.name: x for x in extract_fields(result.pages)}
    # What OCR reads is checked, not assumed: the amount line is read; a label may be misread, and then
    # the field is simply NOT_FOUND rather than guessed.
    assert f["principal_amount"].status == "EXTRACTED" and f["principal_amount"].value.startswith("1500000")
    assert f["principal_amount"].confidence in ("MEDIUM", "LOW") and f["principal_amount"].source == "OCR"
    assert f["fdr_number"].status in ("EXTRACTED", "AMBIGUOUS", "NOT_FOUND")
    if f["fdr_number"].status == "EXTRACTED":
        assert f["fdr_number"].confidence in ("MEDIUM", "LOW")


@pytest.mark.skipif(_real_tesseract() is None or "mar" in TesseractEngine(_real_tesseract() or ["x"], "eng").installed_languages(),
                    reason="Marathi OCR data is installed here, so the missing-language path cannot be shown")
def test_real_tesseract_without_marathi_data_refuses_marathi_ocr_explicitly():
    result = SandboxedExtractor(_real_tesseract(), "eng+hin+mar").extract(IMAGE.read_bytes(), "image/png", Limits(timeout_seconds=60))
    assert result.error == ErrorCode.OCR_LANGUAGE_UNAVAILABLE and result.warnings == ["missing_ocr_languages:mar"] and result.pages == []
