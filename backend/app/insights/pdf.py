"""Renders a report as a PDF with ReportLab. No data is fetched here: callers hand over a finished `ReportSpec`.

What the document contains: the portal mark and name, the report title, when it was generated (UTC), the scope
and filters it covers, summary figures, labelled tables whose header row repeats on every page, page numbers
("Page 2 of 5") and a note about where the data comes from. It is NOT digitally signed and says nothing that
suggests it is.

Text safety. Report data (institution names, FDR numbers, depositor names ...) is untrusted: every value is
XML-escaped before it reaches a ReportLab Paragraph (which would otherwise interpret markup), and control
characters are dropped. The built-in PDF fonts only cover Western European characters, so any other character
(for example Devanagari) cannot be drawn correctly. Such characters are replaced by "?" and the document says so
in its notes, with the number of replaced characters, instead of printing garbled glyphs silently.
"""

from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

PRODUCT = "FDR & Security Deposit Portal"
NAVY = colors.HexColor("#1e3a8a")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
RULE = colors.HexColor("#cbd5e1")
BAND = colors.HexColor("#f1f5f9")

SOURCE_NOTE = (
    "Source: records held in this portal, as entered by institutions and recorded by the system. Amounts and dates are "
    "not verified with any bank, and an accepted FDR means accepted after human review of the submitted information. "
    "This document is a generated report; it is not digitally signed."
)


@dataclass
class Section:
    heading: str
    note: str | None = None
    header: list[str] | None = None
    rows: list[list] | None = None
    weights: list[float] | None = None  # relative column widths
    right: tuple[int, ...] = ()  # columns to right-align (numbers)
    empty: str = "Nothing to report."
    total_row: list | None = None


@dataclass
class ReportSpec:
    title: str
    generated_at: datetime
    scope: str
    filters: list[str] = field(default_factory=list)
    summary: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    wide: bool = False  # landscape page, for tables with many columns


class _Text:
    """Makes untrusted text safe for a Paragraph and counts what the built-in fonts cannot show."""

    def __init__(self) -> None:
        self.replaced = 0

    def __call__(self, value) -> str:
        out = []
        for ch in "" if value is None else str(value):
            if ord(ch) < 32 or ord(ch) == 127:
                out.append(" ")
                continue
            try:
                ch.encode("cp1252")
            except UnicodeEncodeError:
                self.replaced += 1
                ch = "?"
            out.append(ch)
        return escape("".join(out))


def inr(amount) -> str:
    """Indian digit grouping with an ASCII currency label ('INR 1,23,456.00'); the rupee sign is not in the fonts."""
    text = format(amount, ".2f") if not isinstance(amount, str) else amount
    whole, _, fraction = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"INR {sign}{','.join([*groups, tail])}.{(fraction + '00')[:2]}"


def stamp(when: datetime) -> str:
    return when.strftime("%d %b %Y, %H:%M UTC")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=INK, alignment=0, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=NAVY, spaceBefore=12, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10, textColor=INK),
        "cellr": ParagraphStyle("cr", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10, textColor=INK, alignment=2),
        "head": ParagraphStyle("hd", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white),
        "headr": ParagraphStyle("hdr", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white, alignment=2),
        "label": ParagraphStyle("l", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=MUTED),
        "metric": ParagraphStyle("m", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=INK),
    }


def _draw_mark(c: canvas.Canvas, x: float, y: float, size: float) -> None:
    """The portal mark: a navy tile with a page of ledger lines and a confirmation tick."""
    s = size / 48
    c.saveState()
    c.setFillColor(NAVY)
    c.roundRect(x, y, size, size, 10 * s, stroke=0, fill=1)
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(x + 14 * s, y + 8 * s, 20 * s, 31 * s, stroke=0, fill=1)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.6 * s)
    for offset, width in ((17, 14), (22, 14), (27, 8)):
        c.line(x + 17 * s, y + (48 - offset - 4) * s, x + (17 + width) * s, y + (48 - offset - 4) * s)
    c.setFillColor(colors.HexColor("#3b6fd4"))
    c.setStrokeColor(colors.HexColor("#f8fafc"))
    c.setLineWidth(1.6 * s)
    c.circle(x + 35 * s, y + 13 * s, 8 * s, stroke=1, fill=1)
    c.setLineWidth(1.8 * s)
    c.lines([(x + 31.5 * s, y + 13 * s, x + 34 * s, y + 10.5 * s), (x + 34 * s, y + 10.5 * s, x + 38.5 * s, y + 15.5 * s)])
    c.restoreState()


def _canvas_maker(spec: ReportSpec, size):
    class Numbered(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._chrome(total)
                super().showPage()
            super().save()

        def _chrome(self, total: int) -> None:
            width, height = size
            _draw_mark(self, 14 * mm, height - 22 * mm, 9 * mm)
            self.setFillColor(INK)
            self.setFont("Helvetica-Bold", 9.5)
            self.drawString(26 * mm, height - 16.5 * mm, PRODUCT)
            self.setFont("Helvetica", 8)
            self.setFillColor(MUTED)
            self.drawString(26 * mm, height - 20.5 * mm, "Report generated from portal records")
            self.drawRightString(width - 14 * mm, height - 16.5 * mm, _ascii(spec.title))
            self.setStrokeColor(RULE)
            self.setLineWidth(0.6)
            self.line(14 * mm, height - 24.5 * mm, width - 14 * mm, height - 24.5 * mm)
            self.line(14 * mm, 14 * mm, width - 14 * mm, 14 * mm)
            self.setFont("Helvetica", 7.5)
            self.drawString(14 * mm, 9.5 * mm, f"Generated {stamp(spec.generated_at)} | Not digitally signed | Demonstration data: not verified with any bank")
            self.drawRightString(width - 14 * mm, 9.5 * mm, f"Page {self._pageNumber} of {total}")

    return Numbered


def _ascii(value: str) -> str:
    return value.encode("cp1252", "replace").decode("cp1252")


def _weights(n: int, given: list[float] | None) -> list[float]:
    return given if given and len(given) == n else [1.0] * n


def _table(section: Section, text: _Text, styles, width: float) -> Table:
    header = section.header or []
    ratios = _weights(len(header), section.weights)
    widths = [width * r / sum(ratios) for r in ratios]
    data = [[Paragraph(text(h), styles["headr" if i in section.right else "head"]) for i, h in enumerate(header)]]
    for row in section.rows or []:
        data.append([Paragraph(text(v), styles["cellr" if i in section.right else "cell"]) for i, v in enumerate(row)])
    if section.total_row:
        data.append([Paragraph(f"<b>{text(v)}</b>", styles["cellr" if i in section.right else "cell"]) for i, v in enumerate(section.total_row)])
    table = Table(data, colWidths=widths, repeatRows=1, splitByRow=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1 - (1 if section.total_row else 0)), [colors.white, BAND]),
    ]
    if section.total_row:
        style.append(("LINEABOVE", (0, -1), (-1, -1), 0.8, INK))
    table.setStyle(TableStyle(style))
    return table


def render(spec: ReportSpec) -> bytes:
    """The finished PDF as bytes."""
    size = landscape(A4) if spec.wide else A4
    width = size[0] - 28 * mm
    text, styles = _Text(), _styles()
    story: list = [Paragraph(text(spec.title), styles["title"])]

    meta = [("Generated", stamp(spec.generated_at)), ("Scope", spec.scope), ("Filters", "; ".join(spec.filters) if spec.filters else "None")]
    meta_table = Table([[Paragraph(text(k), styles["label"]), Paragraph(text(v), styles["body"])] for k, v in meta], colWidths=[width * 0.14, width * 0.86])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta_table, Spacer(1, 6)]

    if spec.summary:
        cells = [[Paragraph(text(label), styles["small"]), Paragraph(text(value), styles["metric"])] for label, value in spec.summary]
        columns = min(4, len(cells))
        rows = [cells[i:i + columns] for i in range(0, len(cells), columns)]
        grid = []
        for row in rows:
            row = row + [["", ""]] * (columns - len(row))
            grid.append([[label, value] for label, value in row])
        summary = Table(grid, colWidths=[width / columns] * columns)
        summary.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, RULE), ("INNERGRID", (0, 0), (-1, -1), 0.5, RULE), ("BACKGROUND", (0, 0), (-1, -1), BAND),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story += [Paragraph("Summary", styles["h2"]), summary]

    for section in spec.sections:
        block: list = [Paragraph(text(section.heading), styles["h2"])]
        if section.note:
            block.append(Paragraph(text(section.note), styles["small"]))
            block.append(Spacer(1, 3))
        if section.header and section.rows:
            story += [KeepTogether(block)]
            story.append(_table(section, text, styles, width))
        else:
            block.append(Paragraph(text(section.empty), styles["body"]))
            story.append(KeepTogether(block))

    notes = [*spec.notes, SOURCE_NOTE]
    if text.replaced:
        notes.append(
            f"{text.replaced} character{'s' if text.replaced != 1 else ''} in the data (for example Devanagari text) cannot be shown in this "
            "PDF and were replaced by '?'. The original text is unchanged in the portal."
        )
    story += [Paragraph("Notes", styles["h2"])] + [Paragraph(text(n) if n is not SOURCE_NOTE else escape(n), styles["small"]) for n in notes]

    out = BytesIO()
    doc = BaseDocTemplate(
        out, pagesize=size, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=29 * mm, bottomMargin=18 * mm,
        title=_ascii(spec.title), author=PRODUCT, subject="Report generated from portal records", creator=PRODUCT,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame])])
    doc.build(story, canvasmaker=_canvas_maker(spec, size))
    return out.getvalue()
