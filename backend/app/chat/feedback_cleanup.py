"""Delete Copilot feedback older than the retention period (90 days by default).

Run it from the backend folder, for example once a week from a scheduler:

    python -m app.chat.feedback_cleanup              # dry run: reports how many rows would be deleted, deletes nothing
    python -m app.chat.feedback_cleanup --apply      # actually deletes them
    python -m app.chat.feedback_cleanup --days 60 --apply

Safe by construction:
  * It is NEVER started by the API or at start-up; someone has to run it.
  * It does nothing unless --apply is given; the default is a dry run that prints a count.
  * It only ever touches `chat_feedback`, and only rows whose age is beyond the cutoff.
  * It deletes in small batches, so a large backlog never holds a long lock.
  * It prints counts only (never any row content) and records one audit event with the number deleted.
  * If the table does not exist yet (migration 0007 not applied) it says so and changes nothing.
"""

import argparse
import sys
from datetime import timedelta

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import Session

from app.audit import AuditOutcome, write_audit_event
from app.models import ChatFeedback
from app.timeutil import utcnow

DEFAULT_DAYS = 90
BATCH = 1000


def expired_count(db: Session, days: int) -> int:
    cutoff = utcnow() - timedelta(days=days)
    return db.scalar(select(func.count()).select_from(ChatFeedback).where(ChatFeedback.created_at < cutoff)) or 0


def purge(db: Session, days: int = DEFAULT_DAYS, *, apply: bool = False, batch: int = BATCH) -> int:
    """Rows older than `days`: counted (dry run) or deleted in batches (apply). Returns that number."""
    if days < 1:
        raise ValueError("days must be at least 1")
    if not inspect(db.get_bind()).has_table(ChatFeedback.__tablename__):
        return 0
    if not apply:
        return expired_count(db, days)
    cutoff = utcnow() - timedelta(days=days)
    deleted = 0
    while True:
        ids = db.scalars(select(ChatFeedback.id).where(ChatFeedback.created_at < cutoff).limit(batch)).all()
        if not ids:
            break
        db.execute(delete(ChatFeedback).where(ChatFeedback.id.in_(ids)))
        db.commit()
        deleted += len(ids)
    if deleted:
        write_audit_event(
            db, action="chat.feedback_purge", outcome=AuditOutcome.SUCCESS, actor_user_id=None, actor_role=None, entity_type="chat_feedback",
            entity_id=None, ip_address=None, request_id=None, details={"result_count": deleted, "filters": [f"older_than_{days}_days"]},
        )
        db.commit()
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete Copilot feedback older than the retention period.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"keep this many days (default {DEFAULT_DAYS})")
    parser.add_argument("--apply", action="store_true", help="really delete; without it nothing is changed")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")
    from app.db import get_session_factory

    with get_session_factory()() as db:
        if not inspect(db.get_bind()).has_table(ChatFeedback.__tablename__):
            print("The chat_feedback table does not exist in this database (migration 0007 has not been applied). Nothing to do.")
            return 0
        count = purge(db, args.days, apply=args.apply)
    print(f"{'Deleted' if args.apply else 'Would delete'} {count} feedback row(s) older than {args.days} days." + ("" if args.apply else " (dry run: nothing was changed)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
