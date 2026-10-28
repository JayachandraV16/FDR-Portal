"""Read-only lookups that answer data questions for FDR Copilot.

Each function takes the AUTHENTICATED database user and builds its query from `fdr_visibility(user)`, the
same rule the registry uses, so an answer can only ever be about records that user may already read. There is
no free-form query, no SQL or code from the model or the user, and nothing here writes. A record that does
not exist and a record the user may not see produce the same answer.

An `Answer` carries the text shown to the user and `llm_context`, a version with only non-sensitive facts
(counts, statuses, dates, FDR numbers): when an external language model is enabled, that is all it receives.
Amounts, names and document contents are never part of it.
"""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chat import i18n
from app.extraction.analysis import ERROR_MESSAGES
from app.extraction.compare import FIELD_LABELS
from app.extraction.summary import document_tables_ready
from app.insights import release_state
from app.insights.derive import DOCUMENT_ROLES, LIVE, REVIEW_ROLES, build_exceptions, link_for, today
from app.models import FDR, Bank, FdrStatus, Institution, User
from app.models.document import DocumentStatus, FDRDocument, FDRDocumentExtraction, FDRExtractionJob
from app.models.fdr import normalize_fdr_number
from app.security.scope import fdr_visibility

STATUS_LABELS = {
    "DRAFT": "Draft", "SUBMITTED": "Submitted (pending review)", "CORRECTION_REQUESTED": "Correction requested",
    "ACCEPTED": "Accepted", "REJECTED": "Rejected", "WITHDRAWN": "Withdrawn",
}
NOT_FOUND = "I couldn't find an FDR with that number among the records you can access."


@dataclass
class Fact:
    label: str
    value: str
    share: bool = True  # may be sent to an external language model (never amounts, names or document content)


@dataclass
class Link:
    label: str
    path: str


@dataclass
class Answer:
    text: str
    kind: str = "records"  # "records" | "help" | "unknown"
    facts: list[Fact] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    language: str = "en"  # the language the text is really in (English when no translation exists)

    @property
    def llm_context(self) -> str:
        lines = [f"{f.label}: {f.value}" for f in self.facts if f.share]
        return "\n".join(lines) if self.kind == "records" else self.text


def status_counts(db: Session, user: User) -> dict[str, int]:
    rows = db.execute(select(FDR.status, func.count()).where(fdr_visibility(user)).group_by(FDR.status)).all()
    return {status: count for status, count in rows}


def counts_answer(db: Session, user: User, status: str | None, lang: str = "en") -> Answer:
    counts = status_counts(db, user)
    total = sum(counts.values())
    if lang != "en":
        n = counts.get(status, 0) if status else total
        return Answer(
            i18n.counts_text(lang, n=n, total=total, status=status, counts=counts),
            facts=[Fact(i18n.STATUS_LABELS[lang][s], str(counts[s])) for s in STATUS_LABELS if s in counts],
            links=[Link(i18n.LINKS[lang]["registry"], "/fdrs")], language=lang,
        )
    facts = [Fact(STATUS_LABELS[s], str(counts[s])) for s in STATUS_LABELS if s in counts]
    links = [Link("Open the FDR registry", "/fdrs")]
    if status:
        n = counts.get(status, 0)
        text = f"You can see {n} FDR{'s' if n != 1 else ''} with status {STATUS_LABELS[status]}."
        if status == "SUBMITTED":
            text = f"You can see {n} FDR{'s' if n != 1 else ''} pending review (status Submitted)."
        return Answer(text, facts=facts, links=links)
    breakdown = ", ".join(f"{counts[s]} {STATUS_LABELS[s].lower()}" for s in STATUS_LABELS if s in counts)
    return Answer(f"You can see {total} FDR{'s' if total != 1 else ''}" + (f": {breakdown}." if breakdown else "."), facts=facts, links=links)


def maturing_answer(db: Session, user: User, days: int = 30, lang: str = "en") -> Answer:
    now = today()
    cond = (fdr_visibility(user), FDR.status.in_(LIVE), FDR.maturity_date >= now, FDR.maturity_date <= now + timedelta(days=days))
    total = db.scalar(select(func.count()).select_from(FDR).where(*cond)) or 0
    rows = db.execute(select(FDR).where(*cond).order_by(FDR.maturity_date, FDR.id).limit(5)).scalars().all()
    overdue = db.scalar(select(func.count()).select_from(FDR).where(fdr_visibility(user), FDR.status.in_(LIVE), FDR.maturity_date < now)) or 0
    if lang != "en":
        return Answer(
            i18n.maturing_text(lang, total=total, shown=len(rows), days=days, overdue=overdue),
            facts=[Fact(f"FDR {f.fdr_number}", i18n.matures_value(lang, f.maturity_date.isoformat(), (f.maturity_date - now).days)) for f in rows],
            links=[Link(i18n.LINKS[lang]["alerts"], "/alerts")], language=lang,
        )
    if total == 0:
        text = f"None of the FDRs you can see mature in the next {days} days."
    else:
        text = f"{total} FDR{'s' if total != 1 else ''} you can see mature within {days} days."
        if total > len(rows):
            text += f" The {len(rows)} soonest are listed."
    if overdue:
        text += f" {overdue} live FDR{'s are' if overdue != 1 else ' is'} already past maturity (see Alerts)."
    facts = [Fact(f"FDR {f.fdr_number}", f"matures {f.maturity_date.isoformat()} (in {(f.maturity_date - now).days} days)") for f in rows]
    return Answer(text, facts=facts, links=[Link("Open Alerts", "/alerts")])


def find_fdr(db: Session, user: User, token: str | None = None, fdr_id: uuid.UUID | None = None):
    """('one', (fdr, institution, bank)) | ('many', n) | ('none', 0), restricted to what `user` may see."""
    stmt = (
        select(FDR, Institution, Bank).join(Institution, Institution.id == FDR.institution_id)
        .join(Bank, Bank.id == FDR.bank_id).where(fdr_visibility(user))
    )
    if fdr_id is not None:
        stmt = stmt.where(FDR.id == fdr_id)
    elif token:
        try:
            stmt = stmt.where(FDR.fdr_number_normalized == normalize_fdr_number(token))
        except ValueError:
            return "none", 0
    else:
        return "none", 0
    rows = db.execute(stmt.limit(3)).all()
    if not rows:
        return "none", 0
    return ("one", rows[0]) if len(rows) == 1 else ("many", len(rows))


def fdr_answer(db: Session, user: User, row, lang: str = "en") -> Answer:
    fdr, inst, bank = row
    now = today()
    if lang != "en":
        return _fdr_answer_translated(db, row, now, lang)
    status = STATUS_LABELS.get(fdr.status, fdr.status)
    text = f"FDR {fdr.fdr_number} ({inst.name}) is currently {status}. It matures on {fdr.maturity_date.isoformat()}."
    if fdr.maturity_date < now and fdr.status in LIVE:
        text += " That date has passed."
    facts = [
        Fact("FDR number", fdr.fdr_number), Fact("Status", status), Fact("Maturity date", fdr.maturity_date.isoformat()),
        Fact("Institution", inst.name, share=False), Fact("Bank", bank.name, share=False),
        Fact("Principal amount", format(fdr.principal_amount, "f"), share=False),
    ]
    if fdr.status == FdrStatus.ACCEPTED.value:
        state = release_state.release_states(db, [fdr.id])[fdr.id]
        text += f" Release: {release_state.eligibility_note(fdr, now)}"
        if state != release_state.NONE:
            text += f" Release state: {state.lower()}."
        facts.append(Fact("Release state", state))
    text += " Accepted means accepted after human review; it is not bank verification." if fdr.status == "ACCEPTED" else ""
    links = [Link("Open the FDR", f"/fdrs/{fdr.id}")]
    if fdr.status == FdrStatus.ACCEPTED.value:
        links.append(Link("Release / refund", f"/release/{fdr.id}"))
    return Answer(text, facts=facts, links=links)


def extraction_answer(db: Session, user: User, row) -> Answer:
    fdr = row[0]
    if user.role not in REVIEW_ROLES:
        who = "Document analysis results are visible to AICTE reviewers and approvers only."
        extra = " You can see your documents on the Documents page." if user.role in DOCUMENT_ROLES else ""
        return Answer(who + extra, links=[Link("Documents", f"/documents/{fdr.id}")] if user.role in DOCUMENT_ROLES else [])
    if not document_tables_ready(db):
        return Answer("Document analysis is not available on this server yet: the document and extraction database migrations have not been applied.")
    docs = db.execute(select(FDRDocument.id, FDRDocument.original_filename).where(
        FDRDocument.fdr_id == fdr.id, FDRDocument.status == DocumentStatus.ACTIVE.value)).all()
    if not docs:
        return Answer(f"FDR {fdr.fdr_number} has no documents, so there is nothing to analyse.", links=[Link("Documents", f"/documents/{fdr.id}")])
    lines, facts = [], []
    for doc_id, name in docs:
        job = db.scalars(select(FDRExtractionJob).where(FDRExtractionJob.document_id == doc_id)
                         .order_by(FDRExtractionJob.requested_at.desc()).limit(1)).first()
        extraction = db.scalars(select(FDRDocumentExtraction).where(FDRDocumentExtraction.document_id == doc_id)
                                .order_by(FDRDocumentExtraction.requested_at.desc()).limit(1)).first()
        if job is not None and job.status in ("PENDING", "RUNNING"):
            state, why = "a check is in progress", None
        elif extraction is not None and extraction.status == "COMPLETED":
            state, why = "analysed", None
        elif (job is not None and job.status == "FAILED") or (extraction is not None and extraction.status == "FAILED"):
            code = (job.error_code if job is not None and job.status == "FAILED" else extraction.error_code)
            state, why = "the check failed", ERROR_MESSAGES.get(code, "The check could not be completed.")
        else:
            state, why = "not analysed yet", None
        lines.append(f"'{name}': {state}." + (f" {why}" if why else ""))
        facts.append(Fact(f"Document {len(facts) + 1}", state + (f" ({code})" if why else ""), share=True))
    return Answer(f"Document analysis for FDR {fdr.fdr_number}: " + " ".join(lines) + " A reviewer can retry a failed check on the Verification page.",
                  facts=facts, links=[Link("Open verification", f"/verification/{fdr.id}")])


def exceptions_answer(db: Session, user: User, row=None) -> Answer:
    result = build_exceptions(db, user)
    scope = ""
    if not result.complete:
        scope = f" Only {result.scanned} of {result.eligible_total} open records were examined, so this may be incomplete."
    if row is not None:
        fdr = row[0]
        mine = [e for e in result.items if e.fdr_id == fdr.id]
        if not mine:
            return Answer(f"No exceptions were found for FDR {fdr.fdr_number} in the records examined.{scope}", links=[Link("Open Exceptions", "/exceptions")])
        text = f"FDR {fdr.fdr_number} has {len(mine)} exception{'s' if len(mine) != 1 else ''}: " + "; ".join(e.issue for e in mine) + "." + scope
        return Answer(text, facts=[Fact(e.kind.replace("_", " ").title(), e.severity.title()) for e in mine],
                      links=[Link("Open the FDR", link_for(user, fdr.id)), Link("Open Exceptions", "/exceptions")])
    if not result.items:
        return Answer("No exceptions were found in the records examined." + scope, links=[Link("Open Exceptions", "/exceptions")])
    by_kind: dict[str, int] = {}
    for e in result.items:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    facts = [Fact(next((k.label for k in result.kinds if k.key == key), key), str(n)) for key, n in by_kind.items()]
    top = result.items[:5]
    text = (f"{len(result.items)} exception{'s' if len(result.items) != 1 else ''} found. Most serious first: "
            + "; ".join(f"FDR {e.fdr_number} ({e.severity.lower()}): {e.issue}" for e in top) + "." + scope)
    return Answer(text, facts=facts, links=[Link("Open Exceptions", "/exceptions")])


def field_names(names) -> str:
    return ", ".join(FIELD_LABELS.get(n, n).lower() for n in names)


def _fdr_answer_translated(db: Session, row, now, lang: str) -> Answer:
    """The same facts as fdr_answer, worded in Hindi or Marathi. Numbers, dates and the FDR number are unchanged."""
    fdr, inst, bank = row
    labels, facts_l = i18n.STATUS_LABELS[lang], i18n.FACT_LABELS[lang]
    status = labels.get(fdr.status, fdr.status)
    text = i18n.fdr_text(lang, number=fdr.fdr_number, institution=inst.name, status=status, date_iso=fdr.maturity_date.isoformat(),
                         passed=fdr.maturity_date < now and fdr.status in LIVE)
    facts = [
        Fact(facts_l["FDR number"], fdr.fdr_number), Fact(facts_l["Status"], status), Fact(facts_l["Maturity date"], fdr.maturity_date.isoformat()),
        Fact(facts_l["Institution"], inst.name, share=False), Fact(facts_l["Bank"], bank.name, share=False),
        Fact(facts_l["Principal amount"], format(fdr.principal_amount, "f"), share=False),
    ]
    links = [Link(i18n.LINKS[lang]["fdr"], f"/fdrs/{fdr.id}")]
    if fdr.status == FdrStatus.ACCEPTED.value:
        state = release_state.release_states(db, [fdr.id])[fdr.id]
        note = i18n.release_note(lang, accepted=True, matured=fdr.maturity_date <= now, date_iso=fdr.maturity_date.isoformat())
        text += i18n.release_line(lang, note, None if state == release_state.NONE else i18n.RELEASE_STATES[lang].get(state, state))
        facts.append(Fact(facts_l["Release state"], i18n.RELEASE_STATES[lang].get(state, state)))
        text += i18n.ACCEPTED_FOOTER[lang]
        links.append(Link(i18n.LINKS[lang]["release"], f"/release/{fdr.id}"))
    return Answer(text, facts=facts, links=links, language=lang)
