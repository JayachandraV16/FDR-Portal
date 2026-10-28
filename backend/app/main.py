import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import account, chat_feedback, fdr_import, report_pdfs, alerts, auth, banks, calendar, chat, documents, extractions, fdrs, health, institutions, release, reports, review, users, workload
from app.api.deps import NotAuthenticated
from app.config import get_settings
from app.security.cookies import clear_session_cookie


log = logging.getLogger("fdr.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the extraction workers with the API and stop them cleanly when it shuts down. Jobs are
    rows in the database, so a job that is running at shutdown is given back to the queue (without
    using an attempt) and is picked up again at the next start. EXTRACTION_WORKERS=0 starts none."""
    runner = None
    settings = get_settings()
    if settings.chat_provider_problem:  # fixed text, no configured values: FDR Copilot stays on built-in answers
        log.warning("AI provider disabled: %s", settings.chat_provider_problem)
    if settings.extraction_workers > 0:
        try:
            from app.db import get_session_factory
            from app.extraction.jobs import ExtractionRunner

            runner = ExtractionRunner.from_settings(settings, get_session_factory())
            runner.start()
            app.state.extraction_runner = runner
        except Exception:  # the API must still come up; queued jobs simply wait
            log.warning("extraction workers were not started (details suppressed)")
            runner = None
    try:
        yield
    finally:
        if runner is not None:
            runner.stop(timeout=settings.extraction_timeout_seconds + 5)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        lifespan=lifespan,
        title="FDR & Security Deposit Portal API",
        version=__version__,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(_request: Request, exc: NotAuthenticated) -> JSONResponse:
        # Generic on purpose: expired, revoked, unknown and malformed cookies are indistinguishable.
        response = JSONResponse({"detail": "Not authenticated"}, status_code=401)
        response.headers["Cache-Control"] = "no-store"
        clear_session_cookie(response, exc.settings)
        return response

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(institutions.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(account.router, prefix="/api/v1")
    app.include_router(banks.router, prefix="/api/v1")
    app.include_router(fdr_import.router, prefix="/api/v1")  # before fdrs: "/fdrs/import/..." must not be read as an FDR id
    app.include_router(fdrs.router, prefix="/api/v1")
    app.include_router(review.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(extractions.router, prefix="/api/v1")
    app.include_router(alerts.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")
    app.include_router(report_pdfs.router, prefix="/api/v1")
    app.include_router(release.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(chat_feedback.router, prefix="/api/v1")
    app.include_router(calendar.router, prefix="/api/v1")
    app.include_router(workload.router, prefix="/api/v1")
    return app


app = create_app()
