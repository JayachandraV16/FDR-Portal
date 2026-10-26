"""PostgreSQL integration tests for the audit chain (skipped unless TEST_DATABASE_URL is set)."""

import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import AUDIT_LOCK_KEY, verify_chain, write_audit_event
from app.models import AuditLog


def test_chain_roundtrips_through_jsonb_and_timestamptz(pg_head):
    with Session(pg_head) as s:
        write_audit_event(
            s, action="a.one", outcome="success", ip_address="127.0.0.1",
            details={"reason": "x", "changed_fields": ["b", "a"], "status_code": 200, "locked": True},
        )
        write_audit_event(s, action="a.two", outcome="denied", details=None)
        s.commit()
    with Session(pg_head) as s:  # fresh session: values come back from PostgreSQL, not the identity map
        result = verify_chain(s)
    assert result.ok and result.checked == 2


def test_advisory_lock_is_held_until_the_writing_transaction_ends(pg_head):
    writer, probe = Session(pg_head), Session(pg_head)
    try:
        write_audit_event(writer, action="a.one", outcome="success")  # lock taken, not yet committed
        assert probe.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": AUDIT_LOCK_KEY}).scalar() is False
        probe.rollback()
        writer.commit()
        assert probe.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": AUDIT_LOCK_KEY}).scalar() is True
    finally:
        writer.close()
        probe.close()


def test_concurrent_writers_produce_a_single_unforked_chain(pg_head):
    threads_n, per_thread = 8, 6
    errors: list[Exception] = []
    barrier = threading.Barrier(threads_n)

    def worker(i: int) -> None:
        try:
            barrier.wait()
            for j in range(per_thread):
                with Session(pg_head) as s:
                    write_audit_event(s, action=f"t{i}.e{j}", outcome="success", details={"result_count": j})
                    s.commit()
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors and not any(t.is_alive() for t in threads)

    with Session(pg_head) as s:
        rows = s.query(AuditLog).order_by(AuditLog.seq).all()
        assert len(rows) == threads_n * per_thread
        assert [r.prev_hash for r in rows[1:]] == [r.hash for r in rows[:-1]]  # linear, no forks
        assert rows[0].prev_hash is None
        result = verify_chain(s)
    assert result.ok and result.checked == threads_n * per_thread


def test_triggers_still_block_edits_to_chained_events(pg_head):
    with Session(pg_head) as s:
        write_audit_event(s, action="a.one", outcome="success")
        s.commit()
    for stmt in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            with pg_head.begin() as conn:
                conn.execute(text(stmt))
    with Session(pg_head) as s:
        assert verify_chain(s).ok


def test_rolled_back_event_leaves_no_gap_in_links(pg_head):
    with Session(pg_head) as s:
        write_audit_event(s, action="kept.1", outcome="success")
        s.commit()
    with Session(pg_head) as s:
        write_audit_event(s, action="discarded", outcome="success")
        s.rollback()
    with Session(pg_head) as s:
        write_audit_event(s, action="kept.2", outcome="success")
        s.commit()
    with Session(pg_head) as s:
        result = verify_chain(s)  # seq has a gap from the rollback; links must still verify
    assert result.ok and result.checked == 2
