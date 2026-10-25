# FDR & Security Deposit Portal

SIH 2026 · Problem Statement 26265 (AICTE) — a centralised platform for managing FDRs and security
deposits of educational institutions.

**Status: Checkpoint 0 (foundation) only.** The repository currently contains the project scaffold,
a health endpoint, a database foundation (`banks`, `institutions`), a synthetic-data seed, and an
application shell. FDR management, documents, extraction, verification, authentication, alerts,
and release workflows are **not implemented yet**.

Design principles: all data is synthetic; document extraction is never treated as bank
verification; a discrepancy only means "needs human review"; no bank integration exists.

## Stack

React · Vite · TypeScript · Tailwind CSS 4 (frontend) — FastAPI · SQLAlchemy 2 · Alembic · Pydantic
(backend) — PostgreSQL 16.

## Layout

```
backend/    FastAPI app, models, Alembic migrations, seed, tests
frontend/   React + Vite SPA
docker-compose.yml   local PostgreSQL only
```

## Prerequisites

- Python 3.11+ (developed on 3.14)
- Node.js 20+ and npm
- PostgreSQL 16 — either via Docker Desktop (`docker compose`, below) or a local install
- Tesseract OCR is **not needed yet** (first needed in Checkpoint 4)

## Setup

Commands are shown for Git Bash / macOS / Linux; on Windows PowerShell use
`.\.venv\Scripts\Activate.ps1` to activate the virtualenv and `Copy-Item` instead of `cp`.

### 1. Database

```bash
cp .env.example .env              # then edit POSTGRES_PASSWORD
docker compose up -d db           # Docker Desktop must be running
```

Without Docker, create an empty PostgreSQL database and user yourself and use their details in step 2.

### 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate     # Windows Git Bash; on Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # set DATABASE_URL to match the credentials from step 1
alembic upgrade head              # create tables
python -m app.seed                # load synthetic banks and institutions (idempotent)
uvicorn app.main:app --reload --port 8000
```

- Liveness: <http://localhost:8000/api/v1/health>
- Readiness (queries the DB, 503 if unavailable): <http://localhost:8000/api/v1/health/ready>
- Interactive API docs (non-production only): <http://localhost:8000/docs>

### 3. Frontend

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

The dev server proxies `/api` to `http://localhost:8000` (override with `VITE_API_PROXY_TARGET`
in `frontend/.env.local`).

## Tests and checks

```bash
cd backend && pytest              # no database server required
cd frontend && npm run build      # type-checks and builds
```

The backend tests cover the health endpoints, configuration parsing, and that the Alembic
migration matches the SQLAlchemy models and the seed is idempotent. The migration/seed tests run
on a temporary **SQLite** file as a stand-in, so they do **not** prove the migration works on
PostgreSQL. To smoke-test against PostgreSQL, run `alembic upgrade head` and
`python -m app.seed` (steps above) and then `GET /api/v1/health/ready`.

## Configuration

| Variable | Where | Purpose |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | root `.env` | credentials for the Docker database |
| `DATABASE_URL` | `backend/.env` | SQLAlchemy URL (required; no default) |
| `APP_ENV` | `backend/.env` | `development` / `test` / `production` (seeding is refused in production) |
| `CORS_ORIGINS` | `backend/.env` | JSON list of allowed browser origins |
| `STORAGE_DIR` | `backend/.env` | private document storage root (used from Checkpoint 3) |
| `LLM_ENABLED` | `backend/.env` | optional LLM-assisted extraction; `false` by default, not used yet |

`.env` files are git-ignored. Only the `.env.example` templates are meant to be committed.

## Roadmap

Checkpoints 1–9: auth/RBAC/audit → institutions & FDRs → documents → extraction & comparison →
human review → maturity alerts → renewal/exceptions → release/refund → reports & polish.
