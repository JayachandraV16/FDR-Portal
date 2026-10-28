"""Background extraction: the job queue, its workers, and everything that must hold when they fail.

Under test no background threads run (EXTRACTION_WORKERS=0 in conftest); jobs are driven explicitly with
`runner.process_one()` / `run_pending()` unless a test is about the threads themselves. Runs on SQLite
and, when TEST_DATABASE_URL is set, on the dedicated PostgreSQL test database (the real-concurrency
tests need PostgreSQL's row locking and are skipped on SQLite). All documents are synthetic.
"""

import json
import logging
import subprocess
import threading
import time
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.extraction.jobs import ExtractionRunner, RunnerConfig, enqueue
from app.extraction.sandbox import Busy, SandboxedExtractor
from app.extraction.text import LocalTextExtractor
from app.main import app
from app.models import FDRDocument, FDRDocumentExtraction, FDRExtractionJob
from app.timeutil import utcnow
from tests.api_support import API, audit_events, audit_rows, build_client, make_settings, sign_in
from tests.extraction_support import FakeOcr, make_image_pdf
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_documents_api import _private_store, store, stored_files  # noqa: F401
from tests.test_fdr_extraction_api import (  # noqa: F401  (fixtures are used by name)
    SENTINEL,
    extraction_rows,
    ocr,
    path,
    receipt_pdf,
    reviewer,
    submitted_with,
)
from tests.test_fdr_write_api import create, institution_user, world  # noqa: F401

pytestmark = sqlite_decimal_warning


@pytest.fixture
def runner(engine, store, ocr):
    return ExtractionRunner(lambda: Session(engine), store, LocalTextExtractor(ocr), RunnerConfig(poll_seconds=0.05, lease_seconds=60))


def jobs(engine) -> list[FDRExtractionJob]:
    with Session(engine) as s:
        return list(s.scalars(select(FDRExtractionJob).order_by(FDRExtractionJob.requested_at, FDRExtractionJob.id)))


def job_row(engine, job_id) -> FDRExtractionJob:
    with Session(engine) as s:
        return s.get(FDRExtractionJob, uuid.UUID(str(job_id)))


def post(who, fdr, doc):
    return who.post(path(fdr, doc, "extractions"))


class Spy:
    """Wraps an extractor to count calls and run a hook (to look at the world mid-extraction)."""

    def __init__(self, inner, hook=None):
        self.inner, self.hook, self.calls = inner, hook, 0

    def extract(self, data, content_type, limits):
        self.calls += 1
        if self.hook:
            self.hook()
        return self.inner.extract(data, content_type, limits)


class Exploding:
    def extract(self, *_a, **_k):
        raise RuntimeError(f"boom with document text {SENTINEL}")


class BusyAlways:
    def extract(self, *_a, **_k):
        raise Busy


# ---- accepting a request ------------------------------------------------------------------------
def test_a_request_is_accepted_at_once_as_a_pending_job_and_nothing_runs_yet(engine, world, runner):
    spy = runner.extractor = Spy(runner.extractor)
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    r = post(rev, fdr, doc)
    assert r.status_code == 202 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["status"] == "PENDING" and body["attempts"] == 0 and body["document_id"] == doc["id"]
    assert body["started_at"] is None and body["finished_at"] is None and body["error_code"] is None and body["error_message"] is None
    assert spy.calls == 0 and extraction_rows(engine) == []  # the request did no analysis at all
    (job,) = jobs(engine)
    assert str(job.id) == body["id"] and job.status == "PENDING" and job.requested_by == rev.user_id and job.claimed_by is None
    assert [e.action for e in audit_rows(engine) if e.action.startswith("document.extract")] == ["document.extract_requested"]
    c = rev.get(path(fdr, doc, "comparison")).json()
    assert c["extraction"] is None and c["fields"] == [] and c["job"]["id"] == body["id"] and c["job"]["status"] == "PENDING"


def test_a_worker_completes_the_job_and_links_the_result(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    job_id = post(rev, fdr, doc).json()["id"]
    assert runner.process_one() is True and runner.process_one() is False  # one job, then nothing left
    job = job_row(engine, job_id)
    assert job.status == "COMPLETED" and job.attempts == 1 and job.error_code is None
    assert job.started_at is not None and job.finished_at is not None and job.claimed_by is None and job.lease_expires_at is None
    (extraction,) = extraction_rows(engine)
    assert job.extraction_id == extraction.id and extraction.status == "COMPLETED" and extraction.requested_by == rev.user_id
    c = rev.get(path(fdr, doc, "comparison")).json()
    assert c["job"]["status"] == "COMPLETED" and c["extraction"]["id"] == str(extraction.id) and len(c["fields"]) == 9


def test_the_running_state_is_visible_while_a_worker_holds_the_job(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    job_id = post(rev, fdr, doc).json()["id"]
    seen = {}

    def look():
        seen["job"] = job_row(engine, job_id)
        seen["api"] = rev.get(path(fdr, doc, "comparison")).json()["job"]
        seen["again"] = post(rev, fdr, doc)

    runner.extractor = Spy(runner.extractor, look)
    runner.run_pending()
    assert seen["job"].status == "RUNNING" and seen["job"].claimed_by and seen["job"].lease_expires_at and seen["job"].attempts == 1
    assert seen["api"]["status"] == "RUNNING" and seen["api"]["started_at"] is not None
    # asking again while it runs returns the same job and queues nothing new
    assert seen["again"].status_code == 200 and seen["again"].json()["id"] == job_id and seen["again"].json()["status"] == "RUNNING"
    assert len(jobs(engine)) == 1


def test_asking_again_while_a_job_is_pending_is_idempotent(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    first = post(rev, fdr, doc)
    second = post(rev, fdr, doc)
    third = post(reviewer(engine, "AICTE_APPROVER"), fdr, doc)  # another reviewer asking is the same request
    assert first.status_code == 202 and second.status_code == 200 and third.status_code == 200
    assert len({first.json()["id"], second.json()["id"], third.json()["id"]}) == 1
    assert len(jobs(engine)) == 1 and len(audit_events(engine, "document.extract_requested")) == 1
    runner.run_pending()
    assert len(extraction_rows(engine)) == 1


def test_the_database_itself_refuses_two_active_jobs_for_one_document(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    post(rev, fdr, doc)
    with Session(engine) as s:
        from sqlalchemy.exc import IntegrityError

        s.add(FDRExtractionJob(document_id=uuid.UUID(doc["id"]), fdr_id=uuid.UUID(fdr["id"]), requested_by=rev.user_id, status="PENDING"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_a_lost_race_between_two_requests_returns_the_winners_job(engine, world, monkeypatch):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    with Session(engine) as winner:
        won, created = enqueue(winner, document_id=uuid.UUID(doc["id"]), fdr_id=uuid.UUID(fdr["id"]), user_id=rev.user_id, queue_limit=5)
        winner.commit()
        winner_id = won.id
    with Session(engine) as loser:
        real = loser.scalars
        calls = {"n": 0}

        def blind_first_lookup(*a, **k):  # the first look for an active job sees nothing (as in a real race)
            calls["n"] += 1
            result = real(*a, **k)
            return type("Empty", (), {"first": staticmethod(lambda: None)})() if calls["n"] == 1 else result

        monkeypatch.setattr(loser, "scalars", blind_first_lookup)
        job, created = enqueue(loser, document_id=uuid.UUID(doc["id"]), fdr_id=uuid.UUID(fdr["id"]), user_id=rev.user_id, queue_limit=5)
        assert created is False and job.id == winner_id
    assert len(jobs(engine)) == 1


# ---- retry, authorization ----------------------------------------------------------------------
def test_a_failed_job_can_be_retried_and_the_history_is_kept(engine, world, runner, ocr):
    _owner, fdr, doc = submitted_with(engine, world, make_image_pdf())
    rev = reviewer(engine)
    first = post(rev, fdr, doc).json()
    runner.run_pending()
    failed = job_row(engine, first["id"])
    assert failed.status == "FAILED" and failed.error_code == "OCR_UNAVAILABLE"
    c = rev.get(path(fdr, doc, "comparison")).json()
    assert c["job"]["status"] == "FAILED" and c["job"]["error_code"] == "OCR_UNAVAILABLE" and "OCR" in c["job"]["error_message"]
    ocr.is_available, ocr.text = True, "FDR No: 998877\nMaturity Date: 15 Jan 2027"
    retry = post(rev, fdr, doc)
    assert retry.status_code == 202 and retry.json()["id"] != first["id"] and retry.json()["status"] == "PENDING"
    runner.run_pending()
    assert [j.status for j in jobs(engine)] == ["FAILED", "COMPLETED"]
    assert [e.status for e in extraction_rows(engine)] == ["FAILED", "COMPLETED"]
    assert [e.action for e in audit_rows(engine) if e.action.startswith("document.extract")] == [
        "document.extract_requested", "document.extract_failed", "document.extract_requested", "document.extract_completed"]


def test_a_completed_job_can_be_run_again(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    post(rev, fdr, doc)
    runner.run_pending()
    assert post(rev, fdr, doc).status_code == 202
    runner.run_pending()
    assert [j.status for j in jobs(engine)] == ["COMPLETED", "COMPLETED"]


@pytest.mark.parametrize("role", ["INSTITUTION_USER", "ADMIN", "AUDITOR"])
def test_roles_without_review_access_cannot_request_or_retry(engine, world, runner, role):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    post(rev, fdr, doc)
    runner.run_pending()
    actor = institution_user(engine, world, "A") if role == "INSTITUTION_USER" else sign_in(engine, role=role)
    assert post(actor, fdr, doc).status_code == 403
    assert len(jobs(engine)) == 1 and len(audit_events(engine, "authz.denied", "denied")) == 1


def test_a_request_for_a_document_the_caller_cannot_see_is_refused_before_anything_is_queued(engine, world):
    owner = institution_user(engine, world, "A")
    fdr = create(owner, world)  # a DRAFT: not visible to reviewers
    from tests.test_fdr_documents_api import up

    doc = up(owner, fdr["id"], receipt_pdf(), "draft.pdf").json()
    assert post(reviewer(engine), fdr, doc).status_code == 404
    assert jobs(engine) == []


def test_unauthenticated_and_forged_requests_queue_nothing(engine, world):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    assert build_client(engine).post(API + path(fdr, doc, "extractions")).status_code == 401
    assert rev.client.post(API + path(fdr, doc, "extractions")).status_code == 403  # no CSRF token
    assert rev.client.post(API + path(fdr, doc, "extractions"), headers={**rev.headers, "Origin": "https://evil.example"}).status_code == 403
    assert jobs(engine) == []


def test_results_are_authorised_again_when_read_not_just_when_requested(engine, world, runner):
    owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    approver = reviewer(engine, "AICTE_APPROVER")
    post(approver, fdr, doc)
    runner.run_pending()
    for who in (owner, sign_in(engine, role="ADMIN"), sign_in(engine, role="AUDITOR")):
        for tail in ("comparison", "extraction"):
            assert who.get(path(fdr, doc, tail)).status_code == 403
    assert reviewer(engine).get(path(fdr, doc, "comparison")).status_code == 200


# ---- the queue limit ------------------------------------------------------------------------------
def test_the_queue_limit_refuses_further_requests_until_work_drains(engine, world, runner):
    docs = [submitted_with(engine, world, receipt_pdf(number=f"Q-{i}"), fdr_number=f"Q-{i}") for i in range(3)]
    rev = sign_in(engine, role="AICTE_REVIEWER", settings=make_settings(extraction_queue_limit=2))
    assert [post(rev, fdr, doc).status_code for _o, fdr, doc in docs[:2]] == [202, 202]
    refused = post(rev, docs[2][1], docs[2][2])
    assert refused.status_code == 503 and "queue is full" in refused.json()["detail"]
    assert len(jobs(engine)) == 2
    (event,) = audit_events(engine, "document.extract_requested", "denied")
    assert event.details["reason"] == "queue_full" and event.details["target_document_id"] == docs[2][2]["id"]
    assert post(rev, docs[0][1], docs[0][2]).status_code == 200  # an already-queued document is still answered
    runner.process_one()
    assert post(rev, docs[2][1], docs[2][2]).status_code == 202  # a slot freed up


# ---- ordering, attempts -------------------------------------------------------------------------
def test_jobs_run_in_the_order_they_were_requested(engine, world, runner):
    docs = [submitted_with(engine, world, receipt_pdf(number=f"O-{i}"), fdr_number=f"O-{i}") for i in range(3)]
    rev = reviewer(engine)
    order = [post(rev, fdr, doc).json()["id"] for _o, fdr, doc in docs]
    claimed = []
    for _ in range(3):
        claimed.append(str(runner.claim_next("w")))
    assert claimed == order and runner.claim_next("w") is None


def test_claiming_counts_an_attempt_and_a_busy_sandbox_gives_the_job_back_without_using_one(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    runner.extractor = BusyAlways()
    assert runner.process_one("w1") is True
    job = job_row(engine, job_id)
    assert job.status == "PENDING" and job.attempts == 0 and job.claimed_by is None and job.error_code is None
    assert extraction_rows(engine) == []
    runner.extractor = LocalTextExtractor(FakeOcr(is_available=False))
    runner.run_pending()
    assert job_row(engine, job_id).status == "COMPLETED" and job_row(engine, job_id).attempts == 1


# ---- failures are sanitized ------------------------------------------------------------------------
def test_an_unexpected_error_is_recorded_as_a_sanitized_failure_and_never_leaks_text(engine, world, runner, caplog):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    job_id = post(rev, fdr, doc).json()["id"]
    runner.extractor = Exploding()
    with caplog.at_level(logging.DEBUG):
        runner.run_pending()
    job = job_row(engine, job_id)
    assert job.status == "FAILED" and job.error_code == "WORKER_FAILED" and job.claimed_by is None
    (extraction,) = extraction_rows(engine)
    assert extraction.status == "FAILED" and extraction.error_code == "WORKER_FAILED" and extraction.result["fields"] == []
    c = rev.get(path(fdr, doc, "comparison"))
    assert c.json()["job"]["error_message"] == "The analysis could not be completed. You can try again."
    everything = json.dumps([c.json(), extraction.result, [[e.details, e.action] for e in audit_rows(engine)]]) + caplog.text
    assert SENTINEL not in everything and "boom" not in everything
    (event,) = audit_events(engine, "document.extract_failed")
    assert event.outcome == "failure" and event.details == {
        "target_fdr_id": fdr["id"], "target_document_id": doc["id"], "reason": "WORKER_FAILED"}
    runner.extractor = LocalTextExtractor(FakeOcr(is_available=False))  # and a retry then works
    assert post(rev, fdr, doc).status_code == 202
    runner.run_pending()
    assert [j.status for j in jobs(engine)] == ["FAILED", "COMPLETED"]


def test_a_document_removed_before_the_job_runs_fails_cleanly(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    with Session(engine) as s:
        d = s.get(FDRDocument, uuid.UUID(doc["id"]))
        d.status, d.deleted_at = "DELETED", utcnow()
        s.commit()
    runner.run_pending()
    job = job_row(engine, job_id)
    assert job.status == "FAILED" and job.error_code == "DOCUMENT_UNAVAILABLE"


def test_a_missing_or_altered_file_fails_the_job_with_its_reason(engine, world, runner, store):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    stored_files(store)[0].write_bytes(b"%PDF-1.4 altered")
    first = post(rev, fdr, doc).json()["id"]
    runner.run_pending()
    stored_files(store)[0].unlink()
    second = post(rev, fdr, doc).json()["id"]
    runner.run_pending()
    assert (job_row(engine, first).error_code, job_row(engine, second).error_code) == ("INTEGRITY_FAILED", "FILE_MISSING")


# ---- two workers, one job ------------------------------------------------------------------------
def test_a_job_that_is_already_claimed_cannot_be_claimed_again(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    other = ExtractionRunner(runner.session_factory, runner.store, runner.extractor, runner.config)
    assert str(runner.claim_next("worker-a")) == job_id
    assert other.claim_next("worker-b") is None
    job = job_row(engine, job_id)
    assert job.status == "RUNNING" and job.claimed_by == "worker-a" and job.attempts == 1


def test_a_worker_with_a_stale_view_of_the_queue_cannot_take_a_job_someone_else_just_took(engine, world, runner, monkeypatch):
    """The race itself, made deterministic: this worker saw the job as PENDING (its candidate list is
    stale) but another worker claimed it first. The conditional UPDATE is what stops a second claim."""
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    first = runner.claim_next("worker-a")
    real_scalars = Session.scalars

    def stale_candidates(self, statement, *args, **kwargs):
        if "ORDER BY fdr_extraction_jobs.requested_at" in str(statement):
            return type("Stale", (), {"all": lambda self: [first]})()  # what worker-b read just before worker-a's UPDATE
        return real_scalars(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalars", stale_candidates)
    other = ExtractionRunner(runner.session_factory, runner.store, runner.extractor, runner.config)
    assert other.claim_next("worker-b") is None
    job = job_row(engine, job_id)
    assert job.status == "RUNNING" and job.claimed_by == "worker-a" and job.attempts == 1


def test_simultaneous_workers_never_run_the_same_job_twice(engine, world, store, ocr):
    if "sqlite" in engine.url.drivername:
        pytest.skip("needs PostgreSQL row locking; SQLite serialises writers")
    docs = [submitted_with(engine, world, receipt_pdf(number=f"C-{i}"), fdr_number=f"C-{i}") for i in range(6)]
    rev = reviewer(engine)
    for _o, fdr, doc in docs:
        post(rev, fdr, doc)
    seen, lock = [], threading.Lock()

    class Counting:
        def extract(self, data, content_type, limits):
            with lock:
                seen.append(data)
            time.sleep(0.05)
            return LocalTextExtractor(ocr).extract(data, content_type, limits)

    runners = [ExtractionRunner(lambda: Session(engine), store, Counting(), RunnerConfig(poll_seconds=0.05)) for _ in range(4)]
    threads = [threading.Thread(target=r.run_pending) for r in runners]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not any(t.is_alive() for t in threads)
    assert [j.status for j in jobs(engine)] == ["COMPLETED"] * 6 and all(j.attempts == 1 for j in jobs(engine))
    assert len(seen) == 6 and len(extraction_rows(engine)) == 6  # each job analysed exactly once
    with Session(engine) as s:
        assert verify_chain(s).ok


def test_a_worker_that_lost_its_lease_cannot_overwrite_the_outcome(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    claimed = runner.claim_next("slow-worker")
    with Session(engine) as s:  # the lease runs out while it works, and the sweep takes the job back
        s.execute(update(FDRExtractionJob).values(lease_expires_at=utcnow() - timedelta(seconds=1)))
        s.commit()
    assert runner.recover_interrupted() == 1 and job_row(engine, job_id).status == "PENDING"
    from app.extraction.analysis import analyse
    from app.extraction.types import Limits

    late = analyse(receipt_pdf(), "application/pdf", LocalTextExtractor(), Limits())
    assert runner._finish(claimed, "slow-worker", late) is False  # fenced out
    assert extraction_rows(engine) == [] and job_row(engine, job_id).status == "PENDING"
    assert not audit_events(engine, "document.extract_completed")
    runner.run_pending()  # another worker completes it, once
    assert job_row(engine, job_id).status == "COMPLETED" and len(extraction_rows(engine)) == 1


# ---- interrupted jobs ------------------------------------------------------------------------------
def _expire(engine, job_id, *, attempts=None, minutes=1):
    with Session(engine) as s:
        values = {"lease_expires_at": utcnow() - timedelta(minutes=minutes)}
        if attempts is not None:
            values["attempts"] = attempts
        s.execute(update(FDRExtractionJob).where(FDRExtractionJob.id == uuid.UUID(job_id)).values(**values))
        s.commit()


def test_a_job_whose_worker_died_goes_back_to_the_queue_and_then_completes(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    assert runner.claim_next("dead-worker") is not None  # ... and the process dies here
    assert runner.recover_interrupted() == 0 and job_row(engine, job_id).status == "RUNNING"  # lease still valid
    _expire(engine, job_id)
    assert runner.recover_interrupted() == 1
    job = job_row(engine, job_id)
    assert job.status == "PENDING" and job.claimed_by is None and job.lease_expires_at is None and job.attempts == 1
    runner.run_pending()
    assert job_row(engine, job_id).status == "COMPLETED" and job_row(engine, job_id).attempts == 2


def test_a_job_interrupted_too_often_is_failed_not_retried_forever(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    rev = reviewer(engine)
    job_id = post(rev, fdr, doc).json()["id"]
    runner.claim_next("dead-worker")
    _expire(engine, job_id, attempts=runner.config.max_attempts)
    assert runner.recover_interrupted() == 1
    job = job_row(engine, job_id)
    assert job.status == "FAILED" and job.error_code == "INTERRUPTED" and job.extraction_id is None
    assert rev.get(path(fdr, doc, "comparison")).json()["job"]["error_message"].startswith("The analysis was interrupted")
    (event,) = audit_events(engine, "document.extract_failed")
    assert event.details["reason"] == "INTERRUPTED" and event.outcome == "failure"
    assert post(rev, fdr, doc).status_code == 202  # and it can be retried


# ---- shutdown ---------------------------------------------------------------------------------------
def test_stopping_gives_a_running_job_back_to_the_queue_without_using_an_attempt(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    claimed = runner.claim_next("w1")
    runner._inflight["w1"] = claimed
    assert runner.stop(timeout=0) == [claimed]
    job = job_row(engine, job_id)
    assert job.status == "PENDING" and job.attempts == 0 and job.claimed_by is None and job.lease_expires_at is None
    runner.run_pending()  # the next start picks it up
    assert job_row(engine, job_id).status == "COMPLETED"


def _wait_for(engine, job_id, status, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if job_row(engine, job_id).status == status:
            return True
        time.sleep(0.05)
    return False


def test_worker_threads_pick_up_a_request_and_stop_cleanly(engine, world, store, ocr):
    runner = ExtractionRunner(lambda: Session(engine), store, LocalTextExtractor(ocr), RunnerConfig(workers=2, poll_seconds=0.05))
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    runner.start()
    try:
        job_id = post(reviewer(engine), fdr, doc).json()["id"]
        runner.wake()
        assert _wait_for(engine, job_id, "COMPLETED"), job_row(engine, job_id).status
    finally:
        assert runner.stop(timeout=10) == []
    assert not any(t.is_alive() for t in runner._threads) and runner._threads == []
    assert len(extraction_rows(engine)) == 1


def test_a_shutdown_during_a_job_releases_it_and_the_late_result_is_discarded(engine, world, store, ocr):
    entered, release = threading.Event(), threading.Event()

    class Blocking:
        def extract(self, data, content_type, limits):
            entered.set()
            release.wait(30)
            return LocalTextExtractor(ocr).extract(data, content_type, limits)

    runner = ExtractionRunner(lambda: Session(engine), store, Blocking(), RunnerConfig(workers=1, poll_seconds=0.05))
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    runner.start()
    assert entered.wait(20) and job_row(engine, job_id).status == "RUNNING"
    released = runner.stop(timeout=0.2)  # shutdown arrives while the analysis is still going
    assert [str(j) for j in released] == [job_id]
    job = job_row(engine, job_id)
    assert job.status == "PENDING" and job.attempts == 0 and job.claimed_by is None
    release.set()  # the abandoned worker finishes later ...
    time.sleep(0.5)
    assert extraction_rows(engine) == [] and job_row(engine, job_id).status == "PENDING"  # ... and cannot record anything
    assert not audit_events(engine, "document.extract_completed")
    successor = ExtractionRunner(lambda: Session(engine), store, LocalTextExtractor(ocr), RunnerConfig(poll_seconds=0.05))
    successor.run_pending()
    assert job_row(engine, job_id).status == "COMPLETED" and len(extraction_rows(engine)) == 1


def test_starting_recovers_jobs_left_running_by_a_previous_process(engine, world, store, ocr):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    ExtractionRunner(lambda: Session(engine), store, LocalTextExtractor(ocr), RunnerConfig()).claim_next("previous-process")
    _expire(engine, job_id)
    runner = ExtractionRunner(lambda: Session(engine), store, LocalTextExtractor(ocr), RunnerConfig(workers=1, poll_seconds=0.05))
    runner.start()
    try:
        assert _wait_for(engine, job_id, "COMPLETED")
    finally:
        runner.stop(timeout=10)
    assert job_row(engine, job_id).attempts == 2


def test_the_api_starts_its_workers_with_the_application_and_stops_them_at_shutdown(engine, world, store, ocr, monkeypatch):
    import app.main as main_module

    runner = ExtractionRunner(lambda: Session(engine), store, LocalTextExtractor(ocr), RunnerConfig(workers=1, poll_seconds=0.05))
    import app.db as database

    monkeypatch.setattr(main_module, "get_settings", lambda: make_settings(extraction_workers=1))
    monkeypatch.setattr(database, "get_session_factory", lambda: (lambda: Session(engine)))  # the suite blocks the real engine
    monkeypatch.setattr(ExtractionRunner, "from_settings", classmethod(lambda cls, settings, factory: runner))
    stopped = {}
    real_stop = runner.stop
    monkeypatch.setattr(runner, "stop", lambda timeout=10.0: stopped.setdefault("released", real_stop(timeout)))
    with TestClient(app):
        assert app.state.extraction_runner is runner and any(t.is_alive() for t in runner._threads)
    assert stopped == {"released": []} and runner._threads == []


def test_the_runner_is_built_from_the_settings_and_its_lease_outlasts_the_analysis_limit(engine, store):
    settings = make_settings(extraction_workers=3, extraction_timeout_seconds=20, extraction_max_attempts=5,
                             extraction_poll_seconds=0.5, extraction_max_pages=7, ocr_language="eng+hin")
    built = ExtractionRunner.from_settings(settings, lambda: Session(engine))
    assert (built.config.workers, built.config.max_attempts, built.config.poll_seconds) == (3, 5, 0.5)
    assert built.config.limits.timeout_seconds == 20 and built.config.limits.max_pages == 7
    assert built.config.lease_seconds > built.config.limits.timeout_seconds  # a slow job is not recovered while it runs
    assert isinstance(built.extractor, SandboxedExtractor) and built.extractor.ocr_language == "eng+hin"


def test_the_api_still_starts_when_the_workers_cannot(engine, monkeypatch):
    import app.main as main_module

    def broken(cls, settings, factory):
        raise RuntimeError("no database for workers")

    monkeypatch.setattr(main_module, "get_settings", lambda: make_settings(extraction_workers=2))
    monkeypatch.setattr(ExtractionRunner, "from_settings", classmethod(broken))
    if hasattr(app.state, "extraction_runner"):
        del app.state.extraction_runner
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
    assert not hasattr(app.state, "extraction_runner")


def test_zero_workers_means_none_are_started(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: make_settings(extraction_workers=0))
    if hasattr(app.state, "extraction_runner"):
        del app.state.extraction_runner
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
    assert not hasattr(app.state, "extraction_runner")


# ---- isolation, audit ---------------------------------------------------------------------------------
def test_the_child_process_never_receives_the_database_url_or_secret_key_when_run_by_a_worker(engine, world, runner, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2@db/x")
    monkeypatch.setenv("SECRET_KEY", "super-secret-key-value-0123456789abcdef")
    seen = []
    real = subprocess.run

    def spy(command, **kwargs):
        seen.append(kwargs.get("env"))
        return real(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    runner.extractor = SandboxedExtractor()
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf())
    job_id = post(reviewer(engine), fdr, doc).json()["id"]
    runner.run_pending()
    assert job_row(engine, job_id).status == "COMPLETED"
    children = [env for env in seen if env is not None]
    assert children and all("DATABASE_URL" not in env and "SECRET_KEY" not in env for env in children)
    assert not any("hunter2" in str(v) or "super-secret" in str(v) for env in children for v in env.values())


def test_the_audit_trail_distinguishes_requested_completed_and_failed_without_any_content(engine, world, runner):
    _owner, fdr, doc = submitted_with(engine, world, receipt_pdf(number="PRIVATE-NUMBER-77"), name="Aadhaar 1234.pdf")
    other_owner, fdr2, doc2 = submitted_with(engine, world, make_image_pdf(), fdr_number="SCAN-1")
    rev = reviewer(engine)
    post(rev, fdr, doc)
    post(rev, fdr2, doc2)
    runner.run_pending()
    actions = [(e.action, e.outcome) for e in audit_rows(engine) if e.action.startswith("document.extract")]
    assert actions == [("document.extract_requested", "success")] * 2 + [("document.extract_completed", "success"), ("document.extract_failed", "failure")]
    assert not audit_events(engine, "document.extract")  # the old, ambiguous action name is gone
    for event in audit_rows(engine):
        if event.action.startswith("document.extract"):
            assert event.entity_type == "extraction_job" and set(event.details) <= {
                "target_fdr_id", "target_document_id", "result_count", "reason"}
    dump = json.dumps([[e.details, e.entity_id, e.action] for e in audit_rows(engine)])
    for forbidden in [SENTINEL, "PRIVATE-NUMBER-77", "Main Branch", "1500000", "Write Bank", "Aadhaar"]:
        assert forbidden not in dump
    with Session(engine) as s:
        assert verify_chain(s).ok
