"""Pure-Python and structural checks for the FDR models (no server needed)."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, create_engine, inspect

from app.models import FDR, Base, FDRRevision, FdrStatus, Role
from app.models.fdr import (
    ACTIVE_STATUSES,
    assert_snapshot_safe,
    fdr_snapshot,
    normalize_fdr_number,
)
from tests.api_support import make_settings  # noqa: F401  (ensures test env is initialised)
from tests.pg_support import alembic_config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_models_are_registered_in_the_shared_metadata():
    assert {"fdrs", "fdr_status_history", "fdr_revisions"} <= set(Base.metadata.tables)


def test_status_vocabulary_is_exactly_the_approved_six():
    assert [s.value for s in FdrStatus] == [
        "DRAFT", "SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED", "REJECTED", "WITHDRAWN",
    ]
    assert [s.value for s in ACTIVE_STATUSES] == ["SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED"]
    assert "VERIFIED" not in FdrStatus.__members__  # ACCEPTED must never read as bank verification


def test_monetary_columns_use_exact_decimal_types():
    cols = FDR.__table__.c
    assert cols.principal_amount.type.precision == 14 and cols.principal_amount.type.scale == 2
    assert cols.interest_rate_pct.type.precision == 5 and cols.interest_rate_pct.type.scale == 2
    assert cols.principal_amount.type.asdecimal is True


def test_draft_creation_does_not_require_submission_only_fields():
    nullable = {c.name for c in FDR.__table__.c if c.nullable}
    assert {"branch_name", "depositor_name", "purpose", "interest_rate_pct", "submitted_at", "submitted_by",
            "decided_at"} <= nullable


def test_foreign_keys_never_cascade_deletes():
    for table in ("fdrs", "fdr_status_history", "fdr_revisions"):
        for fk in Base.metadata.tables[table].foreign_keys:
            assert fk.ondelete == "RESTRICT", f"{table}.{fk.parent.name}"


def test_foreign_keys_reuse_existing_reference_tables():
    targets = {fk.column.table.name for fk in FDR.__table__.foreign_keys}
    assert targets == {"institutions", "banks", "users"}


def test_partial_unique_index_is_declared_on_active_states_only():
    (index,) = [i for i in FDR.__table__.indexes if i.name == "uq_fdrs_active_bank_fdr_number"]
    assert index.unique and [c.name for c in index.columns] == ["bank_id", "fdr_number_normalized"]
    where = str(index.dialect_options["postgresql"]["where"])
    assert all(s.value in where for s in ACTIVE_STATUSES)
    assert not any(s in where for s in ("DRAFT", "REJECTED", "WITHDRAWN"))


# ---- normalisation ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [("fdr 001", "FDR001"), ("  Ab-12 34\t", "AB-1234"), ("12\n34", "1234"), ("x/y.z", "X/Y.Z")],
)
def test_normalize_fdr_number(raw, expected):
    assert normalize_fdr_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_normalize_rejects_empty(raw):
    with pytest.raises(ValueError):
        normalize_fdr_number(raw)


# ---- snapshots -------------------------------------------------------------------------------
def make_fdr(**kw) -> FDR:
    import uuid

    values = dict(
        institution_id=uuid.uuid4(), bank_id=uuid.uuid4(), fdr_number="FDR 001", fdr_number_normalized="FDR001",
        principal_amount=Decimal("1250000.50"), interest_rate_pct=Decimal("7.10"),
        issue_date=date(2025, 1, 1), maturity_date=date(2026, 1, 1), created_by=uuid.uuid4(),
    )
    values.update(kw)
    return FDR(**values)


def test_snapshot_serialises_money_as_exact_decimal_strings():
    snap = fdr_snapshot(make_fdr(principal_amount=Decimal("0.10"), interest_rate_pct=Decimal("7.10")))
    assert snap["principal_amount"] == "0.10" and snap["interest_rate_pct"] == "7.10"
    assert snap["currency"] == "INR" and snap["snapshot_version"] == 1
    assert snap["issue_date"] == "2025-01-01" and snap["maturity_date"] == "2026-01-01"
    assert_snapshot_safe(snap)  # plain JSON only


def test_snapshot_preserves_trailing_zeros_and_large_values_without_float_rounding():
    snap = fdr_snapshot(make_fdr(principal_amount=Decimal("999999999999.99")))
    assert snap["principal_amount"] == "999999999999.99"
    assert "e" not in snap["principal_amount"].lower()


def test_snapshot_handles_missing_rate_and_refuses_floats():
    assert fdr_snapshot(make_fdr(interest_rate_pct=None))["interest_rate_pct"] is None
    with pytest.raises(ValueError):
        fdr_snapshot(make_fdr(principal_amount=1250000.5))
    with pytest.raises(ValueError):
        fdr_snapshot(make_fdr(interest_rate_pct=7.1))


@pytest.mark.parametrize(
    "bad",
    [{"amount": 1.5}, {"nested": {"amount": 2.0}}, {"list": [1, 2.5]}, {"amount": Decimal("1.00")}, {1: "non-string key"}],
)
def test_snapshot_validator_rejects_floats_decimals_and_odd_keys(bad):
    with pytest.raises(ValueError):
        assert_snapshot_safe(bad)
    with pytest.raises(ValueError):
        FDRRevision(fdr_id=None, revision_no=1, snapshot=bad, submitted_by=None)


def test_snapshot_validator_accepts_plain_json_values():
    FDRRevision(snapshot={"a": "1.00", "b": [1, True, None, "x"], "c": {"d": 3}}, revision_no=1)


# ---- migration graph and structure -----------------------------------------------------------
def test_revision_graph_is_linear_and_each_revision_depends_on_the_previous_one():
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert script.get_heads() == ["0007"]
    assert script.get_revision("0003").down_revision == "0002"
    assert script.get_revision("0004").down_revision == "0003"
    assert script.get_revision("0005").down_revision == "0004"
    assert script.get_revision("0006").down_revision == "0005"
    assert script.get_revision("0007").down_revision == "0006"
    assert [r.revision for r in script.walk_revisions()] == ["0007", "0006", "0005", "0004", "0003", "0002", "0001"]


def test_0004_only_creates_its_own_table_and_never_touches_existing_ones():
    source = (BACKEND_DIR / "alembic" / "versions" / "0004_fdr_documents.py").read_text(encoding="utf-8")
    forbidden = re.compile(
        r"op\.(alter_column|add_column|drop_column|rename_table|drop_constraint)|"
        r"ALTER\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|TRUNCATE|INSERT\s+INTO",
        re.IGNORECASE,
    )
    assert forbidden.findall(source) == []
    assert source.count("op.create_table(") == 1 and re.search(r'op\.create_table\(\s*"fdr_documents"', source)
    assert source.count("op.drop_table(") == 1 and 'op.drop_table("fdr_documents")' in source


def test_0005_only_creates_its_own_table_and_never_touches_existing_ones():
    source = (BACKEND_DIR / "alembic" / "versions" / "0005_document_extractions.py").read_text(encoding="utf-8")
    forbidden = re.compile(
        r"op\.(alter_column|add_column|drop_column|rename_table|drop_constraint)|"
        r"ALTER\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|TRUNCATE|INSERT\s+INTO",
        re.IGNORECASE,
    )
    assert forbidden.findall(source) == []
    assert source.count("op.create_table(") == 1 and re.search(r'op\.create_table\(\s*"fdr_document_extractions"', source)
    assert source.count("op.drop_table(") == 1 and 'op.drop_table("fdr_document_extractions")' in source


def test_0006_only_creates_its_own_table_and_never_touches_existing_ones():
    source = (BACKEND_DIR / "alembic" / "versions" / "0006_extraction_jobs.py").read_text(encoding="utf-8")
    forbidden = re.compile(
        r"op\.(alter_column|add_column|drop_column|rename_table|drop_constraint)|"
        r"ALTER\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|TRUNCATE|INSERT\s+INTO",
        re.IGNORECASE,
    )
    assert forbidden.findall(source) == []
    assert source.count("op.create_table(") == 1 and re.search(r'op\.create_table\(\s*"fdr_extraction_jobs"', source)
    assert source.count("op.drop_table(") == 1 and 'op.drop_table("fdr_extraction_jobs")' in source


def test_0007_only_creates_its_own_table_and_never_touches_existing_ones():
    source = (BACKEND_DIR / "alembic" / "versions" / "0007_chat_feedback.py").read_text(encoding="utf-8")
    forbidden = re.compile(
        r"op\.(alter_column|add_column|drop_column|rename_table|drop_constraint)|"
        r"ALTER\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|TRUNCATE|INSERT\s+INTO",
        re.IGNORECASE,
    )
    assert forbidden.findall(source) == []
    assert source.count("op.create_table(") == 1 and re.search(r'op\.create_table\(\s*"chat_feedback"', source)
    assert source.count("op.drop_table(") == 1 and 'op.drop_table("chat_feedback")' in source
    for forbidden_column in ("message", "prompt", "question", "answer", "reply_text", "comment", "ip_address", "token"):
        assert f'sa.Column("{forbidden_column}"' not in source  # the table holds no conversation content


def test_migration_file_only_creates_and_never_touches_existing_tables():
    source = (BACKEND_DIR / "alembic" / "versions" / "0003_fdr_core.py").read_text(encoding="utf-8")
    # Real statements only (trigger DDL legitimately contains "BEFORE UPDATE OR DELETE ON ...").
    forbidden = re.compile(
        r"op\.(alter_column|add_column|drop_column|rename_table|drop_constraint|drop_table|drop_index)|"
        r"\bALTER\s+TABLE\b|\bDELETE\s+FROM\b|\bUPDATE\s+\w+\s+SET\b|\bTRUNCATE\s+TABLE\b|\bINSERT\s+INTO\b",
        re.IGNORECASE,
    )
    assert not forbidden.search(source.split("def downgrade")[0])
    upgrade_tables = set(re.findall(r'op\.create_table\(\s*"(\w+)"', source))
    assert upgrade_tables == {"fdrs", "fdr_status_history", "fdr_revisions"}
    drops = set(re.findall(r'op\.drop_table\("(\w+)"\)', source))
    assert drops == upgrade_tables  # downgrade removes exactly what upgrade created


def test_migrated_check_constraint_names_match_the_models(tmp_path):
    """Alembic's autogenerate comparison ignores CHECK constraints, so compare their names here."""
    from alembic import command

    url = f"sqlite:///{tmp_path / 'checks.sqlite'}"
    command.upgrade(alembic_config(url), "head")
    insp = inspect(create_engine(url))
    for table in ("fdrs", "fdr_status_history", "fdr_revisions"):
        in_db = {c["name"] for c in insp.get_check_constraints(table)}
        in_models = {c.name for c in Base.metadata.tables[table].constraints if isinstance(c, CheckConstraint)}
        assert in_db == in_models, table


def test_role_list_in_history_check_tracks_the_role_enum():
    check = next(c for c in Base.metadata.tables["fdr_status_history"].constraints
                 if isinstance(c, CheckConstraint) and c.name == "ck_fdr_status_history_actor_role_valid")
    assert all(r.value in str(check.sqltext) for r in Role)
