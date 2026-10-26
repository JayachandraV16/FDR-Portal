"""Institution data scoping.

Scope is derived ONLY from the authenticated database user. No request parameter, header, cookie or
body field can widen it. Every query for institution-owned data must start from a helper here.

Agreed policy for this checkpoint: INSTITUTION_USER sees only the institution they are assigned to;
AICTE_REVIEWER, AICTE_APPROVER, ADMIN and AUDITOR see all institutions (AUDITOR read-only).
"""

from sqlalchemy import ColumnElement, Select, false, select

from app.models import FDR, FdrStatus, Institution, Role, User

ALL_INSTITUTIONS_ROLES = frozenset(
    {Role.AICTE_REVIEWER.value, Role.AICTE_APPROVER.value, Role.ADMIN.value, Role.AUDITOR.value}
)


def scoped_institutions(user: User) -> Select[tuple[Institution]]:
    """Base query for institutions visible to `user`."""
    stmt = select(Institution)
    if user.role in ALL_INSTITUTIONS_ROLES:
        return stmt
    if user.role == Role.INSTITUTION_USER.value and user.institution_id is not None:
        return stmt.where(Institution.id == user.institution_id)
    return stmt.where(false())  # unknown role or inconsistent row: see nothing


# --- FDR visibility ---------------------------------------------------------------------------
# PROVISIONAL, narrowest-safe policy (decision D4 is still open; widening it is a one-line change):
#   * INSTITUTION_USER: every FDR of their own institution, in any status (including DRAFT).
#   * AICTE_REVIEWER, AICTE_APPROVER, ADMIN, AUDITOR: FDRs of every institution, but only in states
#     that exist after submission. DRAFT (private working copies) and WITHDRAWN (which may have
#     been withdrawn before ever being submitted) are hidden from them.
# The data model has no reviewer-to-institution assignment, so "all institutions" is the only
# AICTE-side scope the schema can express; it mirrors the institution policy above.
SUBMITTED_STATE_STATUSES = (
    FdrStatus.SUBMITTED.value,
    FdrStatus.CORRECTION_REQUESTED.value,
    FdrStatus.ACCEPTED.value,
    FdrStatus.REJECTED.value,
)


def fdr_visibility(user: User) -> ColumnElement[bool]:
    """WHERE-clause fragment limiting FDR rows to those `user` may read. Derived only from the
    authenticated database user, never from request input. Apply it BEFORE pagination or counting."""
    if user.role == Role.INSTITUTION_USER.value and user.institution_id is not None:
        return FDR.institution_id == user.institution_id
    if user.role in ALL_INSTITUTIONS_ROLES:
        return FDR.status.in_(SUBMITTED_STATE_STATUSES)
    return false()  # unknown role or inconsistent row: see nothing
