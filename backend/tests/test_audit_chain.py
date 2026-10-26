import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.audit import (
    AuditDetailError,
    AuditOutcome,
    canonical_event_json,
    compute_event_hash,
    verify_chain,
    write_audit_event,
)
from app.models import AuditLog

T0 = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=UTC)
UID = uuid.UUID("11111111-1111-1111-1111-111111111111")

BASE = dict(
    occurred_at=T0,
    actor_user_id=UID,
    actor_role="ADMIN",
    action="auth.login",
    entity_type="user",
    entity_id="abc",
    outcome="success",
    ip_address="127.0.0.1",
    request_id="req-1",
    details={"reason": "ok", "status_code": 200},
    prev_hash=None,
)


# ---- canonical form and hash -----------------------------------------------------------------
def test_canonical_json_is_deterministic_compact_and_key_order_independent():
    a = canonical_event_json(**BASE)
    b = canonical_event_json(**{**BASE, "details": {"status_code": 200, "reason": "ok"}})
    assert a == b
    assert " " not in a.replace("fdr-audit-v1", "")  # compact separators


def test_timestamp_is_normalised_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    same_instant = T0.astimezone(ist)
    assert compute_event_hash(**{**BASE, "occurred_at": same_instant}) == compute_event_hash(**BASE)
    assert compute_event_hash(**{**BASE, "occurred_at": T0.replace(tzinfo=None)}) == compute_event_hash(**BASE)


def test_actor_id_hashes_the_same_as_uuid_or_string():
    assert compute_event_hash(**{**BASE, "actor_user_id": str(UID)}) == compute_event_hash(**BASE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("occurred_at", T0 + timedelta(microseconds=1)),
        ("actor_user_id", uuid.uuid4()),
        ("actor_role", "AUDITOR"),
        ("action", "auth.logout"),
        ("entity_type", "fdr"),
        ("entity_id", "abd"),
        ("outcome", "failure"),
        ("ip_address", "10.0.0.1"),
        ("request_id", "req-2"),
        ("details", {"reason": "other"}),
        ("details", None),
        ("prev_hash", "0" * 64),
    ],
)
def test_changing_any_field_changes_the_hash(field, value):
    assert compute_event_hash(**{**BASE, field: value}) != compute_event_hash(**BASE)


def test_hash_is_64_hex_chars():
    h = compute_event_hash(**BASE)
    assert len(h) == 64 and int(h, 16) >= 0


# ---- writing ---------------------------------------------------------------------------------
def test_first_event_has_no_prev_and_second_links_to_first(sqlite_session):
    e1 = write_audit_event(sqlite_session, action="a.one", outcome=AuditOutcome.SUCCESS)
    e2 = write_audit_event(sqlite_session, action="a.two", outcome="failure")
    assert e1.prev_hash is None
    assert e2.prev_hash == e1.hash and e2.seq > e1.seq
    assert e1.hash != e2.hash


def test_event_records_actor_entity_and_context(sqlite_session, make_user):
    user = make_user()
    e = write_audit_event(
        sqlite_session,
        action="user.update",
        outcome="success",
        actor_user_id=user.id,
        actor_role="ADMIN",
        entity_type="user",
        entity_id=user.id,
        ip_address="203.0.113.9",
        request_id="r-1",
        details={"changed_fields": ["role"], "old_role": "AUDITOR", "new_role": "ADMIN"},
    )
    sqlite_session.commit()
    row = sqlite_session.get(AuditLog, e.seq)
    assert row.actor_user_id == user.id and row.entity_id == str(user.id)
    assert row.details["new_role"] == "ADMIN"
    assert row.occurred_at is not None


def test_secrets_cannot_be_written(sqlite_session):
    with pytest.raises(AuditDetailError):
        write_audit_event(sqlite_session, action="auth.login", outcome="failure", details={"password": "x"})
    assert sqlite_session.scalar(select(AuditLog.seq)) is None


def test_invalid_outcome_and_empty_action_rejected(sqlite_session):
    with pytest.raises(ValueError):
        write_audit_event(sqlite_session, action="x", outcome="great")
    with pytest.raises(ValueError):
        write_audit_event(sqlite_session, action="", outcome="success")


def test_oversized_identifying_fields_rejected_but_header_values_truncated(sqlite_session):
    with pytest.raises(ValueError):
        write_audit_event(sqlite_session, action="x" * 65, outcome="success")
    e = write_audit_event(
        sqlite_session, action="ok", outcome="success", ip_address="9" * 100, request_id="r" * 200
    )
    assert len(e.ip_address) == 45 and len(e.request_id) == 64


# ---- verification ----------------------------------------------------------------------------
def _seed_chain(db, n=5):
    return [write_audit_event(db, action=f"evt.{i}", outcome="success", details={"result_count": i}) for i in range(n)]


def test_empty_and_intact_chains_verify(sqlite_session):
    assert verify_chain(sqlite_session).ok is True
    _seed_chain(sqlite_session, 5)
    sqlite_session.commit()
    result = verify_chain(sqlite_session, batch_size=2)  # exercises batching
    assert result.ok and result.checked == 5


def test_modified_row_is_detected(sqlite_session):
    events = _seed_chain(sqlite_session)
    sqlite_session.commit()
    # SQLite has no append-only trigger, so this simulates a privileged attacker editing a row.
    sqlite_session.execute(text("UPDATE audit_logs SET action = 'tampered' WHERE seq = :s"), {"s": events[2].seq})
    sqlite_session.commit()
    sqlite_session.expire_all()
    result = verify_chain(sqlite_session)
    assert (result.ok, result.first_bad_seq, result.reason) == (False, events[2].seq, "hash_mismatch")


def test_modified_details_are_detected(sqlite_session):
    events = _seed_chain(sqlite_session)
    sqlite_session.commit()
    sqlite_session.execute(
        text("UPDATE audit_logs SET details = :d WHERE seq = :s"),
        {"d": '{"result_count": 99}', "s": events[1].seq},
    )
    sqlite_session.commit()
    sqlite_session.expire_all()
    assert verify_chain(sqlite_session).first_bad_seq == events[1].seq


def test_deleted_middle_row_is_detected(sqlite_session):
    events = _seed_chain(sqlite_session)
    sqlite_session.commit()
    sqlite_session.execute(text("DELETE FROM audit_logs WHERE seq = :s"), {"s": events[2].seq})
    sqlite_session.commit()
    result = verify_chain(sqlite_session)
    assert (result.ok, result.first_bad_seq, result.reason) == (False, events[3].seq, "broken_link")


def test_rewritten_prev_hash_is_detected(sqlite_session):
    events = _seed_chain(sqlite_session)
    sqlite_session.commit()
    sqlite_session.execute(
        text("UPDATE audit_logs SET prev_hash = :p WHERE seq = :s"), {"p": "f" * 64, "s": events[3].seq}
    )
    sqlite_session.commit()
    sqlite_session.expire_all()
    assert verify_chain(sqlite_session).reason == "broken_link"


def test_documented_limitation_tail_truncation_is_not_detectable(sqlite_session):
    """Removing the newest rows leaves a valid shorter chain. Detecting this needs an external
    anchor for the latest hash; this module deliberately does not claim to detect it."""
    events = _seed_chain(sqlite_session)
    sqlite_session.commit()
    sqlite_session.execute(text("DELETE FROM audit_logs WHERE seq = :s"), {"s": events[-1].seq})
    sqlite_session.commit()
    result = verify_chain(sqlite_session)
    assert result.ok is True and result.checked == len(events) - 1
