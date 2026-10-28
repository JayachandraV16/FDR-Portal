import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.models.fdr import ASSUMED_CURRENCY

TWO_PLACES = Decimal("0.01")

# What a draft may omit but a submission may not. PROVISIONAL: the model docstring leaves the final
# list to the product owners; keeping it in one place makes it a one-line change.
SUBMISSION_REQUIRED_FIELDS = ("branch_name", "depositor_name")

_OPTIONAL_TEXT = ("branch_name", "depositor_name", "purpose")
_NOT_NULLABLE = ("bank_id", "fdr_number", "principal_amount", "issue_date", "maturity_date")


class _Strict(BaseModel):
    # Unknown fields are rejected, so a client can never send institution_id, status, created_by,
    # row_version-as-a-lever or anything else the server decides.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _FdrFields(_Strict):
    """Writable fields. Shared validation for create and update."""

    @field_validator(*_OPTIONAL_TEXT, mode="after", check_fields=False)
    @classmethod
    def _blank_text_is_null(cls, value: str | None) -> str | None:
        return value or None


class FdrCreate(_FdrFields):
    bank_id: uuid.UUID
    fdr_number: str = Field(min_length=1, max_length=64)
    branch_name: str | None = Field(default=None, max_length=200)
    depositor_name: str | None = Field(default=None, max_length=300)
    purpose: str | None = Field(default=None, max_length=200)
    # Numeric(14, 2): at most 12 integer digits and 2 decimals; strictly positive.
    principal_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    interest_rate_pct: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    issue_date: date
    maturity_date: date

    @model_validator(mode="after")
    def _maturity_after_issue(self) -> "FdrCreate":
        if self.maturity_date <= self.issue_date:
            raise ValueError("maturity_date must be after issue_date")
        return self


class FdrUpdate(_FdrFields):
    """Partial update of a draft. Only fields present in the request are changed. `row_version` must
    match the stored value (optimistic concurrency)."""

    row_version: int = Field(ge=1)
    bank_id: uuid.UUID | None = None
    fdr_number: str | None = Field(default=None, min_length=1, max_length=64)
    branch_name: str | None = Field(default=None, max_length=200)
    depositor_name: str | None = Field(default=None, max_length=300)
    purpose: str | None = Field(default=None, max_length=200)
    principal_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    interest_rate_pct: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    issue_date: date | None = None
    maturity_date: date | None = None

    @field_validator(*_NOT_NULLABLE, mode="before")
    @classmethod
    def _required_columns_cannot_be_cleared(cls, value):
        if value is None:
            raise ValueError("This field cannot be cleared")
        return value


class FdrSubmit(_Strict):
    row_version: int = Field(ge=1)


class BankOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str


class BankList(BaseModel):
    items: list[BankOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class InstitutionSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class FdrOut(BaseModel):
    """Read model of an FDR. Deliberately omits internal/auth data (creator and submitter user IDs,
    password or session material) and anything from the audit log or revision snapshots.

    `status` ACCEPTED means accepted after human review; it is NOT bank verification.
    """

    id: uuid.UUID
    institution: InstitutionSummary
    bank: BankOut
    fdr_number: str
    branch_name: str | None
    depositor_name: str | None
    purpose: str | None
    # Provisional assumption (see app.models.fdr): all amounts are INR.
    currency: Literal["INR"] = ASSUMED_CURRENCY  # type: ignore[assignment]
    principal_amount: Decimal
    interest_rate_pct: Decimal | None
    issue_date: date
    maturity_date: date
    status: str
    row_version: int  # needed by clients for optimistic concurrency in later stages
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None

    @field_serializer("principal_amount", "interest_rate_pct")
    def _money_as_exact_decimal_string(self, value: Decimal | None) -> str | None:
        # Money crosses the API as a string with exactly two decimals, never as a JSON float.
        return None if value is None else format(value.quantize(TWO_PLACES), "f")


class FdrList(BaseModel):
    items: list[FdrOut]
    page: int
    page_size: int
    total: int  # count of records visible to the caller that match the filters; never cross-tenant
    total_pages: int


# ---- review workflow ---------------------------------------------------------------------------

REVIEW_COMMENT_MAX = 2000  # matches the CHECK constraint on fdr_status_history.comment


class CorrectionRequest(_Strict):
    """A reviewer asks the institution to correct a submitted FDR. The reason is mandatory: it is
    what the institution user will read."""

    row_version: int = Field(ge=1)
    comment: str = Field(min_length=1, max_length=REVIEW_COMMENT_MAX)


class RejectRequest(_Strict):
    row_version: int = Field(ge=1)
    comment: str = Field(min_length=1, max_length=REVIEW_COMMENT_MAX)  # a reason is mandatory


class AcceptRequest(_Strict):
    row_version: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=REVIEW_COMMENT_MAX)  # optional

    @field_validator("comment", mode="after")
    @classmethod
    def _blank_comment_is_null(cls, value: str | None) -> str | None:
        return value or None


class HistoryEntryOut(BaseModel):
    """One status change. The actor is identified by role only: no user ids, names or emails."""

    from_status: str | None
    to_status: str
    actor_role: str
    comment: str | None
    occurred_at: datetime


class RevisionInfo(BaseModel):
    """That a revision exists and when it was submitted. The frozen snapshot itself is not exposed."""

    revision_no: int
    submitted_at: datetime


class FdrHistory(BaseModel):
    history: list[HistoryEntryOut]  # oldest first
    revisions: list[RevisionInfo]  # oldest first
