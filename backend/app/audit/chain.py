"""Audit event writing and hash-chain verification.

Each event's hash covers its own content plus the previous event's hash, so editing, removing or
reordering a row in the middle of the chain is detectable by `verify_chain`.

What this does NOT give you (by design, do not overstate it):
  * It is tamper-EVIDENCE, not immutability. Someone who can rewrite the table (a database owner
    or superuser) can disable the triggers and recompute every hash from the edit onwards.
  * Removing rows from the END of the chain is not detectable from inside the table; that needs the
    latest hash anchored somewhere the database owner cannot reach.
  * Gaps in `seq` are normal (rolled-back transactions consume sequence values) and are not flagged.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit.details import sanitize_details
from app.audit.outcomes import AuditOutcome
from app.models import AuditLog
from app.timeutil import as_utc, utcnow

HASH_VERSION = "fdr-audit-v1"
# Constant 64-bit key for pg_advisory_xact_lock; serialises writers so the chain cannot fork.
AUDIT_LOCK_KEY = int.from_bytes(hashlib.sha256(b"fdr_portal.audit_chain").digest()[:8], "big", signed=True)


def canonical_event_json(
    *,
    occurred_at: datetime,
    actor_user_id: uuid.UUID | str | None,
    actor_role: str | None,
    action: str,
    entity_type: str | None,
    entity_id: str | None,
    outcome: str,
    ip_address: str | None,
    request_id: str | None,
    details: dict[str, Any] | None,
    prev_hash: str | None,
) -> str:
    """Deterministic serialisation: sorted keys, no whitespace, ASCII, UTC microsecond timestamp."""
    payload = {
        "v": HASH_VERSION,
        "occurred_at": as_utc(occurred_at).isoformat(timespec="microseconds"),
        "actor_user_id": None if actor_user_id is None else str(actor_user_id),
        "actor_role": actor_role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "outcome": str(outcome),
        "ip_address": ip_address,
        "request_id": request_id,
        "details": details,
        "prev_hash": prev_hash,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_event_hash(**fields: Any) -> str:
    return hashlib.sha256(canonical_event_json(**fields).encode("ascii")).hexdigest()


def _fit(name: str, value: str | None, limit: int) -> str | None:
    if value is not None and len(value) > limit:
        raise ValueError(f"Audit field '{name}' exceeds {limit} characters")
    return value


def write_audit_event(
    db: Session,
    *,
    action: str,
    outcome: AuditOutcome | str,
    actor_user_id: uuid.UUID | str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> AuditLog:
    """Append one event inside the caller's transaction and flush it (the caller commits).

    On PostgreSQL a transaction-scoped advisory lock is taken first, so concurrent writers queue up
    and every event links to a distinct predecessor. The lock lasts until the caller's transaction
    ends, so keep audit-writing transactions short. Details pass through the allow-list scrubber.
    """
    if not action:
        raise ValueError("Audit action is required")
    outcome = AuditOutcome(outcome)
    clean_details = sanitize_details(details)

    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUDIT_LOCK_KEY})

    prev_hash = db.scalar(select(AuditLog.hash).order_by(AuditLog.seq.desc()).limit(1))

    fields: dict[str, Any] = {
        "occurred_at": as_utc(now or utcnow()),
        "actor_user_id": None if actor_user_id is None else uuid.UUID(str(actor_user_id)),
        "actor_role": _fit("actor_role", actor_role, 30),
        "action": _fit("action", action, 64),
        "entity_type": _fit("entity_type", entity_type, 64),
        "entity_id": _fit("entity_id", None if entity_id is None else str(entity_id), 64),
        "outcome": outcome.value,
        # Client-controlled header-derived values are truncated, not rejected.
        "ip_address": ip_address[:45] if ip_address else None,
        "request_id": request_id[:64] if request_id else None,
        "details": clean_details,
        "prev_hash": prev_hash,
    }
    event = AuditLog(**fields, hash=compute_event_hash(**fields))
    db.add(event)
    db.flush()
    return event


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str | None = None  # "broken_link" or "hash_mismatch"


def verify_chain(db: Session, *, batch_size: int = 1000) -> ChainVerification:
    """Recompute every hash from the first event and check each link. Read-only."""
    expected_prev: str | None = None
    last_seq = 0
    checked = 0
    while True:
        rows = db.scalars(
            select(AuditLog).where(AuditLog.seq > last_seq).order_by(AuditLog.seq).limit(batch_size)
        ).all()
        if not rows:
            return ChainVerification(ok=True, checked=checked)
        for row in rows:
            if row.prev_hash != expected_prev:
                return ChainVerification(False, checked, row.seq, "broken_link")
            recomputed = compute_event_hash(
                occurred_at=row.occurred_at,
                actor_user_id=row.actor_user_id,
                actor_role=row.actor_role,
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                outcome=row.outcome,
                ip_address=row.ip_address,
                request_id=row.request_id,
                details=row.details,
                prev_hash=row.prev_hash,
            )
            if recomputed != row.hash:
                return ChainVerification(False, checked, row.seq, "hash_mismatch")
            expected_prev = row.hash
            last_seq = row.seq
            checked += 1
