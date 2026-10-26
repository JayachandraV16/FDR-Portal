"""Helpers for API tests: build a TestClient wired to an injected test engine and settings."""

import os
import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models import AuditLog, Institution, User
from app.security.passwords import hash_password

ORIGIN = "http://localhost:5173"
API = "/api/v1"
PW = "correct horse battery staple"
NEW_PW = "another long passphrase 42"


def make_settings(**overrides) -> Settings:
    values = dict(
        database_url="postgresql://unused:unused@127.0.0.1:1/unused",  # never connected to
        secret_key=os.environ["SECRET_KEY"],
        app_env="test",
        cookie_secure=False,  # permitted only outside production; the test client speaks http
        cors_origins=[ORIGIN],
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def create_user(
    engine,
    *,
    email="admin@example.invalid",
    password=PW,
    role="ADMIN",
    is_active=True,
    institution_id=None,
    password_hash=None,
    must_change_password=False,
) -> uuid.UUID:
    with Session(engine) as s:
        user = User(
            email=email,
            full_name="Test User (Synthetic)",
            password_hash=password_hash or hash_password(password),
            role=role,
            is_active=is_active,
            institution_id=institution_id,
            must_change_password=must_change_password,
        )
        s.add(user)
        s.commit()
        return user.id


def create_institution(engine, code="TST-001", name="Test Institution (Synthetic)") -> uuid.UUID:
    with Session(engine) as s:
        inst = Institution(code=code, name=name, state="Karnataka", institution_type="Engineering",
                           contact_email=f"{code.lower()}@example.invalid")
        s.add(inst)
        s.commit()
        return inst.id


@dataclass
class Actor:
    """A logged-in user: their client plus the CSRF headers to use on unsafe requests."""

    client: TestClient
    headers: dict[str, str]
    user_id: uuid.UUID
    email: str

    def get(self, path, **kw):
        return self.client.get(f"{API}{path}", **kw)

    def post(self, path, json=None, **kw):
        return self.client.post(f"{API}{path}", json=json, headers=self.headers, **kw)

    def patch(self, path, json=None, **kw):
        return self.client.patch(f"{API}{path}", json=json, headers=self.headers, **kw)


def sign_in(engine, *, role="ADMIN", email=None, institution_id=None, must_change_password=False,
            is_active=True, settings=None, raise_server_exceptions=True) -> Actor:
    email = email or f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.invalid"
    user_id = create_user(engine, email=email, role=role, institution_id=institution_id,
                          must_change_password=must_change_password, is_active=is_active)
    client = build_client(engine, settings, raise_server_exceptions=raise_server_exceptions)
    if not is_active:  # cannot log in; caller only needs the account to exist
        return Actor(client, {}, user_id, email)
    response = login(client, email)
    assert response.status_code == 200, response.text
    return Actor(client, csrf_headers(response), user_id, email)


def build_client(engine, settings: Settings | None = None, *, origin: str | None = ORIGIN,
                 raise_server_exceptions: bool = True) -> TestClient:
    settings = settings or make_settings()

    def _db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    headers = {"Origin": origin} if origin else {}
    return TestClient(app, headers=headers, raise_server_exceptions=raise_server_exceptions)


def login(client: TestClient, email="admin@example.invalid", password=PW, **kw):
    return client.post(f"{API}/auth/login", json={"email": email, "password": password}, **kw)


def csrf_headers(login_response) -> dict[str, str]:
    return {"X-CSRF-Token": login_response.json()["csrf_token"]}


def audit_rows(engine) -> list[AuditLog]:
    with Session(engine) as s:
        return list(s.scalars(select(AuditLog).order_by(AuditLog.seq)))


def audit_events(engine, action: str, outcome: str | None = None) -> list[AuditLog]:
    return [r for r in audit_rows(engine) if r.action == action and (outcome is None or r.outcome == outcome)]
