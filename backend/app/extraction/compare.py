"""Comparing extracted values with what the institution submitted.

Outcomes, per field:
  MATCH           the document and the submitted record agree under the rules below
  MISMATCH        both have a value and they differ
  NOT_FOUND       nothing was found in the document for this field. Never a match, even if the
                  submitted value is also empty
  AMBIGUOUS       the document yields several possible values, or a date that can be read two ways.
                  Never a match, even if the submitted value equals one reading
  NOT_COMPARABLE  the field cannot be compared: no submitted value, or a different currency

Rules (deliberately narrow; a person decides everything the rules do not settle):
  * amounts: exact decimal equality to the paisa; no rounding, no currency conversion
  * dates: parsed explicitly; numeric dd/mm vs mm/dd is never guessed (see fields.parse_date)
  * FDR numbers: equal only if identical after trimming outer whitespace. A difference of letter case,
    inner spaces or punctuation is reported as MISMATCH with a note, never merged
  * bank names: equal after case/punctuation folding, or equal through an alias that is written down
    in BANK_ALIASES below; nothing is matched fuzzily
  * branch and depositor names: equal after case and whitespace folding only
  * interest rate: exact decimal equality, only when the document states one
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.extraction.fields import ExtractedField

MATCH, MISMATCH, NOT_FOUND, AMBIGUOUS, NOT_COMPARABLE = "MATCH", "MISMATCH", "NOT_FOUND", "AMBIGUOUS", "NOT_COMPARABLE"
OUTCOMES = (MATCH, MISMATCH, NOT_FOUND, AMBIGUOUS, NOT_COMPARABLE)
ASSUMED_CURRENCY = "INR"  # the portal records every amount in INR

FIELD_LABELS = {
    "bank_name": "Bank",
    "branch_name": "Branch",
    "fdr_number": "FDR / receipt number",
    "depositor_name": "Depositor",
    "principal_amount": "Principal amount",
    "currency": "Currency",
    "issue_date": "Issue date",
    "maturity_date": "Maturity date",
    "interest_rate_pct": "Interest rate",
}

# Explicit aliases only. Add an entry here, with a test, to teach the comparison a new alias; nothing
# is inferred and nothing is transliterated. Each canonical name lists every spelling that is accepted
# for it, including the Hindi and Marathi spellings seen on bilingual receipts (spelling variants such
# as ऑफ and ऑफ़ are listed separately on purpose). Keys and values are stored in folded form.
_BANK_ALIAS_SOURCE: dict[str, list[str]] = {
    "state bank of india": ["SBI", "भारतीय स्टेट बैंक", "भारतीय स्टेट बँक", "स्टेट बैंक ऑफ इंडिया", "स्टेट बैंक ऑफ़ इंडिया",
                            "स्टेट बँक ऑफ इंडिया"],
    "bank of maharashtra": ["BOM", "बैंक ऑफ महाराष्ट्र", "बैंक ऑफ़ महाराष्ट्र", "बँक ऑफ महाराष्ट्र"],
    "bank of baroda": ["BOB", "बैंक ऑफ बड़ौदा", "बैंक ऑफ बडौदा", "बँक ऑफ बडोदा", "बँक ऑफ बरोडा"],
    "icici bank": ["ICICI", "आईसीआईसीआई बैंक", "आयसीआयसीआय बँक"],
    "hdfc bank": ["HDFC", "एचडीएफसी बैंक", "एचडीएफसी बँक"],
    "canara bank": ["Canara", "कैनरा बैंक", "कॅनरा बँक", "कैनरा बँक"],
    "punjab national bank": ["PNB", "पंजाब नेशनल बैंक", "पंजाब नैशनल बैंक", "पंजाब नॅशनल बँक"],
    "union bank of india": ["Union Bank", "यूनियन बैंक ऑफ इंडिया", "युनियन बँक ऑफ इंडिया"],
}


def fold_bank(value: str) -> str:
    """Case-, punctuation- and Unicode-form-insensitive form of a bank name. Letters and combining
    vowel signs of every script are kept (only punctuation and symbols are removed), so Devanagari
    names are not mangled."""
    text = unicodedata.normalize("NFC", value).casefold().replace("&", " and ")
    text = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
    text = re.sub(r"\b(?:limited|ltd)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


BANK_ALIASES: dict[str, str] = {}
for _canonical, _names in _BANK_ALIAS_SOURCE.items():
    BANK_ALIASES[fold_bank(_canonical)] = fold_bank(_canonical)
    for _name in _names:
        BANK_ALIASES[fold_bank(_name)] = fold_bank(_canonical)


@dataclass(frozen=True)
class Submitted:
    """The submitted side of a comparison: the current record, or a frozen revision snapshot."""

    bank_name: str | None
    bank_code: str | None
    fdr_number: str | None
    branch_name: str | None
    depositor_name: str | None
    principal_amount: str | None
    interest_rate_pct: str | None
    issue_date: str | None
    maturity_date: str | None


@dataclass(frozen=True)
class FieldOutcome:
    outcome: str
    explanation: str
    submitted_value: str | None


def fold_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _script(text: str) -> str:
    devanagari = any("\u0900" <= ch <= "\u097f" for ch in text)
    latin = any(ch.isascii() and ch.isalpha() for ch in text)
    return "mixed" if devanagari and latin else "devanagari" if devanagari else "latin" if latin else "other"


def _different_scripts(a: str, b: str) -> bool:
    """True when one text is only Devanagari and the other only Latin: names are never transliterated,
    so such a pair cannot be judged to match or differ."""
    return {_script(a), _script(b)} == {"devanagari", "latin"}


_SCRIPT_NOTE = (
    "The names are written in different scripts (Devanagari and Latin). They are not transliterated, "
    "so compare them by eye."
)
_DEVANAGARI_DIGIT = re.compile("[\u0966-\u096f]")


def _decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _gate(extracted: ExtractedField, submitted: str | None, label: str) -> FieldOutcome | None:
    """Outcomes decided before any value comparison: absence and ambiguity can never become a match."""
    if extracted.status == "NOT_FOUND":
        return FieldOutcome(NOT_FOUND, f"{label} was not found in the document.", submitted)
    if extracted.status == "AMBIGUOUS" and extracted.note != "date_order_ambiguous":
        return FieldOutcome(AMBIGUOUS, f"The document shows more than one possible {label.lower()}, or it could not be read reliably.", submitted)
    if submitted in (None, ""):
        return FieldOutcome(NOT_COMPARABLE, f"No {label.lower()} was submitted to compare with.", submitted)
    return None


def compare_field(extracted: ExtractedField, submitted: Submitted) -> FieldOutcome:
    name = extracted.name
    label = FIELD_LABELS[name]
    sub = {
        "bank_name": submitted.bank_name, "branch_name": submitted.branch_name, "fdr_number": submitted.fdr_number,
        "depositor_name": submitted.depositor_name, "principal_amount": submitted.principal_amount,
        "currency": ASSUMED_CURRENCY, "issue_date": submitted.issue_date, "maturity_date": submitted.maturity_date,
        "interest_rate_pct": submitted.interest_rate_pct,
    }[name]
    gate = _gate(extracted, sub, label)
    if gate is not None:
        return gate
    value = extracted.value

    if name == "fdr_number":
        a, b = value.strip(), sub.strip()
        if a == b:
            return FieldOutcome(MATCH, "The numbers are identical.", sub)
        if re.sub(r"\s", "", a).casefold() == re.sub(r"\s", "", b).casefold():
            return FieldOutcome(MISMATCH, "The numbers differ only in letter case or spacing. They are not treated as the same; check them by eye.", sub)
        if _DEVANAGARI_DIGIT.search(a) and not _DEVANAGARI_DIGIT.search(b):
            return FieldOutcome(MISMATCH, "The number is printed in Devanagari digits. Identifiers are never converted, so it is not treated as equal to the submitted number; compare them by eye.", sub)
        return FieldOutcome(MISMATCH, "The numbers differ.", sub)

    if name == "principal_amount":
        if extracted.currency and extracted.currency != ASSUMED_CURRENCY:
            return FieldOutcome(NOT_COMPARABLE, f"The document states {extracted.currency}; amounts here are recorded in INR and are not converted.", sub)
        a, b = _decimal(value), _decimal(sub)
        if a is None or b is None:
            return FieldOutcome(NOT_COMPARABLE, "An amount could not be read as a number.", sub)
        if a == b:
            note = "" if extracted.currency else " The document does not state a currency next to the amount."
            return FieldOutcome(MATCH, "The amounts are equal to the paisa." + note, sub)
        return FieldOutcome(MISMATCH, "The amounts differ.", sub)

    if name == "currency":
        if extracted.value == ASSUMED_CURRENCY:
            return FieldOutcome(MATCH, "The document states INR.", sub)
        return FieldOutcome(MISMATCH, f"The document states {extracted.value}; the record is in INR.", sub)

    if name in ("issue_date", "maturity_date"):
        submitted_date = _iso(sub)
        readings = [r for r in ([c["value"] for c in extracted.candidates] if extracted.status == "AMBIGUOUS" else [value]) if r]
        parsed = [d for d in (_iso(r) for r in readings) if d]
        if submitted_date is None or not parsed:
            return FieldOutcome(NOT_COMPARABLE, "A date could not be read.", sub)
        if extracted.status == "AMBIGUOUS":
            if submitted_date in parsed:
                return FieldOutcome(AMBIGUOUS, "The document's numeric date can be read two ways. One reading equals the submitted date, but the order is not guessed; confirm it by eye.", sub)
            return FieldOutcome(MISMATCH, "Neither possible reading of the document's date equals the submitted date.", sub)
        return FieldOutcome(MATCH, "The dates are equal.", sub) if parsed[0] == submitted_date else FieldOutcome(MISMATCH, "The dates differ.", sub)

    if name == "interest_rate_pct":
        a, b = _decimal(value), _decimal(sub)
        if a is None or b is None:
            return FieldOutcome(NOT_COMPARABLE, "A rate could not be read as a number.", sub)
        return FieldOutcome(MATCH, "The rates are equal.", sub) if a == b else FieldOutcome(MISMATCH, "The rates differ.", sub)

    if name == "bank_name":
        fa, fb = fold_bank(value), fold_bank(sub)
        if fa == fb:
            return FieldOutcome(MATCH, "The names are the same apart from case and punctuation.", sub)
        if submitted.bank_code and fa == fold_bank(submitted.bank_code):
            return FieldOutcome(MATCH, "The document gives the bank's registered short code.", sub)
        canonical_a, canonical_b = BANK_ALIASES.get(fa), BANK_ALIASES.get(fb)
        if canonical_a is not None and canonical_a == canonical_b:
            return FieldOutcome(MATCH, "The names are known aliases of the same bank.", sub)
        if _different_scripts(value, sub):
            return FieldOutcome(NOT_COMPARABLE, _SCRIPT_NOTE, sub)
        return FieldOutcome(MISMATCH, "The names differ.", sub)

    # branch_name, depositor_name
    if unicodedata.normalize("NFC", fold_text(value)) == unicodedata.normalize("NFC", fold_text(sub)):
        return FieldOutcome(MATCH, "The names are the same apart from case and spacing.", sub)
    if _different_scripts(value, sub):
        return FieldOutcome(NOT_COMPARABLE, _SCRIPT_NOTE, sub)
    return FieldOutcome(MISMATCH, "The names differ. Names are compared as written; a person should decide whether this matters.", sub)


def compare_all(extracted: list[ExtractedField], submitted: Submitted) -> list[tuple[ExtractedField, FieldOutcome]]:
    return [(f, compare_field(f, submitted)) for f in extracted]


def _money(value) -> str | None:
    return None if value is None else format(value, "f")


def submitted_from_fdr(fdr, bank) -> Submitted:
    """The submitted side taken from the FDR record as it is now (duck-typed: an FDR row and a Bank row)."""
    return Submitted(
        bank_name=bank.name if bank else None, bank_code=bank.code if bank else None, fdr_number=fdr.fdr_number,
        branch_name=fdr.branch_name, depositor_name=fdr.depositor_name,
        principal_amount=_money(fdr.principal_amount), interest_rate_pct=_money(fdr.interest_rate_pct),
        issue_date=fdr.issue_date.isoformat(), maturity_date=fdr.maturity_date.isoformat(),
    )


def submitted_from_snapshot(snapshot: dict, bank) -> Submitted:
    """The submitted side taken from a frozen revision snapshot (money already stored as strings)."""
    return Submitted(
        bank_name=bank.name if bank else None, bank_code=bank.code if bank else None,
        fdr_number=snapshot.get("fdr_number"), branch_name=snapshot.get("branch_name"),
        depositor_name=snapshot.get("depositor_name"), principal_amount=snapshot.get("principal_amount"),
        interest_rate_pct=snapshot.get("interest_rate_pct"), issue_date=snapshot.get("issue_date"),
        maturity_date=snapshot.get("maturity_date"),
    )
