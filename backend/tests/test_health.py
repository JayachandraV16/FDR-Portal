from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import get_db
from app.main import app


def test_health_reports_ok_without_database():
    with TestClient(app) as client:
        r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "fdr-portal-api"
    assert body["environment"] == "test"


def test_ready_returns_503_when_database_unavailable():
    class BrokenSession:
        def execute(self, *_a, **_k):
            raise OperationalError("SELECT 1", {}, Exception("down"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        r = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json()["detail"] == "Database unavailable"


def test_ready_ok_when_database_responds():
    class OkSession:
        def execute(self, *_a, **_k):
            return None

    app.dependency_overrides[get_db] = lambda: OkSession()
    try:
        r = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "database": "ok"}
