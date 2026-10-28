"""Copilot feedback: what is stored (and what never is), duplicate protection, limits, retention."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.api.chat_feedback as module
from app.api.chat import RateLimiter
from app.chat import feedback_cleanup
from app.models import ChatFeedback
from app.timeutil import utcnow
from tests.api_support import API, audit_events, build_client, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_write_api import institution_user, world  # noqa: F401

pytestmark = sqlite_decimal_warning
ROLES = ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"]


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(module, "limiter", RateLimiter(limit=30, window=600))


def payload(**over):
    return {"reply_id": str(uuid.uuid4()), "rating": "helpful", "kind": "help", "source": "built-in", "language": "en", "page": "fdrs", **over}


def actor(engine, world, role):
    return institution_user(engine, world) if role == "INSTITUTION_USER" else sign_in(engine, role=role)


def rows(engine):
    with Session(engine) as s:
        return list(s.scalars(select(ChatFeedback).order_by(ChatFeedback.created_at)))


def add(engine, user_id, *, days_old=0, rating=1, reason=None):
    with Session(engine) as s:
        s.add(ChatFeedback(user_id=user_id, rating=rating, reason_code=reason, reply_kind="help", source="built-in", language="en", page="fdrs",
                           reply_id=uuid.uuid4(), created_at=utcnow() - timedelta(days=days_old)))
        s.commit()


# ---- recording ---------------------------------------------------------------------------------------------------
def test_a_helpful_rating_is_stored_with_only_facts_about_the_answer(engine, world):
    who = institution_user(engine, world)
    body = payload(kind="records", source="llm", provider="gemini", language="hi", page="fdr_detail")
    response = who.post("/chat/feedback", body)
    assert response.status_code == 200 and response.json() == {"recorded": True, "duplicate": False, "rating": "helpful"}
    assert response.headers["cache-control"] == "no-store"
    (row,) = rows(engine)
    assert (row.user_id, row.rating, row.reason_code, row.reply_kind, row.source, row.provider, row.language, row.page) == (
        who.user_id, 1, None, "records", "llm", "gemini", "hi", "fdr_detail")
    assert str(row.reply_id) == body["reply_id"] and row.created_at is not None


def test_a_not_helpful_rating_keeps_an_optional_fixed_reason(engine, world):
    who = institution_user(engine, world)
    assert who.post("/chat/feedback", payload(rating="not_helpful", reason="incorrect")).json()["rating"] == "not_helpful"
    assert who.post("/chat/feedback", payload(rating="not_helpful")).status_code == 200
    stored = rows(engine)
    assert [(r.rating, r.reason_code) for r in stored] == [(-1, "incorrect"), (-1, None)]


def test_the_table_has_no_column_that_could_hold_conversation_text(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("chat_feedback")}
    assert columns == {"id", "user_id", "created_at", "rating", "reason_code", "reply_kind", "source", "provider", "language", "page", "reply_id"}


@pytest.mark.parametrize("field", ["message", "question", "answer", "reply", "comment", "text", "prompt", "document", "email", "ip_address", "token"])
def test_a_submission_cannot_carry_text_or_personal_data_at_all(engine, world, field):
    response = institution_user(engine, world).post("/chat/feedback", {**payload(), field: "anything the user typed"})
    assert response.status_code == 422 and rows(engine) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"rating": "great"}, {"rating": 5}, {"reason": "because"}, {"reason": "incorrect"}, {"kind": "chat"}, {"source": "gemini"}, {"language": "fr"},
        {"page": "somewhere"}, {"reply_id": "not-a-uuid"}, {"provider": "other-ai"}, {"source": "built-in", "provider": "gemini"}, {"rating": "helpful", "reason": "unclear"},
    ],
)
def test_invalid_or_inconsistent_submissions_are_refused(engine, world, bad):
    assert institution_user(engine, world).post("/chat/feedback", payload(**bad)).status_code == 422
    assert rows(engine) == []


# ---- duplicates ------------------------------------------------------------------------------------------------
def test_a_repeated_submission_for_the_same_answer_is_recorded_once(engine, world):
    who = institution_user(engine, world)
    body = payload()
    first, second, third = (who.post("/chat/feedback", body).json() for _ in range(3))
    assert first["duplicate"] is False and second["duplicate"] is True and third["duplicate"] is True
    assert len(rows(engine)) == 1


def test_changing_the_rating_for_an_answer_that_was_already_rated_keeps_the_first_rating(engine, world):
    who = institution_user(engine, world)
    body = payload(rating="helpful")
    who.post("/chat/feedback", body)
    again = who.post("/chat/feedback", {**body, "rating": "not_helpful", "reason": "incorrect"}).json()
    assert again == {"recorded": True, "duplicate": True, "rating": "helpful"}
    assert [r.rating for r in rows(engine)] == [1]


def test_two_users_may_have_the_same_reply_id_and_each_is_recorded(engine, world):
    one, two = institution_user(engine, world), sign_in(engine, role="AICTE_REVIEWER")
    body = payload()
    assert one.post("/chat/feedback", body).json()["duplicate"] is False and two.post("/chat/feedback", body).json()["duplicate"] is False
    assert len(rows(engine)) == 2


def test_the_database_itself_refuses_a_duplicate_a_bad_rating_and_an_inconsistent_row(engine, world):
    who = institution_user(engine, world)
    base = dict(user_id=who.user_id, reply_kind="help", source="built-in", language="en", reply_id=uuid.uuid4())
    bad_rows = [
        dict(base, rating=0), dict(base, rating=1, reason_code="incorrect"), dict(base, rating=-1, reason_code="nonsense"), dict(base, rating=1, language="fr"),
        dict(base, rating=1, reply_kind="chat"), dict(base, rating=1, source="gemini"),
    ]
    for values in bad_rows:
        with Session(engine) as s:
            s.add(ChatFeedback(**values))
            with pytest.raises(IntegrityError):
                s.commit()
    with Session(engine) as s:
        s.add(ChatFeedback(**dict(base, rating=1)))
        s.commit()
        s.add(ChatFeedback(**dict(base, rating=-1)))
        with pytest.raises(IntegrityError):  # same user and reply id
            s.commit()


# ---- limits and access ---------------------------------------------------------------------------------------------
def test_each_user_has_a_rate_limit(engine, world, monkeypatch):
    monkeypatch.setattr(module, "limiter", RateLimiter(limit=2, window=60))
    who = institution_user(engine, world)
    assert [who.post("/chat/feedback", payload()).status_code for _ in range(3)] == [200, 200, 429]
    assert sign_in(engine, role="AICTE_REVIEWER").post("/chat/feedback", payload()).status_code == 200  # another user is unaffected
    assert len(rows(engine)) == 3


def test_a_duplicate_does_not_use_up_the_limit_twice_but_a_flood_is_stopped(engine, world, monkeypatch):
    monkeypatch.setattr(module, "limiter", RateLimiter(limit=3, window=60))
    who = institution_user(engine, world)
    body = payload()
    assert [who.post("/chat/feedback", body).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert len(rows(engine)) == 1


@pytest.mark.parametrize("role", ROLES)
def test_every_signed_in_role_may_rate_an_answer(engine, world, role):
    assert actor(engine, world, role).post("/chat/feedback", payload()).status_code == 200


def test_feedback_needs_a_session_the_csrf_token_and_a_permanent_password(engine, world):
    who = institution_user(engine, world)
    assert build_client(engine).post(f"{API}/chat/feedback", json=payload()).status_code in (401, 403)
    assert who.client.post(f"{API}/chat/feedback", json=payload()).status_code == 403  # no CSRF token
    assert sign_in(engine, role="ADMIN", must_change_password=True).post("/chat/feedback", payload()).status_code == 403
    assert rows(engine) == []


def test_without_the_migration_nothing_is_stored_and_the_answer_is_an_honest_503(engine, world):
    who = institution_user(engine, world)
    ChatFeedback.__table__.drop(engine)  # simulate a database where migration 0007 has not been applied
    response = who.post("/chat/feedback", payload())
    assert response.status_code == 503 and "not available on this server yet" in response.json()["detail"]
    assert not inspect(engine).has_table("chat_feedback")
    summary = sign_in(engine, role="ADMIN").get("/chat/feedback/summary").json()
    assert summary["available"] is False and "migration has not been applied" in summary["note"] and summary["total"] == 0


# ---- the administrator summary -------------------------------------------------------------------------------------
def test_the_summary_counts_the_window_by_rating_reason_kind_source_language_and_page(engine, world):
    admin = sign_in(engine, role="ADMIN")
    a, b = institution_user(engine, world), sign_in(engine, role="AICTE_REVIEWER")
    a.post("/chat/feedback", payload(rating="helpful", kind="records", source="llm", provider="gemini", language="hi", page="overview"))
    a.post("/chat/feedback", payload(rating="not_helpful", reason="incorrect"))
    b.post("/chat/feedback", payload(rating="not_helpful", reason="unclear", language="mr"))
    add(engine, a.user_id, days_old=100, rating=-1, reason="other")  # outside every window
    body = admin.get("/chat/feedback/summary").json()
    assert (body["available"], body["total"], body["helpful"], body["not_helpful"]) == (True, 3, 1, 2)
    assert body["by_reason"] == {"incorrect": 1, "unclear": 1} and body["by_kind"] == {"records": 1, "help": 2}
    assert body["by_source"] == {"llm": 1, "built-in": 2} and body["by_language"] == {"hi": 1, "en": 1, "mr": 1}
    assert body["by_page"]["overview"] == 1 and body["retention_days"] == 90
    assert admin.get("/chat/feedback/summary?days=7").json()["total"] == 3
    assert admin.get("/chat/feedback/summary?days=30").json()["window_days"] == 30


@pytest.mark.parametrize("role", ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "AUDITOR"])
def test_only_administrators_may_read_the_summary(engine, world, role):
    assert actor(engine, world, role).get("/chat/feedback/summary").status_code == 403
    assert sign_in(engine, role="ADMIN").get("/chat/feedback/summary?days=45").status_code == 422


# ---- retention ------------------------------------------------------------------------------------------------
def test_a_dry_run_counts_expired_rows_and_deletes_nothing(engine, world):
    who = institution_user(engine, world)
    add(engine, who.user_id, days_old=120)
    add(engine, who.user_id, days_old=95)
    add(engine, who.user_id, days_old=89)
    with Session(engine) as s:
        assert feedback_cleanup.purge(s, 90) == 2
    assert len(rows(engine)) == 3


def test_applying_deletes_only_rows_older_than_the_cutoff_in_batches_and_audits_the_count(engine, world):
    who = institution_user(engine, world)
    for age in (200, 150, 120, 100, 91):
        add(engine, who.user_id, days_old=age)
    keep = [add(engine, who.user_id, days_old=age) for age in (89, 30, 0)]
    assert keep == [None, None, None]
    with Session(engine) as s:
        assert feedback_cleanup.purge(s, 90, apply=True, batch=2) == 5
    assert len(rows(engine)) == 3
    event = audit_events(engine, "chat.feedback_purge", "success")[-1]
    assert event.details == {"result_count": 5, "filters": ["older_than_90_days"]} and event.actor_user_id is None
    with Session(engine) as s:
        assert feedback_cleanup.purge(s, 90, apply=True) == 0  # nothing left to delete, and no second audit event
    assert len(audit_events(engine, "chat.feedback_purge")) == 1


def test_the_retention_period_must_be_positive_and_a_missing_table_is_harmless(engine, world):
    with Session(engine) as s:
        with pytest.raises(ValueError):
            feedback_cleanup.purge(s, 0)
    ChatFeedback.__table__.drop(engine)
    with Session(engine) as s:
        assert feedback_cleanup.purge(s, 90, apply=True) == 0


def test_the_command_defaults_to_a_dry_run_and_only_deletes_with_apply(engine, world, monkeypatch, capsys):
    who = institution_user(engine, world)
    add(engine, who.user_id, days_old=200)
    monkeypatch.setattr("app.db.get_session_factory", lambda: sessionmaker(bind=engine))
    assert feedback_cleanup.main([]) == 0
    assert "Would delete 1 feedback row(s) older than 90 days. (dry run: nothing was changed)" in capsys.readouterr().out
    assert len(rows(engine)) == 1
    assert feedback_cleanup.main(["--apply"]) == 0
    assert "Deleted 1 feedback row(s)" in capsys.readouterr().out and rows(engine) == []


def test_the_command_refuses_a_nonsensical_period(engine, monkeypatch):
    monkeypatch.setattr("app.db.get_session_factory", lambda: sessionmaker(bind=engine))
    with pytest.raises(SystemExit):
        feedback_cleanup.main(["--days", "0"])


def test_the_command_says_so_when_the_table_does_not_exist(engine, monkeypatch, capsys):
    ChatFeedback.__table__.drop(engine)
    monkeypatch.setattr("app.db.get_session_factory", lambda: sessionmaker(bind=engine))
    assert feedback_cleanup.main(["--apply"]) == 0
    assert "migration 0007 has not been applied" in capsys.readouterr().out


def test_nothing_in_the_api_runs_the_cleanup_on_its_own():
    import app.main as main_module
    from pathlib import Path

    for path in Path(main_module.__file__).parent.rglob("*.py"):
        if path.name != "feedback_cleanup.py":
            code = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.lstrip().startswith(("import ", "from ")) or "purge(" in ln]
            assert not any("feedback_cleanup" in ln for ln in code), path.name


def test_the_default_retention_is_ninety_days():
    assert feedback_cleanup.DEFAULT_DAYS == 90 == module.RETENTION_DAYS
    assert func is not None
