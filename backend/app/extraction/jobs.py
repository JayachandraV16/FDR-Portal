"""Background extraction: a database-backed job queue and the workers that drain it.

Why this shape. Extraction can take tens of seconds, so a request only *enqueues* a job (a row in
`fdr_extraction_jobs`) and returns at once; worker threads inside the API process claim and run jobs.
Using the database as the queue needs no new service and means an accepted job is not lost when the
process restarts: it stays a row and is picked up again.

What is and is not guaranteed.
  * A job is claimed with one atomic conditional UPDATE (PENDING -> RUNNING), so two workers can never
    run the same job at once. The claim carries a lease.
  * A worker that dies leaves a RUNNING row whose lease expires; a recovery sweep (at start-up and
    periodically) puts it back to PENDING, or fails it as INTERRUPTED after `max_attempts` claims.
  * A result is committed only if the worker still holds the job ("fencing"): a worker that was
    released at shutdown, or whose lease was recovered, cannot overwrite the outcome.
  * Delivery is at-least-once across crashes (a recovered job runs again) and exactly-one-result per
    completed run. It is NOT instant: there is no push, workers poll the table, and while the API
    process is down nothing runs. It is also not a distributed queue: it is designed for the workers
    of one deployment, and the queue limit is a soft limit (a burst of simultaneous requests can
    overshoot it by about the number of concurrent requests).

Privacy. Nothing here logs or audits document text or extracted values; failures are recorded as
fixed reason codes. The child process that does the parsing still receives no application secrets
(see sandbox.py).
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditOutcome, write_audit_event
from app.documents.storage import DocumentStore, StorageError, sha256_hex
from app.extraction.analysis import Analysis, analyse, failed
from app.extraction.sandbox import Busy
from app.extraction.text import TextExtractor
from app.extraction.types import ErrorCode, Limits
from app.models import (
    ACTIVE_JOB_STATUSES,
    DocumentStatus,
    FDRDocument,
    FDRDocumentExtraction,
    FDRExtractionJob,
    JobStatus,
    User,
)
from app.timeutil import utcnow

log = logging.getLogger("fdr.extraction")  # messages here never contain document text or values

SWEEP_SECONDS = 30.0


class QueueFull(Exception):
    """The configured number of pending and running jobs has been reached."""


def enqueue(db: Session, *, document_id: uuid.UUID, fdr_id: uuid.UUID, user_id: uuid.UUID,
            queue_limit: int) -> tuple[FDRExtractionJob, bool]:
    """Add a job for a document unless one is already pending or running (then that one is returned
    and nothing is created, which makes a repeated request idempotent). The caller commits.
    Returns (job, created)."""
    def active() -> FDRExtractionJob | None:
        return db.scalars(
            select(FDRExtractionJob).where(
                FDRExtractionJob.document_id == document_id, FDRExtractionJob.status.in_(ACTIVE_JOB_STATUSES)
            )
        ).first()

    existing = active()
    if existing is not None:
        return existing, False
    waiting = db.scalar(
        select(func.count()).select_from(FDRExtractionJob).where(FDRExtractionJob.status.in_(ACTIVE_JOB_STATUSES))
    ) or 0
    if waiting >= queue_limit:
        raise QueueFull
    job = FDRExtractionJob(
        document_id=document_id, fdr_id=fdr_id, requested_by=user_id, status=JobStatus.PENDING.value, requested_at=utcnow()
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race with a simultaneous request for the same document: the unique index refused ours.
        db.rollback()
        existing = active()
        if existing is None:
            raise
        return existing, False
    return job, True


def read_stored(store: DocumentStore, key: str, size: int, checksum: str) -> tuple[bytes | None, str | None]:
    """The stored file's bytes, re-checked against its recorded size and SHA-256, or (None, reason)."""
    try:
        data = store.read(key)
    except StorageError:
        return None, ErrorCode.STORAGE_ERROR
    if data is None:
        return None, ErrorCode.FILE_MISSING
    if len(data) != size or sha256_hex(data) != checksum:
        return None, ErrorCode.INTEGRITY_FAILED
    return data, None


@dataclass(frozen=True)
class RunnerConfig:
    workers: int = 2
    poll_seconds: float = 2.0
    lease_seconds: float = 90.0  # must exceed the analysis time limit, or a slow job would be recovered while running
    max_attempts: int = 3
    limits: Limits = Limits()


class ExtractionRunner:
    """Claims and runs extraction jobs. `process_one` does one job in the calling thread (used by the
    worker threads, and directly by tests and tooling); `start`/`stop` manage the threads."""

    def __init__(self, session_factory, store: DocumentStore, extractor: TextExtractor, config: RunnerConfig):
        self.session_factory = session_factory
        self.store = store
        self.extractor = extractor
        self.config = config
        self.id = uuid.uuid4().hex[:12]
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._inflight: dict[str, uuid.UUID] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings, session_factory) -> "ExtractionRunner":
        from app.api.extractions import limits_from
        from app.extraction.ocr import discover_command
        from app.extraction.sandbox import SandboxedExtractor

        extractor = SandboxedExtractor(discover_command(settings.ocr_command), settings.ocr_language)
        config = RunnerConfig(
            workers=settings.extraction_workers, poll_seconds=settings.extraction_poll_seconds,
            lease_seconds=settings.extraction_timeout_seconds + 60.0, max_attempts=settings.extraction_max_attempts,
            limits=limits_from(settings),
        )
        return cls(session_factory, DocumentStore(settings.storage_dir), extractor, config)

    # ---- claiming ----------------------------------------------------------------------------
    def claim_next(self, worker_id: str) -> uuid.UUID | None:
        """Atomically take the oldest PENDING job, or None. Two workers racing for the same row cannot
        both succeed: the UPDATE only applies while the row is still PENDING."""
        with self.session_factory() as s:
            candidates = s.scalars(
                select(FDRExtractionJob.id).where(FDRExtractionJob.status == JobStatus.PENDING.value)
                .order_by(FDRExtractionJob.requested_at, FDRExtractionJob.id).limit(5)
            ).all()
            for job_id in candidates:
                now = utcnow()
                taken = s.execute(
                    update(FDRExtractionJob)
                    .where(FDRExtractionJob.id == job_id, FDRExtractionJob.status == JobStatus.PENDING.value)
                    .values(
                        status=JobStatus.RUNNING.value, claimed_by=worker_id, started_at=now,
                        lease_expires_at=now + timedelta(seconds=self.config.lease_seconds),
                        attempts=FDRExtractionJob.attempts + 1,
                    ),
                    execution_options={"synchronize_session": False},
                )
                s.commit()
                if taken.rowcount == 1:
                    return job_id
        return None

    # ---- running -----------------------------------------------------------------------------
    def process_one(self, worker_id: str | None = None) -> bool:
        """Claim and run one job. Returns False when there was nothing to claim."""
        worker_id = worker_id or f"{self.id}-inline"
        job_id = self.claim_next(worker_id)
        if job_id is None:
            return False
        with self._lock:
            self._inflight[worker_id] = job_id
        try:
            self._execute(job_id, worker_id)
        except Busy:
            self._release(job_id, worker_id)  # every sandbox slot is taken: try again later, no attempt used
            time.sleep(min(1.0, self.config.poll_seconds))
        except Exception:  # never leave a claimed job RUNNING because of a bug: record a sanitized failure
            log.warning("extraction job %s failed unexpectedly (details suppressed)", job_id)
            try:
                self._finish(job_id, worker_id, failed(ErrorCode.WORKER_FAILED))
            except Exception:
                log.warning("extraction job %s could not be marked failed; its lease will expire", job_id)
        finally:
            with self._lock:
                self._inflight.pop(worker_id, None)
        return True

    def run_pending(self, max_jobs: int = 1000) -> int:
        """Run jobs in this thread until none is claimable. For tests and tooling."""
        done = 0
        while done < max_jobs and self.process_one():
            done += 1
        return done

    def _execute(self, job_id: uuid.UUID, worker_id: str) -> None:
        with self.session_factory() as s:
            job = s.get(FDRExtractionJob, job_id)
            doc = s.get(FDRDocument, job.document_id)
            usable = doc is not None and doc.status == DocumentStatus.ACTIVE.value
            stored = (doc.storage_key, doc.size_bytes, doc.sha256, doc.content_type) if usable else None
        if stored is None:
            self._finish(job_id, worker_id, failed(ErrorCode.DOCUMENT_UNAVAILABLE))
            return
        key, size, checksum, content_type = stored
        data, problem = read_stored(self.store, key, size, checksum)
        analysis = failed(problem) if problem is not None else analyse(data, content_type, self.extractor, self.config.limits)
        self._finish(job_id, worker_id, analysis)

    def _finish(self, job_id: uuid.UUID, worker_id: str, analysis: Analysis) -> bool:
        """Record the result and the job's end in ONE transaction, only if this worker still holds the
        job. Returns False (and records nothing) if the lease was lost."""
        with self.session_factory() as s:
            job = s.get(FDRExtractionJob, job_id)
            fdr_id, document_id, requested_by = job.fdr_id, job.document_id, job.requested_by
            now = utcnow()
            extraction = FDRDocumentExtraction(
                document_id=document_id, fdr_id=fdr_id, status=analysis.status, error_code=analysis.error_code,
                extractor=analysis.extractor[:120], result=analysis.result, requested_by=requested_by, requested_at=now,
            )
            s.add(extraction)
            s.flush()
            ok = analysis.status == "COMPLETED"
            taken = s.execute(
                update(FDRExtractionJob)
                .where(
                    FDRExtractionJob.id == job_id, FDRExtractionJob.status == JobStatus.RUNNING.value,
                    FDRExtractionJob.claimed_by == worker_id,
                )
                .values(
                    status=JobStatus.COMPLETED.value if ok else JobStatus.FAILED.value, finished_at=now,
                    extraction_id=extraction.id, error_code=None if ok else analysis.error_code,
                    lease_expires_at=None, claimed_by=None,
                ),
                execution_options={"synchronize_session": False},
            )
            if taken.rowcount != 1:
                s.rollback()
                return False
            self._audit_finished(s, job_id, fdr_id, document_id, requested_by, ok, analysis)
            s.commit()
            return True

    @staticmethod
    def _audit_finished(s: Session, job_id, fdr_id, document_id, requested_by, ok: bool, analysis: Analysis | None,
                        reason: str | None = None) -> None:
        user = s.get(User, requested_by)
        details: dict = {"target_fdr_id": str(fdr_id), "target_document_id": str(document_id)}
        if ok:
            details["result_count"] = sum(1 for f in analysis.result["fields"] if f["status"] == "EXTRACTED")
        else:
            details["reason"] = reason or analysis.error_code
        write_audit_event(
            s, action="document.extract_completed" if ok else "document.extract_failed",
            outcome=AuditOutcome.SUCCESS if ok else AuditOutcome.FAILURE,
            actor_user_id=requested_by, actor_role=user.role if user else None,
            entity_type="extraction_job", entity_id=job_id, details=details,
        )

    # ---- giving a job back / recovering ------------------------------------------------------
    def _release(self, job_id: uuid.UUID, worker_id: str) -> bool:
        """Put a job this worker holds back to PENDING without using up an attempt (shutdown, busy)."""
        with self.session_factory() as s:
            released = s.execute(
                update(FDRExtractionJob)
                .where(
                    FDRExtractionJob.id == job_id, FDRExtractionJob.status == JobStatus.RUNNING.value,
                    FDRExtractionJob.claimed_by == worker_id,
                )
                .values(
                    status=JobStatus.PENDING.value, claimed_by=None, lease_expires_at=None, started_at=None,
                    attempts=case((FDRExtractionJob.attempts > 0, FDRExtractionJob.attempts - 1), else_=0),
                ),
                execution_options={"synchronize_session": False},
            )
            s.commit()
            return released.rowcount == 1

    def recover_interrupted(self) -> int:
        """Handle RUNNING jobs whose lease expired (their worker died): back to PENDING, or FAILED as
        INTERRUPTED once `max_attempts` claims have been used. Returns how many were handled."""
        handled = 0
        with self.session_factory() as s:
            now = utcnow()
            stale = s.execute(
                select(
                    FDRExtractionJob.id, FDRExtractionJob.attempts, FDRExtractionJob.fdr_id,
                    FDRExtractionJob.document_id, FDRExtractionJob.requested_by,
                ).where(FDRExtractionJob.status == JobStatus.RUNNING.value, FDRExtractionJob.lease_expires_at < now)
            ).all()
            for job_id, attempts, fdr_id, document_id, requested_by in stale:
                still_stale = (
                    FDRExtractionJob.id == job_id, FDRExtractionJob.status == JobStatus.RUNNING.value,
                    FDRExtractionJob.lease_expires_at < now,
                )
                if attempts >= self.config.max_attempts:
                    changed = s.execute(
                        update(FDRExtractionJob).where(*still_stale).values(
                            status=JobStatus.FAILED.value, error_code=ErrorCode.INTERRUPTED.value, finished_at=now,
                            claimed_by=None, lease_expires_at=None,
                        ),
                        execution_options={"synchronize_session": False},
                    )
                    if changed.rowcount == 1:
                        self._audit_finished(s, job_id, fdr_id, document_id, requested_by, False, None,
                                             reason=ErrorCode.INTERRUPTED.value)
                else:
                    changed = s.execute(
                        update(FDRExtractionJob).where(*still_stale).values(
                            status=JobStatus.PENDING.value, claimed_by=None, lease_expires_at=None, started_at=None,
                        ),
                        execution_options={"synchronize_session": False},
                    )
                handled += changed.rowcount
            s.commit()
        return handled

    # ---- threads -----------------------------------------------------------------------------
    def wake(self) -> None:
        """Tell idle workers there is work (a request just enqueued a job)."""
        self._wake.set()

    def start(self) -> None:
        if self._threads or self.config.workers <= 0:
            return
        self._stop.clear()
        try:
            self.recover_interrupted()
        except Exception:
            log.warning("could not check for interrupted extraction jobs at start-up (details suppressed)")
        for n in range(self.config.workers):
            worker_id = f"{self.id}-{n}"
            thread = threading.Thread(target=self._loop, args=(worker_id,), name=f"extraction-{n}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def _loop(self, worker_id: str) -> None:
        last_sweep = 0.0
        while not self._stop.is_set():
            worked = False
            try:
                if time.monotonic() - last_sweep > SWEEP_SECONDS:
                    last_sweep = time.monotonic()
                    self.recover_interrupted()
                worked = self.process_one(worker_id)
            except Exception:
                log.warning("extraction worker could not reach the queue (details suppressed)")
            if not worked and not self._stop.is_set():
                self._wake.wait(self.config.poll_seconds)
                self._wake.clear()

    def stop(self, timeout: float = 10.0) -> list[uuid.UUID]:
        """Stop taking jobs, wait up to `timeout` for running ones, then give back any still running so
        the next start picks them up (they do not use up an attempt). Returns the jobs given back."""
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            leftovers = dict(self._inflight)
        released = [job_id for worker_id, job_id in leftovers.items() if self._release(job_id, worker_id)]
        self._threads = []
        return released
