"""Last-active-ADMIN invariant under real concurrency (PostgreSQL only; needs row locks)."""

import threading

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.models import User
from tests.api_support import audit_events, sign_in


def _run_together(fns):
    barrier = threading.Barrier(len(fns))
    results, errors = [None] * len(fns), []

    def worker(i):
        try:
            barrier.wait()
            results[i] = fns[i]()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(fns))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors and not any(t.is_alive() for t in threads)
    return results


def active_admins(engine) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(User).where(User.role == "ADMIN", User.is_active.is_(True)))


def fresh_pair(engine, tag):
    """Two new logged-in admins, with every other admin demoted so exactly {a, b} are active."""
    a = sign_in(engine, role="ADMIN", email=f"a{tag}@example.invalid")
    b = sign_in(engine, role="ADMIN", email=f"b{tag}@example.invalid")
    with Session(engine) as s:
        for u in s.scalars(select(User).where(User.role == "ADMIN", User.id.notin_([a.user_id, b.user_id]))):
            u.role = "AUDITOR"
        s.commit()
    return a, b


def test_two_admins_cannot_demote_each_other_simultaneously(pg_head):
    for round_no in range(8):
        a, b = fresh_pair(pg_head, f"d{round_no}")
        statuses = _run_together([
            lambda: a.patch(f"/users/{b.user_id}", {"role": "AUDITOR"}).status_code,
            lambda: b.patch(f"/users/{a.user_id}", {"role": "AUDITOR"}).status_code,
        ])
        assert active_admins(pg_head) == 1, statuses
        assert sorted(statuses)[0] == 200 and sorted(statuses)[1] in (403, 409)


def test_two_admins_cannot_deactivate_each_other_simultaneously(pg_head):
    for round_no in range(8):
        a, b = fresh_pair(pg_head, f"x{round_no}")
        statuses = _run_together([
            lambda: a.patch(f"/users/{b.user_id}", {"is_active": False}).status_code,
            lambda: b.patch(f"/users/{a.user_id}", {"is_active": False}).status_code,
        ])
        assert active_admins(pg_head) == 1, statuses
        assert 200 in statuses and set(statuses) <= {200, 401, 403, 409}


def test_two_admins_cannot_both_step_down_themselves(pg_head):
    for round_no in range(8):
        a, b = fresh_pair(pg_head, f"s{round_no}")
        statuses = _run_together([
            lambda: a.patch(f"/users/{a.user_id}", {"role": "AUDITOR"}).status_code,
            lambda: b.patch(f"/users/{b.user_id}", {"role": "AUDITOR"}).status_code,
        ])
        assert active_admins(pg_head) == 1, statuses
        assert sorted(statuses) == [200, 409]


def test_many_admins_racing_to_remove_each_other_always_leave_at_least_one(pg_head):
    admins = [sign_in(pg_head, role="ADMIN", email=f"adm{i}@example.invalid") for i in range(6)]
    ids = [x.user_id for x in admins]
    fns = []
    for i, actor in enumerate(admins):
        victim = ids[(i + 1) % len(ids)]
        payload = {"is_active": False} if i % 2 else {"role": "AUDITOR"}
        fns.append(lambda actor=actor, victim=victim, payload=payload: actor.patch(f"/users/{victim}", payload).status_code)
    statuses = _run_together(fns)
    assert set(statuses) <= {200, 401, 403, 409}
    assert active_admins(pg_head) >= 1
    with Session(pg_head) as s:
        assert verify_chain(s).ok


def test_blocked_attempts_are_audited_and_successes_match_the_final_state(pg_head):
    a = sign_in(pg_head, role="ADMIN", email="a@example.invalid")
    b = sign_in(pg_head, role="ADMIN", email="b@example.invalid")
    statuses = _run_together([
        lambda: a.patch(f"/users/{b.user_id}", {"role": "AUDITOR"}).status_code,
        lambda: b.patch(f"/users/{a.user_id}", {"role": "AUDITOR"}).status_code,
    ])
    assert len(audit_events(pg_head, "user.role_change", "success")) == statuses.count(200) == 1
    refused = audit_events(pg_head, "authz.denied", "denied")
    assert len(refused) == 1 and refused[0].details["reason"] in ("last_admin_protection", "role_not_permitted")


def test_concurrent_user_creation_with_the_same_email_creates_one_account(pg_head):
    admin = sign_in(pg_head, role="ADMIN")
    payload = {"email": "dup@example.invalid", "full_name": "Dup (Synthetic)", "role": "AUDITOR"}
    statuses = _run_together([lambda: admin.post("/users", payload).status_code for _ in range(2)] if False else [
        lambda: sign_in_again(pg_head, admin).post("/users", payload).status_code,
        lambda: sign_in_again(pg_head, admin).post("/users", payload).status_code,
    ])
    assert sorted(statuses) == [201, 409]
    with Session(pg_head) as s:
        assert s.scalar(select(func.count()).select_from(User).where(User.email == "dup@example.invalid")) == 1
        assert verify_chain(s).ok


def sign_in_again(engine, admin):
    """A second client for the same admin (clients are not safe to share across threads)."""
    from tests.api_support import build_client, csrf_headers, login, Actor

    client = build_client(engine)
    response = login(client, admin.email)
    return Actor(client, csrf_headers(response), admin.user_id, admin.email)
