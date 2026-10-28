"""Finding FDR fields in extracted text. Deterministic, label-driven, and conservative.

A value is only reported when it follows an explicit label in the text ("Maturity Date: ...",
"परिपक्वता तिथि: ...", "मुदतपूर्ती दिनांक: ...") or, for the bank name only, appears as a header line.
Nothing is inferred, defaulted or filled in. English, Hindi and Marathi labels are recognised side by
side; adding a language only adds alternatives to the same patterns.

Statuses per field:
  EXTRACTED  exactly one distinct value was found
  AMBIGUOUS  several distinct values were found, or the single value can be read more than one way
             (for example 03/04/2026 is 3 April or 4 March); candidates are listed, `value` is empty
  NOT_FOUND  no labelled value was found

Confidence is a category, not a probability:
  HIGH    a recognised label and its value on the same line of embedded PDF text
  MEDIUM  the value is on the line after its label, or it is a header-line bank name; or a HIGH
          finding that came from OCR (recognition can misread characters)
  LOW     a MEDIUM finding that came from OCR
A field with several corroborating occurrences keeps the highest confidence among them.

Text handling: lines are normalised to Unicode NFC and stripped of zero-width spaces and byte-order
marks (invisible characters PDF text often carries); nothing else is changed. `value` is the parsed
value (an exact decimal, an ISO date) and `raw` is the text exactly as printed, so a value printed
in Devanagari digits is shown as printed while still being parsed. Identifiers and names are never
transliterated or converted: their `value` is the printed text.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.extraction.types import PageText, TextSource

FIELD_NAMES = (
    "bank_name", "branch_name", "fdr_number", "depositor_name",
    "principal_amount", "currency", "issue_date", "maturity_date", "interest_rate_pct",
)

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
_DOWNGRADE = {HIGH: MEDIUM, MEDIUM: LOW, LOW: LOW}
_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}

MAX_TEXT_VALUE = 120
DEVANAGARI = "ऀ-॥॰-ॿ"  # the script block without its own digits
DEVANAGARI_DIGITS = "०-९"
_DIGIT_TABLE = {0x0966 + i: ord("0") + i for i in range(10)}


def ascii_digits(text: str) -> str:
    """Devanagari digits as ASCII digits, character for character (so offsets are unchanged)."""
    return text.translate(_DIGIT_TABLE)


@dataclass
class Candidate:
    value: str
    page: int
    confidence: str
    source: str
    note: str | None = None
    currency: str | None = None
    raw: str | None = None


@dataclass
class ExtractedField:
    name: str
    status: str  # EXTRACTED | AMBIGUOUS | NOT_FOUND
    value: str | None = None
    currency: str | None = None
    confidence: str | None = None
    page: int | None = None
    source: str | None = None
    candidates: list[dict] = field(default_factory=list)
    note: str | None = None
    raw: str | None = None  # the value exactly as printed (differs from `value` for digits, dates, amounts)


# ---- value parsers ------------------------------------------------------------------------------
_CURRENCIES = {"rs": "INR", "inr": "INR", "₹": "INR", "रु": "INR", "रू": "INR", "रुपये": "INR", "रुपए": "INR",
               "usd": "USD", "us$": "USD", "$": "USD", "eur": "EUR", "€": "EUR", "gbp": "GBP", "£": "GBP"}
_AMOUNT = re.compile(
    r"(?P<cur>Rs\.?|INR|₹|रुपये|रुपए|रु\.?|रू\.?|USD|US\$|\$|EUR|€|GBP|£)?\s?(?P<num>\d[\d,]{0,24}(?:\.\d{1,8})?)(?![\d,])"
    r"(?:\s*(?P<suf>रुपये|रुपए|रु\.?|रू\.?|Rs\.?|INR)(?![A-Za-z" + DEVANAGARI + r"]))?",
    re.IGNORECASE,
)
# Western (1,500,000.00), Indian (15,00,000.00) or ungrouped (1500000.00) digits, at most two decimals.
_PLAIN = re.compile(r"^\d{1,15}(\.\d{1,2})?$")
_WESTERN = re.compile(r"^\d{1,3}(,\d{3})+(\.\d{1,2})?$")
_INDIAN = re.compile(r"^\d{1,2}(,\d{2})*,\d{3}(\.\d{1,2})?$")


def _parse_amount(text: str) -> tuple[Decimal, str | None, int] | None:
    translated = ascii_digits(text.strip())
    m = _AMOUNT.match(translated)
    if not m:
        return None
    number = m.group("num")
    if not (_PLAIN.match(number) or _WESTERN.match(number) or _INDIAN.match(number)):
        return None
    try:
        amount = Decimal(number.replace(",", ""))
    except InvalidOperation:
        return None
    marker = m.group("cur") or m.group("suf")
    return amount, (_CURRENCIES.get(marker.rstrip(".").lower()) if marker else None), m.end()


def parse_amount(text: str) -> tuple[Decimal, str | None] | None:
    """(amount, currency or None) from the start of `text`, or None if it is not an unambiguous
    amount. Never rounds: more than two decimals, odd digit grouping or a missing number all fail."""
    parsed = _parse_amount(text)
    return None if parsed is None else (parsed[0], parsed[1])


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_ENGLISH_MONTH_NAMES = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
                        "sept", "october", "november", "december", *_MONTHS}
# Hindi and Marathi month names as printed on receipts; spelling variants are listed explicitly, never
# guessed. Keys are stored in NFC so a decomposed nukta (फ + ़) matches too.
_DEVANAGARI_MONTHS = {
    1: ["जनवरी", "जानेवारी"], 2: ["फरवरी", "फ़रवरी", "फेब्रुवारी"], 3: ["मार्च"], 4: ["अप्रैल", "अप्रेल", "एप्रिल"],
    5: ["मई", "मे"], 6: ["जून"], 7: ["जुलाई", "जुलै"], 8: ["अगस्त", "ऑगस्ट", "ऑगस्ट"],
    9: ["सितंबर", "सितम्बर", "सप्टेंबर", "सप्टेम्बर"], 10: ["अक्टूबर", "अक्तूबर", "ऑक्टोबर", "ऑक्टोंबर"],
    11: ["नवंबर", "नवम्बर", "नोव्हेंबर", "नोव्हेम्बर"], 12: ["दिसंबर", "दिसम्बर", "डिसेंबर", "डिसेम्बर"],
}
_DEVANAGARI_MONTH_LOOKUP = {
    unicodedata.normalize("NFC", name): number for number, names in _DEVANAGARI_MONTHS.items() for name in names
}
_NAME = r"[A-Za-z" + DEVANAGARI + r"]{2,12}"
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?!\d)")
_NUMERIC = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)")
_DAY_MONTH_NAME = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*(" + _NAME + r")[\s\-/.,]*(\d{4})(?!\d)")
_MONTH_NAME_DAY = re.compile(r"^(" + _NAME + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)")


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month(name: str) -> int | None:
    folded = unicodedata.normalize("NFC", name)
    if folded in _DEVANAGARI_MONTH_LOOKUP:
        return _DEVANAGARI_MONTH_LOOKUP[folded]
    return _MONTHS.get(name[:3].lower()) if name.lower() in _ENGLISH_MONTH_NAMES else None


def _parse_date(text: str) -> tuple[tuple[date, ...], int] | None:
    t = ascii_digits(text.strip())
    if m := _ISO.match(t):
        d = _valid(int(m[1]), int(m[2]), int(m[3]))
        return ((d,), m.end()) if d else None
    if m := _NUMERIC.match(t):
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        readings = {d for d in (_valid(year, b, a), _valid(year, a, b)) if d}
        return (tuple(sorted(readings)), m.end()) if readings else None
    if m := _DAY_MONTH_NAME.match(t):
        month = _month(m[2])
        d = _valid(int(m[3]), month, int(m[1])) if month else None
        return ((d,), m.end()) if d else None
    if m := _MONTH_NAME_DAY.match(t):
        month = _month(m[1])
        d = _valid(int(m[3]), month, int(m[2])) if month else None
        return ((d,), m.end()) if d else None
    return None


def parse_date(text: str) -> tuple[date, ...] | None:
    """The calendar date(s) `text` can mean: one for ISO and named-month forms (English, Hindi or
    Marathi), and for numeric forms where one reading is impossible (a day above 12) or both readings
    agree; two when a numeric date is genuinely ambiguous. Two-digit years and invalid dates return
    None. Never guesses."""
    parsed = _parse_date(text)
    return None if parsed is None else parsed[0]


_RATE = re.compile(
    r"^(\d{1,2}(?:\.\d{1,2})?)\s*(?:%|per\s*cent|p\.?\s?a\b|प्रतिशत|टक्के|टक्का|प्रति\s*वर्ष|प्र\.\s?व)",
    re.IGNORECASE,
)
_NUMBER_TOKEN = re.compile(r"^[A-Za-z0-9" + DEVANAGARI_DIGITS + r"][A-Za-z0-9" + DEVANAGARI_DIGITS + r"/\-]{2,39}")


def _parse_rate(text: str) -> tuple[Decimal, int] | None:
    m = _RATE.match(ascii_digits(text.strip()))
    if not m:
        return None
    rate = Decimal(m.group(1))
    return (rate, m.end()) if rate <= 100 else None


def parse_rate(text: str) -> Decimal | None:
    """An interest rate only when it is explicitly marked (%, 'per cent', 'p.a.', or the Hindi and
    Marathi equivalents प्रतिशत / टक्के / प्रति वर्ष)."""
    parsed = _parse_rate(text)
    return None if parsed is None else parsed[0]


def parse_identifier(text: str) -> str | None:
    """An FDR/receipt number exactly as printed (trailing punctuation removed, digits NOT converted).
    It must contain a digit; no case folding, padding or separator changes are applied."""
    m = _NUMBER_TOKEN.match(text.strip())
    if not m:
        return None
    token = m.group(0).rstrip("-/")
    return token if re.search(r"[0-9" + DEVANAGARI_DIGITS + r"]", token) and len(token) >= 3 else None


_TEXT_END = re.compile(r"\s{2,}|\t|\s[|]\s")


def _cut_at_next_label(value: str) -> str:
    """End a value where the next known field label (followed by a colon) begins on the same line, so
    one value never runs into the next field. A name that merely contains a label word is untouched."""
    cut = len(value)
    for pattern in LABELS.values():
        for m in pattern.finditer(value):
            if m.start() > 0 and re.match(r"\s*:", value[m.end():]):
                cut = min(cut, m.start())
    return value[:cut]


def parse_text_value(text: str) -> str | None:
    """A free-text value: the rest of the line up to a wide gap or the next "Label:", trimmed. Blank,
    label-like or very long values are rejected rather than guessed at. Any script is kept as printed."""
    value = _cut_at_next_label(_TEXT_END.split(text.strip(), maxsplit=1)[0])
    value = value.strip(" :-–—.,;")
    if len(value) < 2 or len(value) > MAX_TEXT_VALUE or value.endswith(":"):
        return None
    return re.sub(r"\s+", " ", value)


# ---- labels -------------------------------------------------------------------------------------
# Boundaries are spelled out because Python's \b treats Devanagari vowel signs (combining marks) as
# non-word characters and would split words in the middle.
_BEFORE = r"(?<![\w" + DEVANAGARI + r"])"
_AFTER = r"(?![A-Za-z" + DEVANAGARI + r"])"


def _label(english: str, localized: str = "") -> re.Pattern:
    alternatives = english if not localized else f"{english}|{localized}"
    return re.compile(rf"{_BEFORE}(?:{alternatives}){_AFTER}", re.IGNORECASE)


_NUMBER_WORD = r"(?:क्रमांक|क्र\.?|संख्या|सं\.?|नंबर|क्रं\.?)"  # "number" in Hindi and Marathi

LABELS: dict[str, re.Pattern] = {
    "bank_name": _label(
        r"Bank\s+Name|Name\s+of\s+(?:the\s+)?Bank|Issuing\s+Bank",
        r"बैंक\s+का\s+नाम|बैंक\s+नाम|जारीकर्ता\s+बैंक|बँकेचे\s+नाव|बँकेचं\s+नाव|बँकेचे\s+नांव|बँक\s+नाव|बँकेचे\s+नाम",
    ),
    "branch_name": _label(
        r"Branch(?:\s+Name)?(?!\s*(?:Code|No\b|IFSC))",
        r"शाखा(?:\s+का\s+नाम|\s+नाम)?(?!\s*(?:कोड|क्र|क्रमांक|संख्या|IFSC))|शाखेचे\s+नाव|शाखेचं\s+नाव",
    ),
    "fdr_number": _label(
        r"FDR\s*(?:A/c\.?\s*)?(?:No\.?|Number)|F\.?D\.?R\.?\s*(?:No\.?|Number)|FD\s*(?:A/c\.?\s*)?(?:No\.?|Number)|"
        r"(?:Deposit\s+)?Receipt\s*(?:No\.?|Number)|Deposit\s*(?:No\.?|Number)|Certificate\s*(?:No\.?|Number)",
        r"(?:एफ\.?\s?डी\.?\s?आर\.?|एफडीआर|सावधि\s+जमा\s+रसीद|मुदत\s+ठेव\s+पावती|जमा\s+रसीद|ठेव\s+पावती|रसीद|पावती|प्रमाणपत्र|ठेव)"
        r"\s*" + _NUMBER_WORD,
    ),
    "depositor_name": _label(
        r"Name\s+of\s+(?:the\s+)?(?:Depositor|Customer|Account\s+Holder)|Depositor(?:'s)?(?:\s+Name)?|"
        r"Customer\s+Name|Account\s+Holder(?:\s+Name)?|Received\s+from|Issued\s+to|Deposited\s+by",
        r"जमाकर्ता(?:\s+का\s+नाम|\s+नाम)?|जमाकर्ता\s+का\s+नाम|ग्राहक\s+का\s+नाम|खाताधारक(?:\s+का\s+नाम)?|"
        r"ठेवीदाराचे\s+नाव|ठेवीदार|ग्राहकाचे\s+नाव|खातेदाराचे\s+नाव|खातेदार|द्वारा\s+जमा",
    ),
    "principal_amount": _label(
        r"Principal(?:\s+Amount)?|Deposit\s+Amount|Amount\s+of\s+(?:the\s+)?Deposit|Amount\s+Deposited|Deposited\s+Amount",
        r"मूल\s+राशि|मूलधन|जमा\s+राशि|जमा\s+की\s+गई\s+राशि|जमा\s+रकम|मुद्दल\s+रक्कम|मुद्दल|ठेव\s+रक्कम|जमा\s+रक्कम|ठेवीची\s+रक्कम",
    ),
    "issue_date": _label(
        r"Date\s+of\s+(?:Deposit|Issue|Opening)|Deposit\s+Date|Issue\s+Date|Opening\s+Date|Value\s+Date|Start\s+Date",
        r"जमा\s+की\s+(?:तारीख|तिथि)|जमा\s+(?:तिथि|तारीख|दिनांक)|जारी\s+करने\s+की\s+(?:तारीख|तिथि)|जारी\s+(?:तिथि|दिनांक|तारीख)|"
        r"खाता\s+खोलने\s+की\s+(?:तारीख|तिथि)|प्रारंभ\s+(?:तिथि|दिनांक)|मूल्य\s+(?:तिथि|दिनांक)|"
        r"ठेव\s+(?:दिनांक|तारीख)|ठेवीचा\s+दिनांक|सुरुवात\s+दिनांक|खाते\s+उघडल्याचा\s+दिनांक",
    ),
    "maturity_date": _label(
        r"Maturity\s+Date|Date\s+of\s+Maturity|Maturing\s+on|Due\s+Date",
        r"परिपक्वता\s+(?:की\s+)?(?:तिथि|तारीख|दिनांक)|देय\s+(?:तिथि|तारीख|दिनांक)|"
        r"मुदतपूर्ती(?:चा)?\s+(?:दिनांक|तारीख)|मुदत\s+संपण्याचा\s+दिनांक|मुदतपूर्ती\s+दिनांक",
    ),
    "interest_rate_pct": _label(
        r"Rate\s+of\s+Interest|Interest\s+Rate|Rate\s+of\s+Int\.?|ROI|Interest",
        r"ब्याज\s+(?:की\s+)?दर|ब्याज\s+दर|ब्याज|व्याजाचा\s+दर|व्याज\s+दर|व्याजदर|व्याज",
    ),
}
_SEPARATORS = " \t:-–—.=।"
_HEADER_BANK = re.compile(
    r"^[A-Za-z" + DEVANAGARI + r"&.,'() \-]{0,40}(?:\bBank\b|बैंक|बँक)[A-Za-z" + DEVANAGARI + r"&.,'() \-]{0,40}$",
    re.IGNORECASE,
)
_HEADER_LINES = 8
_NOT_A_BANK_NAME = re.compile(
    r"receipt|copy|deposit|account|statement|certificate|customer|branch|depositor|passbook|advice|notice|"
    r"रसीद|पावती|प्रति|जमा|खाता|प्रमाणपत्र|शाखा|ग्राहक|ठेव|सूचना|विवरण",
    re.IGNORECASE,
)
_MATURITY_LINE = re.compile(r"maturity|परिपक्व|मुदतपूर्ती|मॅच्युरिटी|देय", re.IGNORECASE)


@dataclass
class _Line:
    page: int
    source: str
    text: str
    index: int  # position among the page's non-empty lines


def normalise_text(text: str) -> str:
    """NFC, no zero-width spaces or byte-order marks, plain spaces. Nothing else is changed."""
    text = unicodedata.normalize("NFC", text)
    return text.replace("​", "").replace("﻿", "").replace("\xa0", " ").replace("\r", "\n")


def _lines(pages: list[PageText]) -> list[_Line]:
    out: list[_Line] = []
    for page in pages:
        non_empty = [ln.rstrip() for ln in normalise_text(page.text).split("\n") if ln.strip()]
        out.extend(_Line(page.page, page.source, ln, i) for i, ln in enumerate(non_empty))
    return out


def _confidence(base: str, source: str) -> str:
    return _DOWNGRADE[base] if source == TextSource.OCR else base


def _after_label(lines: list[_Line], position: int, match: re.Match) -> tuple[str, bool]:
    """The text following a label on the same line, else the next line of the same page."""
    rest = lines[position].text[match.end():].lstrip(_SEPARATORS)
    if rest.strip():
        return rest, False
    nxt = lines[position + 1] if position + 1 < len(lines) else None
    if nxt is not None and nxt.page == lines[position].page:
        return nxt.text.strip(), True
    return "", False


def _starts_with_label(text: str) -> bool:
    return any(pattern.match(text.strip()) for pattern in LABELS.values())


def _candidates(name: str, lines: list[_Line]) -> list[Candidate]:
    out: list[Candidate] = []
    label = LABELS[name]
    for position, line in enumerate(lines):
        if name == "principal_amount" and _MATURITY_LINE.search(line.text):
            continue
        # Only the first occurrence of a field's label on a line is its label; a later occurrence is
        # part of the value (for example the branch "Main Branch").
        for match in filter(None, [label.search(line.text)]):
            rest, next_line = _after_label(lines, position, match)
            if not rest or _starts_with_label(rest):
                continue
            if next_line and line.text[: match.start()].strip():
                continue  # a label in the middle of a sentence does not claim the following line
            base = MEDIUM if next_line else HIGH
            confidence = _confidence(base, line.source)
            printed = rest.strip()
            if name == "principal_amount":
                parsed = _parse_amount(rest)
                if parsed is None:
                    out.append(Candidate("", line.page, confidence, line.source, note="unreadable_amount"))
                else:
                    out.append(Candidate(format(parsed[0], "f"), line.page, confidence, line.source,
                                         currency=parsed[1], raw=printed[: parsed[2]].strip()))
            elif name in ("issue_date", "maturity_date"):
                parsed_date = _parse_date(rest)
                if parsed_date is None:
                    out.append(Candidate("", line.page, confidence, line.source, note="unreadable_date"))
                else:
                    dates, end = parsed_date
                    out.append(Candidate("|".join(d.isoformat() for d in dates), line.page, confidence, line.source,
                                         raw=printed[:end].strip()))
            elif name == "interest_rate_pct":
                parsed_rate = _parse_rate(rest)
                if parsed_rate is not None:
                    out.append(Candidate(format(parsed_rate[0], "f"), line.page, confidence, line.source,
                                         raw=printed[: parsed_rate[1]].strip()))
            elif name == "fdr_number":
                token = parse_identifier(rest)
                if token:
                    out.append(Candidate(token, line.page, confidence, line.source, raw=token))
            else:
                value = parse_text_value(rest)
                if value:
                    out.append(Candidate(value, line.page, confidence, line.source, raw=value))
    if name == "bank_name":
        for line in lines:
            if line.page == lines[0].page and line.index < _HEADER_LINES and _HEADER_BANK.match(line.text.strip()) \
                    and not LABELS["bank_name"].search(line.text) and ":" not in line.text \
                    and not _NOT_A_BANK_NAME.search(line.text):
                value = parse_text_value(line.text) or ""
                out.append(Candidate(value, line.page, _confidence(MEDIUM, line.source), line.source,
                                     note="header_line", raw=value or None))
    return [c for c in out if c.value or c.note]


def _same_text(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", a).strip().casefold() == re.sub(r"\s+", " ", b).strip().casefold()


def _resolve(name: str, candidates: list[Candidate]) -> ExtractedField:
    if not candidates:
        return ExtractedField(name, "NOT_FOUND")
    readable = [c for c in candidates if c.value]
    if not readable:
        return ExtractedField(name, "AMBIGUOUS", note=candidates[0].note, candidates=[])

    # Merge repeated occurrences of the same value; keep the best confidence and earliest page.
    merged: list[Candidate] = []
    for c in readable:
        existing = next((m for m in merged if _same_text(m.value, c.value) and m.currency == c.currency), None)
        if existing is None:
            merged.append(Candidate(c.value, c.page, c.confidence, c.source, c.note, c.currency, c.raw))
        elif _RANK[c.confidence] > _RANK[existing.confidence]:
            existing.confidence, existing.source = c.confidence, c.source
    unreadable = len(readable) != len(candidates)

    if len(merged) == 1 and "|" not in merged[0].value and not unreadable:
        c = merged[0]
        return ExtractedField(name, "EXTRACTED", c.value, c.currency, c.confidence, c.page, c.source, note=c.note, raw=c.raw)

    listed: list[dict] = []
    for c in merged:
        for reading in c.value.split("|"):
            listed.append({"value": reading, "page": c.page})
    note = "multiple_values"
    raw = None
    if len(merged) == 1 and "|" in merged[0].value:
        note, raw = "date_order_ambiguous", merged[0].raw
    elif unreadable and len(merged) == 1:
        note = "some_occurrences_unreadable"
    return ExtractedField(name, "AMBIGUOUS", candidates=listed, note=note, source=None, raw=raw)


def extract_fields(pages: list[PageText]) -> list[ExtractedField]:
    """All fields, in a fixed order. `currency` is derived from the principal amount's marker."""
    lines = _lines(pages)
    by_name = {name: _resolve(name, _candidates(name, lines)) for name in FIELD_NAMES if name != "currency"}
    principal = by_name["principal_amount"]
    if principal.status == "EXTRACTED" and principal.currency:
        by_name["currency"] = ExtractedField("currency", "EXTRACTED", principal.currency, None, principal.confidence,
                                             principal.page, principal.source)
    elif principal.status == "EXTRACTED":
        by_name["currency"] = ExtractedField("currency", "NOT_FOUND", note="no_currency_marker_next_to_amount")
    else:
        by_name["currency"] = ExtractedField("currency", "NOT_FOUND")
    return [by_name[name] for name in FIELD_NAMES]
