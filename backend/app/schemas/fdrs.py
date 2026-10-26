import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer

from app.models.fdr import ASSUMED_CURRENCY

TWO_PLACES = Decimal("0.01")


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
