"""What FDR Copilot knows without looking at anyone's records: how the portal works.

Every statement here describes behaviour the application actually has; if a rule changes in the code, the
matching text changes here. Nothing in this module reads data. Matching is by simple keywords on purpose:
it is predictable, testable and never invents an answer, and a question that matches nothing gets an honest
"I can't answer that" instead of a guess.
"""

import re
from dataclasses import dataclass

from app.chat import i18n
from app.models import Role

NOT_VERIFIED = (
    "Nothing in this portal is verified with a bank. Extracted values and comparisons are a reading aid for a "
    "human reviewer, and an accepted FDR means it was accepted after human review of the submitted information."
)


@dataclass(frozen=True)
class Topic:
    key: str
    keywords: tuple[str, ...]  # any of these (as whole words or phrases) selects the topic
    text: str


TOPICS: tuple[Topic, ...] = (
    Topic("create_fdr", ("create an fdr", "create fdr", "add an fdr", "new fdr", "enter an fdr", "register an fdr", "submit an fdr", "how do i create"), (
        "To create an FDR (institution users): open FDRs, choose Create FDR, pick the bank, enter the FDR number, "
        "principal amount, issue date and maturity date (branch, depositor, interest rate and purpose can be added now or "
        "later), and save. It is saved as a draft that only your institution can see. Attach documents while it is a draft "
        "(Documents page), then use Submit for review. Once submitted it can no longer be edited unless a reviewer returns it "
        "for correction. The server checks every field again, and rejects a duplicate FDR number for the same bank."
    )),
    Topic("documents_required", ("which documents", "documents are required", "required documents", "what documents", "documents do i need", "document types", "file types", "file size", "supported format", "upload a document"), (
        "The portal does not block submission when no document is attached, but reviewers expect supporting documents: the "
        "FDR certificate or receipt, proof of the security deposit, and any supporting letter. Accepted files are PDF, JPEG and "
        "PNG, up to 10 MB each, with at most 20 documents per FDR; other types (for example HTML, SVG, ZIP or executables) are "
        "refused, and the file's contents must match its type. Documents can only be added or removed while the FDR is a "
        "draft or has been returned for correction."
    )),
    Topic("verification_process", ("verification process", "how does verification", "verify", "verified", "extraction", "extract", "comparison", "compare"), (
        "A reviewer can run a document check, which reads values (bank, branch, FDR number, depositor, amount, dates, rate) "
        "from the uploaded file and compares each with the submitted record. Each field is a MATCH, MISMATCH, NOT_FOUND "
        "(nothing was found in the document), AMBIGUOUS (several readings, such as a date that could be day/month or "
        "month/day) or NOT_COMPARABLE (no submitted value, or a different currency). Only a MATCH is agreement; the others "
        "need a person. Text read by OCR can contain mistakes and is shown with lower confidence. " + NOT_VERIFIED
    )),
    Topic("statuses", ("status mean", "statuses", "what does draft", "what does submitted", "what does accepted", "what does rejected", "what does withdrawn", "what is draft", "fdr status", "status of fdr"), (
        "FDR statuses: Draft (being prepared by the institution, not visible to AICTE); Submitted (waiting for AICTE review, "
        "also called pending review); Correction requested (a reviewer returned it with a comment; the institution can edit and "
        "resubmit); Accepted (accepted after human review of the submitted information, which is not bank verification); "
        "Rejected (declined by an approver, with a reason); Withdrawn (no longer active). " + NOT_VERIFIED
    )),
    Topic("pending_review", ("pending review", "awaiting review", "waiting for review", "under review"), (
        "Pending review means the FDR is Submitted: the institution has finished it and it is waiting in the AICTE review "
        "queue. A reviewer or approver may request corrections; only an approver can accept or reject it."
    )),
    Topic("exceptions", ("resolve an exception", "resolve exception", "exception mean", "what is an exception", "exceptions", "handle an exception", "mismatch"), (
        "Exceptions are problems found in the records: a document that differs from the submitted record, ambiguous or failed "
        "document reading, a missing document, an incomplete record, or an FDR past maturity with no approved release. They are "
        "worked out from the current records each time, so there is nothing to acknowledge or dismiss: an exception goes away "
        "when its cause is fixed (for example by correcting the record, uploading a document while it is editable, or an "
        "approved release). Open the linked FDR to act on it."
    )),
    Topic("alerts", ("alerts", "alert mean", "what is an alert", "notifications"), (
        "Alerts are computed from current records each time you open the page: upcoming maturities (within 30 days), FDRs past "
        "maturity without an approved release, FDRs awaiting review, corrections requested, release requests waiting for a "
        "decision, and failed document processing. They are not stored or sent as notifications."
    )),
    Topic("release", ("release", "refund", "ineligible", "not eligible", "eligible for release"), (
        "Release / refund is a request-and-approval workflow, not a payment. An FDR is eligible when it is Accepted and has "
        "reached its maturity date. The institution requests release; an AICTE approver approves or rejects it (a rejection "
        "records a reason and the institution may ask again). An FDR cannot be released if it is not accepted, has not matured, "
        "already has an open request, or has already been approved. Approval is recorded in the audit trail under the "
        "approver's name; this system does not transfer funds."
    )),
    Topic("navigation", ("where do i", "how do i find", "navigate", "where can i", "menu", "sidebar", "which page"), (
        "Use the sidebar: Overview (summary), Institutions, FDRs (registry; open a record for its details), Bulk import (institution users), Calendar (maturity "
        "dates), Review queue (reviewers and approvers), Workload (reviewers, approvers and administrators), Documents, "
        "Verification (reviewers), Alerts, Exceptions, Release / Refund, Reports, Security (your own sign-in sessions) and, for "
        "administrators, Administration. You only see what your role can use."
    )),
    Topic("roles", ("my role", "what can i do", "permissions", "who can", "roles"), (
        "Roles: institution users create, edit and submit their own institution's FDRs and request release; AICTE reviewers "
        "inspect submissions and can request corrections; AICTE approvers can also accept, reject and decide release requests; "
        "administrators manage user accounts; auditors have read-only access. Everyone only sees records their role permits."
    )),
    Topic("validation", ("validation error", "why was my fdr rejected", "duplicate fdr", "already exists", "invalid", "error when saving", "cannot save"), (
        "Common reasons a save or submit is refused: the maturity date is not after the issue date; the principal amount is zero "
        "or has more than two decimals; the interest rate is outside 0 to 100; the bank is not chosen; the FDR number is blank; "
        "branch and depositor are missing at submission; another live FDR already uses that number at the same bank (409); or "
        "the record changed since you opened it (reload and retry). The message under the field says which."
    )),
    Topic("extraction_errors", ("extraction fail", "extraction is failing", "ocr", "could not be analysed", "document check fail", "why is extraction"), (
        "A document check can fail for a clear reason: the file is damaged, password-protected, too long (over 10 pages) or too "
        "large; it is a scan and text recognition (OCR) is not available on the server, or its language data is missing; the "
        "analysis timed out; or the stored file changed or went missing. The reason is shown on the document check, and a "
        "reviewer can retry it."
    )),
)

SUGGESTIONS_BY_PAGE: dict[str, tuple[str, ...]] = {
    "overview": ("How many of my FDRs are pending review?", "Which FDRs are approaching maturity?", "What do the FDR statuses mean?"),
    "fdrs": ("How do I create an FDR?", "What does pending review mean?", "What do the FDR statuses mean?"),
    "documents": ("Which documents are required?", "Why is extraction failing?", "Explain the FDR verification process."),
    "verification": ("Explain the FDR verification process.", "Why is extraction failing?", "What does AMBIGUOUS mean?"),
    "review": ("What does pending review mean?", "Explain the FDR verification process.", "How many FDRs are pending review?"),
    "alerts": ("What do alerts mean?", "Which FDRs are approaching maturity?", "How do I resolve an exception?"),
    "exceptions": ("How do I resolve an exception?", "Explain the FDR verification process.", "Which FDRs have exceptions?"),
    "release": ("Why might an FDR be ineligible for release?", "How does release approval work?", "Which FDRs are approaching maturity?"),
    "reports": ("What do the FDR statuses mean?", "Which FDRs are approaching maturity?", "How many of my FDRs are pending review?"),
    "calendar": ("Which FDRs are approaching maturity?", "What do alerts mean?", "How does release approval work?"),
    "workload": ("What does pending review mean?", "How many FDRs are pending review?", "Explain the FDR verification process."),
    "import": ("How do I create an FDR?", "Why was my FDR rejected?", "Which documents are required?"),
    "security": ("What can I do with my role?", "Where do I find the alerts page?", "What do the FDR statuses mean?"),
    "admin": ("What can each role do?", "Where do I find the alerts page?", "What do the FDR statuses mean?"),
    "default": ("How do I create an FDR?", "Which documents are required?", "What do the FDR statuses mean?"),
}

CAPABILITIES = (
    "I can explain how the portal works (creating FDRs, documents, verification, statuses, exceptions, release) and answer "
    "questions about FDRs you are allowed to see, such as how many are pending review, which mature soon, or the status of a "
    "specific FDR number. I only read information: I cannot approve, reject, release funds, upload or change anything."
)

ROLE_FOOTER = {
    Role.INSTITUTION_USER.value: "As an institution user you see your own institution's records.",
}


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9/ \-]+", " ", text.casefold()).strip()


def match_topic(message: str, language: str = "en") -> Topic | None:
    """The best topic for a message: the one whose keyword phrase appears earliest and is longest. A
    message that matches no topic returns None; the caller then says it cannot answer.

    English keywords always apply (people mix languages). When the message is written in Devanagari, or a
    Hindi/Marathi answer was asked for, the Devanagari phrases of that language apply too."""
    text = f" {normalise(message)} "
    best: tuple[int, int, Topic] | None = None
    for topic in TOPICS:
        for keyword in topic.keywords:
            at = text.find(f" {keyword} ") if " " in keyword else text.find(f" {keyword}")
            if at != -1:
                candidate = (-len(keyword), at, topic)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
    if best is None and i18n.has_devanagari(message):
        folded = i18n.fold(message)
        for lang in ([language] if language in i18n.TOPIC_KEYWORDS else []) + [l for l in i18n.TOPIC_KEYWORDS if l != language]:
            for topic in TOPICS:
                for keyword in i18n.TOPIC_KEYWORDS[lang].get(topic.key, ()):
                    at = folded.find(i18n.fold(keyword))
                    if at != -1:
                        candidate = (-len(keyword), at, topic)
                        if best is None or candidate[:2] < best[:2]:
                            best = candidate
    return best[2] if best else None


def topic_text(topic: Topic, language: str) -> tuple[str, str]:
    """(text, language it is actually in). A topic without a translation is returned in English."""
    translated = i18n.TOPIC_TEXT.get(language, {}).get(topic.key)
    return (translated, language) if translated else (topic.text, "en")


def capabilities(language: str) -> str:
    return i18n.CAPABILITIES.get(language, CAPABILITIES)


def suggestions_for(page: str | None, language: str = "en") -> list[str]:
    table = i18n.SUGGESTIONS.get(language) or SUGGESTIONS_BY_PAGE
    return list(table.get(page or "default") or table["default"])
