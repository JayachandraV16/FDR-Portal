"""Release/refund state, derived from the audit trail.

There is no release/refund table (adding one needs a database migration, which has not been approved), so
the workflow's state is read from audit events that real, signed-in people caused:

    release.requested   an institution user asked for release of a matured, accepted FDR
    release.approved    an AICTE approver authorised the release
    release.rejected    an AICTE approver declined it (with a fixed reason code)

The audit trail is append-only and hash-chained, which is why it can serve as the record of who did what.
Only SUCCESS events count; denied attempts are logged under the same action names with outcome 'denied'
and are ignored here. State is NONE -> REQUESTED -> APPROVED or REJECTED; a rejected request may be made
again. APPROVED is final. None of this moves money: an approval is an authorisation recorded here, and
the FDR's own status is untouched.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FDR, AuditLog, FdrStatus, User

REQUESTED, APPROVED, REJECTED, NONE = "REQUESTED", "APPROVED", "REJECTED", "NONE"
ACTIONS = ("release.requested", "release.approved", "release.rejected")
_STATE_AFTER = {"release.requested": REQUESTED, "release.approved": APPROVED, "release.rejected": REJECTED}
REJECTION_REASONS = ("INCOMPLETE_SUPPORTING_INFORMATION", "NOT_YET_DUE", "DETAILS_NEED_CORRECTION", "OTHER")
_CHUNK = 400


def is_eligible(fdr: FDR, today: date) -> bool:
    """A release request makes sense only for an accepted FDR that has reached its maturity date."""
    return fdr.status == FdrStatus.ACCEPTED.value and fdr.maturity_date <= today


def eligibility_note(fdr: FDR, today: date) -> str:
    if fdr.status != FdrStatus.ACCEPTED.value:
        return "Only an accepted FDR can be released."
    if fdr.maturity_date > today:
        return f"Not yet matured: matures on {fdr.maturity_date.isoformat()}."
    return "Matured and accepted: eligible for a release request."


def release_states(db: Session, fdr_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """The current release state of each FDR (NONE if it has no release events)."""
    states = {fdr_id: NONE for fdr_id in fdr_ids}
    for start in range(0, len(fdr_ids), _CHUNK):
        chunk = [str(i) for i in fdr_ids[start : start + _CHUNK]]
        rows = db.execute(
            select(AuditLog.entity_id, AuditLog.action)
            .where(
                AuditLog.entity_type == "fdr", AuditLog.entity_id.in_(chunk),
                AuditLog.action.in_(ACTIONS), AuditLog.outcome == "success",
            )
            .order_by(AuditLog.seq)
        )
        for entity_id, action in rows:
            states[uuid.UUID(entity_id)] = _STATE_AFTER[action]
    return states


def release_history(db: Session, fdr_id: uuid.UUID) -> list[tuple[AuditLog, str | None]]:
    """The successful release events of one FDR, oldest first, with the acting user's name."""
    rows = db.execute(
        select(AuditLog, User.full_name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(
            AuditLog.entity_type == "fdr", AuditLog.entity_id == str(fdr_id),
            AuditLog.action.in_(ACTIONS), AuditLog.outcome == "success",
        )
        .order_by(AuditLog.seq)
    ).all()
    return [(event, name) for event, name in rows]
