"""Development-only bootstrap of the very first administrator account.

Run explicitly from backend/ (there is deliberately no HTTP endpoint for this):

    python -m app.bootstrap_admin --email you@example.invalid --confirm-database fdr_portal

Guard rails, all checked before anything is written:
  * APP_ENV must be explicitly set to "development" (the unset default does not count).
  * DATABASE_URL must be PostgreSQL on a local host, port 5433, database "fdr_portal" exactly.
    Any other database (including fdr_portal_test or another project's) is refused.
  * The live connection must report current_database() == "fdr_portal".
  * The caller must repeat the database name via --confirm-database.
  * Refused if any ADMIN account already exists (active or not); a transaction-scoped advisory
    lock makes two simultaneous runs queue up so only the first can create one.

The temporary password is generated and hashed by the existing helpers, the account is created
with must_change_password=true, and the plaintext is printed once to standard output. It is never
logged, audited, written to a file or stored anywhere but as an Argon2id hash.
"""

import argparse
import hashlib
import sys
import uuid
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditOutcome, write_audit_event
from app.config import Settings, get_settings
from app.models import Role, User
from app.security.emails import InvalidEmail, normalize_email
from app.security.passwords import (
    generate_temporary_password,
    hash_password,
    validate_password_policy,
)

DEV_DATABASE_NAME = "fdr_portal"
DEV_DATABASE_PORT = 5433  # the FDR Portal container; another project uses 5432
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
REQUIRED_TABLES = ("users", "audit_logs")
MAX_FULL_NAME_LENGTH = 200

# Constant 64-bit key for pg_advisory_xact_lock, distinct from the audit-chain lock.
BOOTSTRAP_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"fdr_portal.bootstrap_admin").digest()[:8], "big", signed=True
)


class BootstrapRefused(Exception):
    """A guard rail stopped the bootstrap. The message is safe to show (no secrets)."""


class AdminAlreadyExists(BootstrapRefused):
    pass


@dataclass(frozen=True)
class CreatedAdmin:
    user_id: uuid.UUID
    email: str
    temporary_password: str  # returned to the caller once; never stored or logged


def check_environment(settings: Settings) -> None:
    if "app_env" not in settings.model_fields_set:
        raise BootstrapRefused("APP_ENV is not set explicitly; refusing (set APP_ENV=development).")
    if settings.app_env != "development":
        raise BootstrapRefused(f"APP_ENV is {settings.app_env!r}; the bootstrap runs only in development.")


def check_target_url(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise BootstrapRefused("DATABASE_URL must be a PostgreSQL URL.")
    if (url.host or "") not in LOCAL_HOSTS:
        raise BootstrapRefused("DATABASE_URL must point at a local host.")
    if url.port != DEV_DATABASE_PORT:
        raise BootstrapRefused(f"DATABASE_URL must use port {DEV_DATABASE_PORT} (the FDR Portal database).")
    if url.database != DEV_DATABASE_NAME:
        raise BootstrapRefused(f"DATABASE_URL must target the database {DEV_DATABASE_NAME!r}.")


def verify_connected_database(engine: Engine, *, allow_non_postgres: bool = False) -> None:
    """Confirm the live connection is the development database and the schema is migrated."""
    with engine.connect() as conn:
        if conn.dialect.name == "postgresql":
            actual = conn.execute(text("SELECT current_database()")).scalar()
            if actual != DEV_DATABASE_NAME:
                raise BootstrapRefused(f"Connected to database {actual!r}, not {DEV_DATABASE_NAME!r}.")
        elif not allow_non_postgres:  # SQLite is accepted only by unit tests, never by the CLI
            raise BootstrapRefused("The connected database is not PostgreSQL.")
        existing = set(inspect(conn).get_table_names())
    missing = [t for t in REQUIRED_TABLES if t not in existing]
    if missing:
        raise BootstrapRefused(f"Tables missing ({', '.join(missing)}); run the Alembic migrations first.")


def create_first_admin(
    db: Session, *, email: str, full_name: str, request_id: str | None = None
) -> CreatedAdmin:
    """Create the first ADMIN and its audit event in one transaction, then commit.

    Raises AdminAlreadyExists if any ADMIN row exists. On PostgreSQL the advisory lock is taken
    before the check, so concurrent callers are serialised and exactly one can succeed.
    """
    try:
        normalized = normalize_email(email)
    except InvalidEmail:
        raise BootstrapRefused("Invalid email address.") from None
    name = full_name.strip()
    if not name or len(name) > MAX_FULL_NAME_LENGTH:
        raise BootstrapRefused(f"Full name must be 1-{MAX_FULL_NAME_LENGTH} characters.")

    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BOOTSTRAP_LOCK_KEY})
    if db.scalar(select(User.id).where(User.role == Role.ADMIN.value).limit(1)) is not None:
        db.rollback()
        raise AdminAlreadyExists("An administrator already exists; refusing to create another.")
    if db.scalar(select(User.id).where(User.email == normalized)) is not None:
        db.rollback()
        raise BootstrapRefused("A user with this email already exists.")

    temporary_password = generate_temporary_password()
    validate_password_policy(temporary_password, normalized)  # reuse the existing policy check
    user = User(
        email=normalized,
        full_name=name,
        password_hash=hash_password(temporary_password),
        role=Role.ADMIN.value,
        institution_id=None,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    try:
        db.flush()
        # No authenticated actor exists yet, so none is recorded; details stay on the allow-list.
        write_audit_event(
            db,
            action="user.bootstrap_admin",
            outcome=AuditOutcome.SUCCESS,
            entity_type="user",
            entity_id=user.id,
            request_id=request_id or uuid.uuid4().hex,
            details={"new_role": user.role, "new_is_active": True, "reason": "development_bootstrap"},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BootstrapRefused("Could not create the administrator (conflicting data).") from None
    return CreatedAdmin(user_id=user.id, email=normalized, temporary_password=temporary_password)


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    allow_non_postgres: bool = False,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    parser = argparse.ArgumentParser(prog="python -m app.bootstrap_admin", description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True, help="login email of the new administrator")
    parser.add_argument("--full-name", default="Development Administrator")
    parser.add_argument(
        "--confirm-database", required=True, metavar="NAME",
        help=f"must equal {DEV_DATABASE_NAME!r}; guards against running against the wrong target",
    )
    args = parser.parse_args(argv)

    try:
        settings = settings or get_settings()
        check_environment(settings)
        check_target_url(settings.database_url)
        if args.confirm_database != DEV_DATABASE_NAME:
            raise BootstrapRefused(f"--confirm-database must be {DEV_DATABASE_NAME!r}.")
        if engine is None:
            from app.db import get_engine

            engine = get_engine()
        verify_connected_database(engine, allow_non_postgres=allow_non_postgres)
        with Session(engine) as session:
            created = create_first_admin(session, email=args.email, full_name=args.full_name)
    except BootstrapRefused as exc:
        print(f"Refused: {exc}", file=err)
        return 2

    print(f"Administrator created: {created.email} (id {created.user_id})", file=out)
    print(f"Temporary password (shown once, not stored): {created.temporary_password}", file=out)
    print("Sign in, then change the password when prompted. It cannot be shown again.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(run())
