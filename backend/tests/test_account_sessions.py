"""Account security: own sessions, revocation that is really enforced, sign-in activity, administrator revocation."""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.account import describe_agent, handle_for, mask_network
from app.models import AuthSession, User
from app.timeutil import utcnow
from tests.api_support import API, PW, Actor, audit_events, build_client, csrf_headers, login, make_settings, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_write_api import institution_user, world  # noqa: F401

pytestmark = sqlite_decimal_warning


def another_session(engine, actor: Actor, *, agent: str | None = None) -> Actor:
    """A second browser for the same account (a genuine, separate sign-in)."""
    client = build_client(engine)
    if agent:
        client.headers["User-Agent"] = agent
    response = login(client, actor.email)
    assert response.status_code == 200, response.text
    return Actor(client, csrf_headers(response), actor.user_id, actor.email)


def sessions(actor):
    response = actor.get("/account/sessions")
    assert response.status_code == 200, response.text
    return response.json()


def alive(actor) -> bool:
    return actor.get("/auth/me").status_code == 200


# ---- listing ------------------------------------------------------------------------------------------------------
def test_a_user_sees_their_own_active_sessions_with_the_current_one_marked_first(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    second = another_session(engine, first, agent="Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36")
    body = sessions(first)
    assert len(body["items"]) == 2 and body["idle_timeout_minutes"] > 0 and body["absolute_timeout_minutes"] > 0
    assert [i["current"] for i in body["items"]] == [True, False]
    other = sessions(second)["items"]
    assert [i["current"] for i in other] == [True, False] and other[0]["handle"] != body["items"][0]["handle"]  # "current" is relative to the caller
    assert (other[0]["browser"], other[0]["system"]) == ("Chrome", "Windows")


def test_the_response_never_contains_a_token_a_hash_a_database_id_or_a_raw_address(engine):
    actor = sign_in(engine, role="AICTE_REVIEWER")
    response = actor.get("/account/sessions")
    text = json.dumps(response.json())
    with Session(engine) as s:
        rows = s.scalars(select(AuthSession).where(AuthSession.user_id == actor.user_id)).all()
        secrets = [rows[0].token_hash, str(rows[0].id), rows[0].ip_address or "no-address"]
    cookie = actor.client.cookies.get("fdr_session")
    assert all(secret not in text for secret in secrets) and cookie not in text
    assert set(response.json()["items"][0]) == {"handle", "current", "created_at", "last_seen_at", "expires_at", "browser", "system", "network"}
    assert response.headers["cache-control"] == "no-store"


def test_other_peoples_sessions_are_never_listed(engine):
    a, b = sign_in(engine, role="AICTE_REVIEWER"), sign_in(engine, role="AICTE_REVIEWER")
    assert len(sessions(a)["items"]) == 1 and len(sessions(b)["items"]) == 1
    assert {i["handle"] for i in sessions(a)["items"]}.isdisjoint({i["handle"] for i in sessions(b)["items"]})


def test_ended_and_idle_sessions_are_not_listed(engine):
    actor = sign_in(engine, role="AICTE_REVIEWER")
    stale, ended = another_session(engine, actor), another_session(engine, actor)
    assert len(sessions(actor)["items"]) == 3
    with Session(engine) as s:
        rows = s.scalars(select(AuthSession).where(AuthSession.user_id == actor.user_id).order_by(AuthSession.created_at)).all()
        rows[1].last_seen_at = utcnow() - timedelta(days=3)  # beyond the idle timeout
        rows[2].revoked_at = utcnow()
        s.commit()
    assert len(sessions(actor)["items"]) == 1
    assert stale is not None and ended is not None


def test_handles_are_stable_per_session_and_depend_on_the_server_secret():
    one, two = uuid.uuid4(), uuid.uuid4()
    s1, s2 = make_settings(), make_settings(secret_key="a-completely-different-secret-key-value-0123456789")
    assert handle_for(one, s1) == handle_for(one, s1) != handle_for(two, s1) and handle_for(one, s1) != handle_for(one, s2)
    assert len(handle_for(one, s1)) == 32


def test_the_listing_needs_a_session_and_a_permanent_password(engine):
    assert build_client(engine).get(API + "/account/sessions").status_code == 401
    assert sign_in(engine, role="ADMIN", must_change_password=True).get("/account/sessions").status_code == 403


# ---- revoking one session: really enforced ------------------------------------------------------------------------
def test_revoking_another_session_stops_it_working_at_once_and_leaves_the_rest(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    second = another_session(engine, first)
    assert alive(first) and alive(second)
    target = next(i for i in sessions(first)["items"] if not i["current"])
    response = first.post(f"/account/sessions/{target['handle']}/revoke")
    assert response.status_code == 200 and response.json() == {"revoked": 1}
    assert second.get("/auth/me").status_code == 401  # the revoked session is refused on its very next request
    assert second.get("/fdrs").status_code == 401
    assert alive(first) and len(sessions(first)["items"]) == 1


def test_a_session_revoked_by_its_owner_cannot_be_revived_by_replaying_its_cookie(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    second = another_session(engine, first)
    cookie = second.client.cookies.get("fdr_session")
    first.post(f"/account/sessions/{next(i for i in sessions(first)['items'] if not i['current'])['handle']}/revoke")
    replay = build_client(engine)
    replay.cookies.set("fdr_session", cookie)
    assert replay.get(API + "/auth/me").status_code == 401


def test_the_current_session_cannot_be_revoked_here_and_stays_valid(engine):
    actor = sign_in(engine, role="AICTE_REVIEWER")
    current = sessions(actor)["items"][0]["handle"]
    response = actor.post(f"/account/sessions/{current}/revoke")
    assert response.status_code == 409 and "Sign out" in response.json()["detail"] and alive(actor)


@pytest.mark.parametrize("handle", ["0" * 32, "not-a-handle", "x" * 200])
def test_an_unknown_handle_is_a_plain_404(engine, handle):
    actor = sign_in(engine, role="AICTE_REVIEWER")
    assert actor.post(f"/account/sessions/{handle}/revoke").status_code == 404


def test_one_user_cannot_revoke_anothers_session_and_gets_the_same_answer_as_for_a_missing_one(engine):
    victim = sign_in(engine, role="AICTE_REVIEWER")
    attacker = sign_in(engine, role="AICTE_REVIEWER")
    handle = sessions(victim)["items"][0]["handle"]
    foreign = attacker.post(f"/account/sessions/{handle}/revoke")
    missing = attacker.post(f"/account/sessions/{'0' * 32}/revoke")
    assert foreign.status_code == missing.status_code == 404 and foreign.json() == missing.json()
    assert alive(victim)


def test_revoking_needs_the_csrf_token_and_a_session(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    another_session(engine, first)
    handle = next(i for i in sessions(first)["items"] if not i["current"])["handle"]
    assert first.client.post(f"{API}/account/sessions/{handle}/revoke").status_code == 403  # no CSRF token
    assert build_client(engine).post(f"{API}/account/sessions/{handle}/revoke").status_code in (401, 403)
    assert len(sessions(first)["items"]) == 2


# ---- revoking all others ------------------------------------------------------------------------------------------
def test_signing_out_other_sessions_ends_them_all_and_keeps_this_one(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    others = [another_session(engine, first) for _ in range(2)]
    response = first.post("/account/sessions/revoke-others")
    assert response.status_code == 200 and response.json() == {"revoked": 2}
    assert alive(first) and not any(alive(o) for o in others)
    assert first.post("/account/sessions/revoke-others").json() == {"revoked": 0}


def test_signing_out_other_sessions_never_touches_another_users_sessions(engine):
    mine, theirs = sign_in(engine, role="AICTE_REVIEWER"), sign_in(engine, role="AICTE_REVIEWER")
    another_session(engine, mine)
    mine.post("/account/sessions/revoke-others")
    assert alive(theirs)


# ---- audit ------------------------------------------------------------------------------------------------------
def test_every_revocation_is_audited_without_exposing_a_handle_or_token(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    another_session(engine, first)
    handle = next(i for i in sessions(first)["items"] if not i["current"])["handle"]
    first.post(f"/account/sessions/{handle}/revoke")
    another_session(engine, first)
    first.post("/account/sessions/revoke-others")
    one, many = audit_events(engine, "auth.session_revoked", "success")[-1], audit_events(engine, "auth.sessions_revoked", "success")[-1]
    assert one.actor_user_id == first.user_id and one.details == {"reason": "user_request"} and many.details == {"result_count": 1}
    assert handle not in json.dumps([one.details, many.details, one.entity_id])


# ---- activity --------------------------------------------------------------------------------------------------
def test_activity_lists_the_users_own_sign_ins_and_the_previous_successful_one(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    assert first.get("/account/activity").json()["previous_sign_in_at"] is None  # only the current sign-in so far
    another_session(engine, first)
    body = first.get("/account/activity").json()
    assert body["previous_sign_in_at"] is not None
    assert [e["event"] for e in body["items"]] == ["Sign-in", "Sign-in"] and {e["outcome"] for e in body["items"]} == {"success"}


def test_a_failed_sign_in_naming_the_account_shows_up_as_a_failed_attempt(engine):
    actor = sign_in(engine, role="AICTE_REVIEWER")
    bad = build_client(engine)
    assert login(bad, actor.email, password="the wrong password entirely").status_code == 401
    events = actor.get("/account/activity").json()["items"]
    assert events[0]["event"] == "Failed sign-in attempt" and events[0]["outcome"] == "failure"
    assert "bad_password" not in json.dumps(events)  # the reason code is not shown


def test_activity_never_includes_anyone_elses_events(engine):
    a, b = sign_in(engine, role="AICTE_REVIEWER"), sign_in(engine, role="AICTE_REVIEWER")
    another_session(engine, b)
    assert len(a.get("/account/activity").json()["items"]) == 1
    assert len(b.get("/account/activity").json()["items"]) == 2


def test_activity_records_logout_and_revocations_in_words(engine):
    first = sign_in(engine, role="AICTE_REVIEWER")
    another_session(engine, first)
    first.post("/account/sessions/revoke-others")
    events = [e["event"] for e in first.get("/account/activity").json()["items"]]
    assert "Your other sessions were ended" in events and events.count("Sign-in") == 2


# ---- administrators ----------------------------------------------------------------------------------------------
def test_an_administrator_can_end_every_session_of_a_user_and_it_is_enforced(engine):
    admin = sign_in(engine, role="ADMIN")
    user = sign_in(engine, role="AICTE_REVIEWER")
    second = another_session(engine, user)
    assert alive(user) and alive(second)
    response = admin.post(f"/users/{user.user_id}/sessions/revoke")
    assert response.status_code == 200 and response.json() == {"revoked": 2}
    assert not alive(user) and not alive(second) and alive(admin)
    event = audit_events(engine, "users.sessions_revoked", "success")[-1]
    assert event.actor_user_id == admin.user_id and event.details == {"target_user_id": str(user.user_id), "result_count": 2}
    # the user can sign in again afterwards: their account is untouched
    assert login(build_client(engine), user.email).status_code == 200


@pytest.mark.parametrize("role", ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "AUDITOR"])
def test_only_administrators_may_end_someone_elses_sessions(engine, world, role):
    target = sign_in(engine, role="AICTE_REVIEWER")
    actor = institution_user(engine, world) if role == "INSTITUTION_USER" else sign_in(engine, role=role)
    assert actor.post(f"/users/{target.user_id}/sessions/revoke").status_code == 403
    assert alive(target)
    assert audit_events(engine, "authz.denied", "denied")


def test_ending_sessions_of_an_unknown_user_is_a_404_and_needs_csrf(engine):
    admin = sign_in(engine, role="ADMIN")
    assert admin.post(f"/users/{uuid.uuid4()}/sessions/revoke").status_code == 404
    target = sign_in(engine, role="AICTE_REVIEWER")
    assert admin.client.post(f"{API}/users/{target.user_id}/sessions/revoke").status_code == 403
    assert alive(target)


def test_an_administrator_who_ends_their_own_sessions_is_signed_out(engine):
    admin = sign_in(engine, role="ADMIN")
    assert admin.post(f"/users/{admin.user_id}/sessions/revoke").json() == {"revoked": 1}
    assert not alive(admin)


# ---- helpers ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0", ("Edge", "Windows")),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0", ("Firefox", "Linux")),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 Version/17.0 Mobile Safari/604.1", ("Safari", "iOS")),
        ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36", ("Chrome", "Android")),
        ("curl/8.0", ("Unknown browser", "Unknown system")), (None, ("Unknown browser", "Unknown system")),
    ],
)
def test_only_browser_and_system_families_are_derived_from_the_user_agent(agent, expected):
    assert describe_agent(agent) == expected


@pytest.mark.parametrize(
    ("address", "expected"),
    [("203.0.113.57", "203.0.113.x"), ("2001:db8:85a3:0:0:8a2e:370:7334", "2001:0db8:85a3:…"), ("testclient", None), (None, None), ("", None)],
)
def test_network_addresses_are_masked(address, expected):
    assert mask_network(address) == expected


def test_the_user_model_is_untouched_by_listing_and_revoking(engine):
    actor = sign_in(engine, role="AICTE_REVIEWER")
    with Session(engine) as s:
        before = s.get(User, actor.user_id).password_hash
    sessions(actor)
    actor.post("/account/sessions/revoke-others")
    with Session(engine) as s:
        assert s.get(User, actor.user_id).password_hash == before
