"""The maturity calendar and the reviewer workload dashboard: scoping, date boundaries and metric arithmetic."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.insights.derive import today
from app.models import FDR, FDRStatusHistory
from app.timeutil import utcnow
from tests.api_support import API, build_client, make_settings, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_review_api import decide, submitted
from tests.test_fdr_write_api import create, force_status, institution_user, world  # noqa: F401

pytestmark = sqlite_decimal_warning


def on(days):
    return (today() + timedelta(days=days)).isoformat()


def fdr(owner, world, number, maturity_in, **extra):
    return create(owner, world, fdr_number=number, issue_date=on(-400), maturity_date=on(maturity_in), **extra)


def calendar(actor, start=-10, end=80, **query):
    q = "".join(f"&{k}={v}" for k, v in query.items())
    return actor.get(f"/calendar?start={on(start)}&end={on(end)}{q}")


def numbers(response):
    assert response.status_code == 200, response.text
    return [e["fdr_number"] for e in response.json()["events"]]


# ---- calendar ---------------------------------------------------------------------------------------------------
def test_events_are_the_real_maturity_dates_in_range_in_date_order(engine, world):
    owner = institution_user(engine, world)
    for number, days in [("CAL-3", 20), ("CAL-1", 3), ("CAL-2", 10), ("CAL-OUT", 400), ("CAL-PAST", -3)]:
        fdr(owner, world, number, days)
    body = calendar(owner, start=0, end=30).json()
    assert [e["fdr_number"] for e in body["events"]] == ["CAL-1", "CAL-2", "CAL-3"]
    assert body["total"] == 3 and not body["truncated"] and body["today"] == today().isoformat()
    first = body["events"][0]
    assert first["maturity_date"] == on(3) and first["days_until"] == 3 and first["link"].startswith("/fdrs/")
    assert set(first) == {"fdr_id", "fdr_number", "institution_code", "institution_name", "maturity_date", "status", "days_until", "marker", "link"}


def test_the_range_includes_both_boundary_days_and_nothing_beyond(engine, world):
    owner = institution_user(engine, world)
    for number, days in [("EDGE-BEFORE", -1), ("EDGE-START", 0), ("EDGE-END", 30), ("EDGE-AFTER", 31)]:
        fdr(owner, world, number, days)
    assert numbers(calendar(owner, start=0, end=30)) == ["EDGE-START", "EDGE-END"]


def test_markers_apply_only_to_live_fdrs_and_a_draft_is_never_flagged(engine, world):
    owner, _ = submitted(engine, world, None, fdr_number="MK-LIVE", issue_date=on(-400), maturity_date=on(-5))  # live and past
    submitted(engine, world, owner, fdr_number="MK-SOON", issue_date=on(-400), maturity_date=on(10))
    submitted(engine, world, owner, fdr_number="MK-LATER", issue_date=on(-400), maturity_date=on(60))
    fdr(owner, world, "MK-DRAFT", -5)
    markers = {e["fdr_number"]: e["marker"] for e in calendar(owner).json()["events"]}
    assert markers == {"MK-LIVE": "OVERDUE", "MK-SOON": "SOON", "MK-LATER": "UPCOMING", "MK-DRAFT": "NONE"}


def test_the_summary_counts_live_fdrs_by_distance_independent_of_the_range_on_screen(engine, world):
    owner, _ = submitted(engine, world, None, fdr_number="SUM-PAST", issue_date=on(-400), maturity_date=on(-2))
    for number, days in [("SUM-5", 5), ("SUM-20", 20), ("SUM-80", 80), ("SUM-200", 200)]:
        submitted(engine, world, owner, fdr_number=number, issue_date=on(-400), maturity_date=on(days))
    fdr(owner, world, "SUM-DRAFT", 3)  # a draft is not live and is never counted
    summary = calendar(owner, start=0, end=1).json()["summary"]  # a range that contains none of them
    assert summary == {"overdue": 1, "next_7": 1, "next_30": 2, "next_90": 3}


def test_institution_users_see_only_their_own_events_and_cannot_widen_with_a_filter(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    fdr(a, world, "OWN-A", 5)
    fdr(b, world, "OWN-B", 6)
    assert numbers(calendar(a)) == ["OWN-A"] and numbers(calendar(b)) == ["OWN-B"]
    assert numbers(calendar(a, institution_id=world["B"])) == ["OWN-A"]  # the filter cannot reach another institution


def test_reviewers_see_every_institution_but_never_drafts_and_can_filter(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    submitted(engine, world, a, fdr_number="REV-A", issue_date=on(-400), maturity_date=on(5))
    submitted(engine, world, b, fdr_number="REV-B", issue_date=on(-400), maturity_date=on(6))
    fdr(a, world, "REV-DRAFT", 7)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert numbers(calendar(reviewer)) == ["REV-A", "REV-B"]
    assert numbers(calendar(reviewer, institution_id=world["A"])) == ["REV-A"]
    assert numbers(calendar(reviewer, status="SUBMITTED")) == ["REV-A", "REV-B"]
    assert numbers(calendar(reviewer, status="DRAFT")) == []  # a filter cannot reveal what the role may not see


def test_an_unknown_institution_filter_returns_nothing_not_an_error_that_leaks(engine, world):
    owner = institution_user(engine, world)
    fdr(owner, world, "ANY-1", 5)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert numbers(calendar(reviewer, institution_id=uuid.uuid4())) == []


@pytest.mark.parametrize("query", ["start=2026-02-01&end=2026-01-01", "start=2026-01-01&end=2026-12-31", "start=bad&end=2026-01-01", "end=2026-01-01"])
def test_the_range_is_validated(engine, world, query):
    assert institution_user(engine, world).get(f"/calendar?{query}").status_code == 422


def test_the_calendar_needs_a_session_and_is_not_cached(engine, world):
    assert build_client(engine).get(API + f"/calendar?start={on(0)}&end={on(5)}").status_code == 401
    owner = institution_user(engine, world)
    assert calendar(owner).headers["cache-control"] == "no-store"


def test_the_event_list_is_capped_and_says_so(engine, world, monkeypatch):
    import app.api.calendar as module

    monkeypatch.setattr(module, "MAX_EVENTS", 2)
    owner = institution_user(engine, world)
    for i in range(3):
        fdr(owner, world, f"CAP-{i}", 2 + i)
    body = calendar(owner).json()
    assert len(body["events"]) == 2 and body["total"] == 3 and body["truncated"] is True


# ---- workload -----------------------------------------------------------------------------------------------------
def workload(actor, days=30):
    return actor.get(f"/review/workload?days={days}")


def set_submitted_at(engine, fdr_id, when):
    with Session(engine) as s:
        record = s.get(FDR, uuid.UUID(str(fdr_id)))
        record.submitted_at = when
        s.commit()


def test_only_reviewers_approvers_and_administrators_may_see_the_workload(engine, world):
    for role, expected in [("AICTE_REVIEWER", 200), ("AICTE_APPROVER", 200), ("ADMIN", 200), ("AUDITOR", 403)]:
        assert workload(sign_in(engine, role=role)).status_code == expected, role
    assert workload(institution_user(engine, world)).status_code == 403
    assert build_client(engine).get(API + "/review/workload").status_code == 401


@pytest.mark.parametrize("days", [0, 1, 45, 365])
def test_the_period_is_one_of_three_fixed_windows(engine, world, days):
    assert workload(sign_in(engine, role="AICTE_REVIEWER"), days).status_code == 422


def test_pending_counts_ages_and_buckets_come_from_the_real_submission_times(engine, world):
    owner = institution_user(engine, world)
    records = [submitted(engine, world, owner, fdr_number=f"PEND-{i}")[1] for i in range(4)]
    now = utcnow()
    for record, age in zip(records, [timedelta(hours=1), timedelta(days=3), timedelta(days=9), timedelta(days=20)]):
        set_submitted_at(engine, record["id"], now - age)
    body = workload(sign_in(engine, role="AICTE_REVIEWER")).json()
    assert body["pending"] == 4 and body["oldest_pending_days"] == 20 and body["pending_missing_timestamp"] == 0
    assert {b["key"]: b["count"] for b in body["waiting"]} == {"D2": 1, "D7": 1, "D14": 1, "D14P": 1}
    assert body["queue_link"] == "/review"


def test_a_pending_record_without_a_submission_time_is_counted_but_never_aged(engine, world):
    _, record = submitted(engine, world, None, fdr_number="NOTIME-1")
    with Session(engine) as s:
        s.get(FDR, uuid.UUID(record["id"])).submitted_at = None
        s.commit()
    body = workload(sign_in(engine, role="AICTE_REVIEWER")).json()
    assert body["pending"] == 1 and body["pending_missing_timestamp"] == 1
    assert body["oldest_pending_at"] is None and sum(b["count"] for b in body["waiting"]) == 0


def test_without_a_configured_threshold_no_breach_is_reported(engine, world):
    submitted(engine, world, None, fdr_number="NOSLA-1")
    sla = workload(sign_in(engine, role="AICTE_REVIEWER")).json()["sla"]
    assert sla["configured"] is False and sla["beyond"] is None and sla["approaching"] is None
    assert "No review deadline is configured" in sla["note"]


def test_a_configured_threshold_counts_records_beyond_and_approaching_it(engine, world):
    owner = institution_user(engine, world)
    records = [submitted(engine, world, owner, fdr_number=f"SLA-{i}")[1] for i in range(3)]
    now = utcnow()
    for record, age in zip(records, [timedelta(days=1), timedelta(days=4.5), timedelta(days=8)]):
        set_submitted_at(engine, record["id"], now - age)
    reviewer = sign_in(engine, role="AICTE_REVIEWER", settings=make_settings(review_sla_days=5))
    sla = workload(reviewer).json()["sla"]
    assert sla == {**sla, "configured": True, "threshold_days": 5, "beyond": 1, "approaching": 1}
    assert "not an AICTE rule" in sla["note"]


def test_completed_reviews_are_the_real_decisions_in_the_window_by_outcome_and_role(engine, world):
    owner = institution_user(engine, world)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    approver = sign_in(engine, role="AICTE_APPROVER")
    one = submitted(engine, world, owner, fdr_number="DONE-1")[1]
    two = submitted(engine, world, owner, fdr_number="DONE-2")[1]
    three = submitted(engine, world, owner, fdr_number="DONE-3")[1]
    submitted(engine, world, owner, fdr_number="STILL-PENDING")
    assert decide(approver, "accept", one).status_code == 200
    assert decide(reviewer, "request-correction", two).status_code == 200
    assert decide(approver, "reject", three).status_code == 200
    body = workload(reviewer).json()
    assert body["completed_total"] == 3 and body["pending"] == 1
    assert {c["key"]: c["count"] for c in body["completed"]} == {"ACCEPTED": 1, "REJECTED": 1, "CORRECTION_REQUESTED": 1}
    assert {r["role"]: r["count"] for r in body["by_role"]} == {"AICTE_APPROVER": 2, "AICTE_REVIEWER": 1}
    assert body["mine"] == 1 and body["by_person"] is None  # a reviewer sees her own count and never a list of colleagues
    assert {d["key"]: d["count"] for d in body["distribution"]} == {"SUBMITTED": 1, "CORRECTION_REQUESTED": 1, "ACCEPTED": 1, "REJECTED": 1}


def test_only_administrators_see_names_and_they_have_no_personal_count(engine, world):
    owner = institution_user(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    record = submitted(engine, world, owner, fdr_number="WHO-1")[1]
    assert decide(approver, "accept", record).status_code == 200
    admin = workload(sign_in(engine, role="ADMIN")).json()
    assert admin["mine"] is None and [(p["role"], p["count"]) for p in admin["by_person"]] == [("AICTE_APPROVER", 1)]
    assert admin["by_person"][0]["name"]
    for role in ("AICTE_REVIEWER", "AICTE_APPROVER"):
        assert workload(sign_in(engine, role=role)).json()["by_person"] is None


def test_decisions_outside_the_window_are_not_counted(engine, world):
    owner = institution_user(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    record = submitted(engine, world, owner, fdr_number="OLD-1")[1]
    assert decide(approver, "accept", record).status_code == 200
    assert workload(approver, 7).json()["completed_total"] == 1
    with Session(engine) as s:
        # history is append-only on PostgreSQL, so add an old decision instead of editing the real one
        s.add(FDRStatusHistory(
            fdr_id=uuid.UUID(record["id"]), from_status="SUBMITTED", to_status="REJECTED", actor_user_id=approver.user_id,
            actor_role="AICTE_APPROVER", occurred_at=utcnow() - timedelta(days=60),
        ))
        s.commit()
    assert workload(approver, 30).json()["completed_total"] == 1
    assert workload(approver, 90).json()["completed_total"] == 2


def test_turnaround_is_measured_from_the_latest_earlier_submission_and_excludes_what_it_cannot_measure(engine, world):
    owner = institution_user(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    record = submitted(engine, world, owner, fdr_number="TAT-1")[1]
    fid = uuid.UUID(record["id"])
    now = utcnow()
    with Session(engine) as s:
        # Synthetic, fully known history for one more FDR: submitted 30 h ago, decided 6 h ago -> 24 h; and a second
        # decision with no earlier submission on record at all.
        s.add_all([
            FDRStatusHistory(fdr_id=fid, from_status="DRAFT", to_status="SUBMITTED", actor_user_id=owner.user_id, actor_role="INSTITUTION_USER", occurred_at=now - timedelta(hours=30)),
            FDRStatusHistory(fdr_id=fid, from_status="SUBMITTED", to_status="ACCEPTED", actor_user_id=approver.user_id, actor_role="AICTE_APPROVER", occurred_at=now - timedelta(hours=6)),
        ])
        s.commit()
    body = workload(approver).json()["turnaround"]
    assert body["measured"] >= 1 and body["excluded"] == 0
    assert body["average_hours"] is not None and body["median_hours"] is not None
    assert "excluded and counted" in body["note"]


def test_turnaround_is_empty_not_zero_when_nothing_can_be_measured(engine, world):
    body = workload(sign_in(engine, role="AICTE_REVIEWER")).json()["turnaround"]
    assert body == {**body, "measured": 0, "excluded": 0, "average_hours": None, "median_hours": None}


def test_assignment_is_reported_as_not_tracked_and_no_counts_are_invented(engine, world):
    body = workload(sign_in(engine, role="AICTE_REVIEWER")).json()
    assert body["assignment_tracked"] is False and "Assignment not tracked" in body["assignment_note"]
    assert not any("assigned" in key or "unassigned" in key for key in body)


def test_the_workload_never_includes_drafts_or_another_roles_blind_spots(engine, world):
    owner = institution_user(engine, world)
    create(owner, world, fdr_number="WL-DRAFT")
    body = workload(sign_in(engine, role="AICTE_REVIEWER")).json()
    assert body["pending"] == 0 and all(d["count"] == 0 for d in body["distribution"])
    force_status(engine, create(owner, world, fdr_number="WL-WD")["id"], "WITHDRAWN")
    assert sum(d["count"] for d in workload(sign_in(engine, role="AICTE_REVIEWER")).json()["distribution"]) == 0
