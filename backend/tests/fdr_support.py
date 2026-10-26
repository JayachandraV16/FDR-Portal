"""Helpers for FDR schema tests (synthetic data only)."""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models import FDR, Bank, FDRRevision, FDRStatusHistory, Institution, User

# SQLite has no native DECIMAL and warns when a Decimal is bound. Money-precision behaviour is
# therefore verified on PostgreSQL only; SQLite runs the same tests as a structural stand-in.
sqlite_decimal_warning = pytest.mark.filterwarnings("ignore:Dialect sqlite.*Decimal:sqlalchemy.exc.SAWarning")


@dataclass
class Prereqs:
    institution_id: uuid.UUID
    institution2_id: uuid.UUID
    bank_id: uuid.UUID
    bank2_id: uuid.UUID
    user_id: uuid.UUID


def seed_prereqs(engine) -> Prereqs:
    with Session(engine) as s:
        inst1 = Institution(code="FDR-I1", name="FDR Institution 1 (Synthetic)", state="X", institution_type="Engineering")
        inst2 = Institution(code="FDR-I2", name="FDR Institution 2 (Synthetic)", state="Y", institution_type="Polytechnic")
        bank1 = Bank(code="FDR-B1", name="Sample Bank 1 (Synthetic)")
        bank2 = Bank(code="FDR-B2", name="Sample Bank 2 (Synthetic)")
        s.add_all([inst1, inst2, bank1, bank2])
        s.flush()
        user = User(email="fdr-owner@example.invalid", full_name="Owner (Synthetic)",
                    password_hash="not-a-real-hash", role="INSTITUTION_USER", institution_id=inst1.id)
        s.add(user)
        s.commit()
        return Prereqs(inst1.id, inst2.id, bank1.id, bank2.id, user.id)


def fdr_kwargs(p: Prereqs, **overrides) -> dict:
    values = dict(
        institution_id=p.institution_id,
        bank_id=p.bank_id,
        fdr_number="FDR 001",
        fdr_number_normalized="FDR001",
        principal_amount=Decimal("100000.00"),
        interest_rate_pct=Decimal("7.10"),
        issue_date=date(2025, 1, 1),
        maturity_date=date(2026, 1, 1),
        created_by=p.user_id,
    )
    values.update(overrides)
    return values


def add_fdr(engine, p: Prereqs, **overrides) -> uuid.UUID:
    with Session(engine) as s:
        fdr = FDR(**fdr_kwargs(p, **overrides))
        s.add(fdr)
        s.commit()
        return fdr.id


def add_history(engine, p: Prereqs, fdr_id, **overrides) -> int:
    values = dict(fdr_id=fdr_id, from_status=None, to_status="DRAFT", actor_user_id=p.user_id, actor_role="INSTITUTION_USER")
    values.update(overrides)
    with Session(engine) as s:
        row = FDRStatusHistory(**values)
        s.add(row)
        s.commit()
        return row.id


def add_revision(engine, p: Prereqs, fdr_id, revision_no=1, snapshot=None) -> uuid.UUID:
    with Session(engine) as s:
        row = FDRRevision(fdr_id=fdr_id, revision_no=revision_no, snapshot=snapshot or {"fdr_number": "FDR 001"},
                          submitted_by=p.user_id)
        s.add(row)
        s.commit()
        return row.id


def rejected_by_database(engine, build) -> bool:
    """True if the database rejects `build(session)`: a constraint violation (IntegrityError) or a
    value that does not fit the column type (DataError, e.g. PostgreSQL varchar length)."""
    with Session(engine) as s:
        try:
            build(s)
            s.commit()
        except (IntegrityError, DataError):
            s.rollback()
            return True
    return False
